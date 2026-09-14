"""Constrói a base de valor patrimonial de FIIs, do Informe Mensal da CVM.

    python atualizar_fundos_cvm.py                # ano corrente e o anterior
    python atualizar_fundos_cvm.py 2025 2026
    python atualizar_fundos_cvm.py --inspecionar  # lista as colunas do arquivo

Por que existe: o P/VP é o gatilho de entrada de FII no radar, e vinha do
`.info` do Yahoo — que falha para metade dos fundos e é bloqueado no Render.
Todo FII entrega Informe Mensal à CVM com o valor patrimonial da cota. É fonte
oficial, mensal, e não depende de IP.

Mesmo desenho da base de ações: este script roda FORA do serviço web e grava
`fundos_cvm.db`, que a API só lê.

COMO ELE SOBREVIVE A NÃO CONHECER O LAYOUT
------------------------------------------
A primeira versão exigia que UM CSV trouxesse CNPJ, competência e o valor
juntos — e o informe distribui esses campos entre arquivos diferentes do mesmo
zip, então tudo era descartado. Agora:

  1. cada CSV do zip contribui o que tiver, e as partes são casadas por
     (CNPJ, competência) no fim;
  2. o casamento de coluna é por padrão (regex sobre o nome normalizado), não
     por lista fechada de nomes;
  3. o valor patrimonial POR COTA é decidido por grandeza, não por nome: se
     dividir pelo número de cotas cai na faixa plausível de uma cota, a coluna
     era o total; se o próprio número já está na faixa, era o por-cota. Nome de
     coluna muda entre anos e entre normativos — ordem de grandeza não.

Se o informe trouxer código de negociação, ele também vira tabela e o cadastro
de tickers do B3 deixa de ser necessário.
"""

import io
import re
import sqlite3
import sys
import unicodedata
import zipfile

import pandas as pd
import requests

URL_INFORME = ("https://dados.cvm.gov.br/dados/FII/DOC/INF_MENSAL/DADOS/"
               "inf_mensal_fii_{ano}.zip")
BANCO = "fundos_cvm.db"
TIMEOUT = 180

# Padrão sobre o nome NORMALIZADO da coluna (minúsculo, sem acento, "_" e "-"
# viram espaço). Regex em vez de lista fechada porque a CVM renomeia colunas a
# cada revisão normativa — a Resolução 175 trocou "CNPJ_Fundo" por
# "CNPJ_Fundo_Classe", e a lista fechada quebrou inteira.
PADROES_ALVO = {
    "cnpj": [r"^cnpj([ _]?(do)?[ _]?fundo)?([ _]?classe)?$", r"^cnpj fundo"],
    "data_referencia": [r"^(data|dt)[ _]?(de[ _]?)?(referencia|competencia|comptc)$"],
    "cotas_emitidas": [r"(quantidade|numero|qtd|qtde).*cotas?[ _]?emitidas?",
                       r"^cotas?[ _]?emitidas?$"],
    # CONFIRMADO no informe de 2026: `Valor_Patrimonial_Cotas` é o valor POR
    # COTA, apesar do plural no nome — 258.202.136,67 de PL / 2.800.149 cotas
    # = 92,2101, que é exatamente o que a coluna traz. O plural engana.
    "vp_por_cota": [r"^valor[ _]?patrimonial[ _]?(das?[ _]?)?cotas?$",
                    r"valor[ _]?patrimonial[ _]?por[ _]?cota",
                    r"^vl[ _]?patrimonial[ _]?cota"],
    "patrimonio_liquido": [r"^patrimonio[ _]?liquido$", r"^vl[ _]?patrim[ _]?liq"],
    # O informe não publica código de negociação, mas publica ISIN — e o ISIN
    # brasileiro carrega a raiz do ticker: BRFVPQCTF015 -> FVPQ -> FVPQ11.
    "isin": [r"^codigo[ _]?isin$", r"^isin$"],
    "codigo_negociacao": [r"codigo[ _]?(de[ _]?)?negociacao", r"^ticker$",
                          r"^codigo[ _]?neg"],
    # O nome do fundo é a única coisa que torna o vínculo ticker->CNPJ
    # AUDITÁVEL. Sem ele, um ISIN mal deduzido aponta para o CNPJ de outro
    # fundo e ninguém percebe: o número aparece na tela como se fosse do papel
    # certo. Foi o que aconteceu com XPML11 -> 07583627000161, um fundo que
    # não é o XP Malls. Com o nome gravado, a divergência salta aos olhos.
    "nome": [r"^denominacao[ _]?social$", r"^nome[ _]?(do[ _]?)?fundo$",
             r"^razao[ _]?social$", r"^nm[ _]?fantasia$"],
}

# BR + raiz de 4 do ticker + CTF (cota de fundo) + dígitos.
ISIN_FII = re.compile(r"^BR([A-Z0-9]{4})CTF\d+$")


def ticker_do_isin(isin):
    """FVPQ11 a partir de BRFVPQCTF015. Devolve None se não for ISIN de cota."""
    casou = ISIN_FII.match(str(isin or "").strip().upper())
    return f"{casou.group(1)}11" if casou else None


# Um FII com VP por cota fora desta faixa é dado corrompido, não notícia.
VP_COTA_MINIMO = 0.01
VP_COTA_MAXIMO = 100_000.0

# Teto de plausibilidade PARA CADA LINHA, aplicado depois de decidida a leitura
# do arquivo inteiro (ver `decidir_leitura`/`aplicar_leitura`). Achado no caso
# real de XPML11 (competência 2026-07): a leitura "total/cotas" foi a correta
# para o arquivo (a maioria dos fundos bateu), mas UMA linha teve
# `cotas_emitidas` errado na fonte — provavelmente um valor de classe/subclasse
# da Resolução 175 casado com o patrimônio do fundo inteiro — e isso deu
# 22.548,02 de VP por cota. `VP_COTA_MAXIMO` (100.000) não pegava isso: está
# ordens de grandeza acima de qualquer cota de FII negociada na B3, mas ainda
# dentro do teto absoluto. `conferir()` também não pegava: VP × cotas bate com
# o patrimônio por CONSTRUÇÃO quando a leitura é "total/cotas" (um é derivado
# do outro), então a conferência é sempre trivialmente satisfeita nesse caminho
# — ela só protege a leitura "por-cota", onde VP vem de uma coluna e cotas de
# outra.
#
# Este teto é a defesa de verdade: nenhuma cota de FII do mercado brasileiro
# negocia perto disso, então uma linha que resulte acima daqui quase certamente
# tem `cotas_emitidas` ou `patrimonio_liquido` errado na fonte, mesmo que os
# dois batam entre si. "P/VP errado é pior que ausente" — a linha vira
# descarte (fundo fica sem P/VP no radar) em vez de virar um número fabricado.
VP_COTA_TETO_PLAUSIVEL = 10_000.0

# Onde vive a esmagadora maioria das cotas de FII do mercado brasileiro. Serve
# para escolher a leitura da coluna, não para descartar fundo: a decisão é
# tomada pela MEDIANA do arquivo inteiro, e depois aplicada linha a linha.
COTA_TIPICA_MINIMA = 1.0
COTA_TIPICA_MAXIMA = 5_000.0


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace("_", " ").replace("-", " ")
    return " ".join(texto.lower().split())


def _mapear_colunas(colunas):
    """{campo: nome_real_da_coluna}. Primeiro padrão que casar vence."""
    mapa = {}
    normalizadas = [(_normalizar(c), c) for c in colunas]
    for campo, padroes in PADROES_ALVO.items():
        for padrao in padroes:
            for texto, original in normalizadas:
                if re.search(padrao, texto):
                    mapa[campo] = original
                    break
            if campo in mapa:
                break
    return mapa


def baixar_zip(ano):
    url = URL_INFORME.format(ano=ano)
    print(f"[{ano}] baixando {url}")
    try:
        resposta = requests.get(url, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        print(f"[{ano}] falha de rede: {type(exc).__name__}: {exc}")
        return None
    if resposta.status_code != 200:
        print(f"[{ano}] CVM respondeu {resposta.status_code} — pulando")
        return None
    return zipfile.ZipFile(io.BytesIO(resposta.content))


def inspecionar(ano):
    """Lista os CSVs do zip e as colunas de cada um, com uma linha de exemplo."""
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return
    for nome in arquivo_zip.namelist():
        if not nome.lower().endswith(".csv"):
            continue
        print(f"\n===== {nome} =====")
        try:
            with arquivo_zip.open(nome) as fluxo:
                amostra = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                      dtype=str, nrows=2)
            for coluna in amostra.columns:
                exemplo = amostra[coluna].iloc[0] if len(amostra) else ""
                print(f"   {coluna:<44} | {str(exemplo)[:34]}")
            print(f"   -> campos reconhecidos: {_mapear_colunas(amostra.columns)}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {type(exc).__name__}: {exc}")


def _para_numero(valor):
    """A CVM alterna ponto e vírgula como separador decimal."""
    texto = str(valor or "").strip()
    if not texto or texto.upper() in ("NAN", "NONE"):
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None


def _so_digitos(valor):
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _fracao_plausivel(valores):
    if not valores:
        return 0.0
    dentro = sum(1 for v in valores if COTA_TIPICA_MINIMA <= v <= COTA_TIPICA_MAXIMA)
    return dentro / len(valores)


def decidir_leitura(pares):
    """A coluna de VP é mesmo por-cota, ou é um total disfarçado?

    Rede de segurança para quando a CVM trocar o significado da coluna sem
    trocar o nome. A primeira versão decidia linha a linha e isso estava
    errado: uma coluna tem um significado só, e no informe de 2026 isso
    produzia 1.164 fundos "por-cota" e 305 "total/cotas" no MESMO arquivo —
    estes últimos viravam cotas de R$ 0,02.

    As duas leituras são testadas contra o arquivo inteiro e vence a que põe
    mais fundos na faixa em que cota de FII realmente vive. Não há empate
    possível na prática: a leitura errada erra por ordens de grandeza.

    Devolve "por-cota", "total/cotas" ou None (sem sinal claro).
    """
    diretos = [v for v, _ in pares if v and v > 0]
    quocientes = [v / c for v, c in pares if v and v > 0 and c and c > 0]

    peso_direto = _fracao_plausivel(diretos)
    peso_quociente = _fracao_plausivel(quocientes)
    print(f"   coluna de VP: por-cota {peso_direto:.0%} plausível, "
          f"total/cotas {peso_quociente:.0%} plausível")

    if max(peso_direto, peso_quociente) < 0.5:
        return None
    return "por-cota" if peso_direto >= peso_quociente else "total/cotas"


def aplicar_leitura(valor, cotas, leitura):
    """VP por cota segundo a leitura já decidida para o arquivo."""
    if valor is None or valor <= 0:
        return None
    if leitura == "total/cotas":
        if not cotas or cotas <= 0:
            return None
        vp = valor / cotas
    else:
        vp = valor
    return vp if VP_COTA_MINIMO <= vp <= VP_COTA_MAXIMO else None


# Divergência acima disso entre o VP publicado e PL/cotas é dado inconsistente.
TOLERANCIA_CONFERENCIA = 0.05


def conferir(vp, patrimonio, cotas):
    """O informe publica VP por cota E patrimônio E número de cotas.

    Ter as três coisas permite conferir em vez de confiar: VP x cotas tem que
    bater com o PL. Quando não bate, o registro não entra — P/VP errado numa
    tela usada para falar com cliente é pior que P/VP ausente.

    Devolve True quando não há como conferir (falta um dos três).
    """
    if not (vp and patrimonio and cotas) or patrimonio <= 0:
        return True
    return abs(vp * cotas - patrimonio) / patrimonio <= TOLERANCIA_CONFERENCIA


def processar_ano(ano):
    """{cnpj: {...}} com a competência mais recente de cada fundo.

    Cada CSV do zip contribui as colunas que tiver; o casamento é por
    (CNPJ, competência). Um arquivo que traga só o número de cotas e outro que
    traga só o patrimônio se completam, em vez de os dois serem descartados.
    """
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return {}, {}

    partes = {}        # (cnpj, competencia) -> {cotas_emitidas, valor_patrimonial}
    tickers = {}       # codigo_negociacao -> cnpj
    vistos = set()

    for nome in arquivo_zip.namelist():
        if not nome.lower().endswith(".csv"):
            continue
        try:
            with arquivo_zip.open(nome) as fluxo:
                dados = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1", dtype=str)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {nome}: {type(exc).__name__}")
            continue

        mapa = _mapear_colunas(dados.columns)
        # Sem chave de casamento o arquivo não serve para nada.
        if "cnpj" not in mapa or "data_referencia" not in mapa:
            print(f"   - {nome}: sem CNPJ/competência, ignorado")
            continue

        uteis = [c for c in ("cotas_emitidas", "vp_por_cota", "patrimonio_liquido",
                             "isin", "codigo_negociacao", "nome") if c in mapa]
        if not uteis:
            print(f"   - {nome}: nada de útil além da chave, ignorado")
            continue
        print(f"   {nome}: {len(dados)} linhas, contribui {uteis}")
        vistos.update(uteis)

        colunas = [mapa["cnpj"], mapa["data_referencia"]] + [mapa[c] for c in uteis]
        recorte = dados[colunas]

        for linha in recorte.itertuples(index=False, name=None):
            cnpj = _so_digitos(linha[0])
            if len(cnpj) != 14:
                continue
            competencia = str(linha[1])[:10]
            if not competencia or competencia == "nan":
                continue

            registro = partes.setdefault((cnpj, competencia), {})
            for posicao, campo in enumerate(uteis, start=2):
                bruto = linha[posicao]
                if campo == "codigo_negociacao":
                    codigo = str(bruto or "").strip().upper()
                    if re.fullmatch(r"[A-Z0-9]{4}\d{1,2}", codigo):
                        tickers[codigo] = cnpj
                    continue
                if campo == "isin":
                    # Só entra se o código de negociação não veio; o publicado
                    # vale mais que o derivado.
                    codigo = ticker_do_isin(bruto)
                    if codigo:
                        tickers.setdefault(codigo, cnpj)
                    continue
                if campo == "nome":
                    texto = str(bruto or "").strip()
                    if texto and texto.upper() not in ("NAN", "NONE"):
                        registro.setdefault("nome", texto)
                    continue
                numero = _para_numero(bruto)
                if numero is not None:
                    registro[campo] = numero

    if not vistos:
        return {}, tickers

    # Uma decisão só para o arquivo inteiro, antes de converter linha alguma.
    pares = [(r.get("vp_por_cota"), r.get("cotas_emitidas")) for r in partes.values()]
    leitura_global = decidir_leitura(pares)
    if leitura_global is None:
        print("   ! coluna de VP sem leitura dominante — usando PL/cotas")

    coletado = {}
    descartes = 0
    inconsistentes = 0
    for (cnpj, competencia), registro in partes.items():
        cotas = registro.get("cotas_emitidas")
        patrimonio = registro.get("patrimonio_liquido")
        publicado = registro.get("vp_por_cota")

        vp = aplicar_leitura(publicado, cotas, leitura_global) if leitura_global else None
        origem = leitura_global
        if vp is None and patrimonio and cotas and cotas > 0:
            candidato = patrimonio / cotas
            if VP_COTA_MINIMO <= candidato <= VP_COTA_MAXIMO:
                vp, origem = candidato, "pl/cotas"

        if vp is None:
            descartes += 1
            continue
        if vp > VP_COTA_TETO_PLAUSIVEL:
            # Acima do teto de plausibilidade: mesmo que VP x cotas bata com o
            # patrimônio (o que sempre bate quando a leitura é "total/cotas",
            # por construção), nenhuma cota de FII real chega perto disso — o
            # dado de origem (cotas ou patrimônio) está errado. Ver comentário
            # de VP_COTA_TETO_PLAUSIVEL.
            inconsistentes += 1
            continue
        if origem != "pl/cotas" and not conferir(vp, patrimonio, cotas):
            inconsistentes += 1
            continue

        atual = coletado.get(cnpj)
        if atual and atual["competencia"] >= competencia:
            continue
        coletado[cnpj] = {
            "competencia": competencia,
            "vp_por_cota": vp,
            "patrimonio_liquido": patrimonio,
            "cotas_emitidas": cotas,
            "nome": registro.get("nome"),
            "leitura": origem,
        }

    print(f"[{ano}] {len(coletado)} fundos, {descartes} competências sem VP, "
          f"{inconsistentes} descartadas por VP x cotas não bater com o PL")
    return coletado, tickers


def gravar(registros, tickers=None, banco=BANCO):
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    # A base é reconstruída inteira a cada execução. Sem o DROP, um fundo que
    # saiu do informe (ou uma linha gravada por uma versão anterior com leitura
    # errada da coluna) ficava para trás e continuava alimentando o P/VP.
    cursor.execute("DROP TABLE IF EXISTS fundos")
    cursor.execute("DROP TABLE IF EXISTS tickers")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS fundos (
            cnpj TEXT PRIMARY KEY,
            competencia TEXT,
            vp_por_cota REAL,
            patrimonio_liquido REAL,
            cotas_emitidas REAL,
            nome TEXT
        )
    """)
    # Só existe quando o informe traz código de negociação. Quando existe,
    # dispensa o cadastro de fundos do B3.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tickers (
            ticker TEXT PRIMARY KEY,
            cnpj TEXT
        )
    """)
    linhas = [(cnpj, v["competencia"], v["vp_por_cota"],
               v["patrimonio_liquido"], v["cotas_emitidas"], v.get("nome"))
              for cnpj, v in registros.items()]
    cursor.executemany(
        "INSERT OR REPLACE INTO fundos "
        "(cnpj, competencia, vp_por_cota, patrimonio_liquido, cotas_emitidas, nome) "
        "VALUES (?, ?, ?, ?, ?, ?)", linhas)

    if tickers:
        cursor.executemany("INSERT OR REPLACE INTO tickers (ticker, cnpj) VALUES (?, ?)",
                           sorted(tickers.items()))
        print(f"{len(tickers)} códigos de negociação gravados")

    conexao.commit()

    print(f"\n{len(linhas)} fundos gravados em {banco}")
    competencias = cursor.execute(
        "SELECT competencia, COUNT(*) FROM fundos GROUP BY competencia "
        "ORDER BY competencia DESC LIMIT 5").fetchall()
    print("competências mais recentes:", competencias)
    leituras = {}
    for valores in registros.values():
        leituras[valores["leitura"]] = leituras.get(valores["leitura"], 0) + 1
    print("leitura da coluna de valor:", leituras)
    print("\namostra:")
    for cnpj, comp, vp, nome in cursor.execute(
            "SELECT cnpj, competencia, vp_por_cota, nome FROM fundos "
            "ORDER BY vp_por_cota DESC LIMIT 6"):
        print(f"   {cnpj}  {comp}  VP/cota R$ {vp:,.2f}  {(nome or '')[:40]}")

    conferir_tickers(cursor)
    conexao.close()


# Os FIIs que o radar realmente usa. A conferência abaixo existe porque o
# vínculo ticker->CNPJ é DEDUZIDO do ISIN (o informe não publica código de
# negociação), e dedução erra silenciosamente: o P/VP de outro fundo aparece
# na tela como se fosse deste papel. Com o nome do fundo ao lado, um humano
# confere em cinco segundos o que nenhuma heurística garante.
TICKERS_DO_RADAR = [
    "HGLG11", "BTLG11", "XPML11", "VISC11", "ALZR11",
    "KNRI11", "VILG11", "MALL11", "PVBI11", "BRCR11",
    "KNIP11", "KNCR11", "IRDM11", "CPTS11", "MXRF11",
    "HGCR11", "MCCI11", "RECR11", "CVBI11", "VRTA11",
]


def conferir_tickers(cursor):
    """Imprime ticker -> CNPJ -> nome do fundo, para conferência humana."""
    print("\nconferência do vínculo ticker -> fundo (o nome tem que bater "
          "com o papel):")
    sem_vinculo, sem_informe = [], []
    for ticker in TICKERS_DO_RADAR:
        linha = cursor.execute(
            "SELECT t.cnpj, f.nome, f.vp_por_cota, f.competencia "
            "FROM tickers t LEFT JOIN fundos f ON f.cnpj = t.cnpj "
            "WHERE t.ticker = ?", (ticker,)).fetchone()
        if not linha:
            sem_vinculo.append(ticker)
            continue
        cnpj, nome, vp, comp = linha
        if vp is None:
            sem_informe.append(ticker)
            print(f"   {ticker:<8} {cnpj}  (sem informe válido para este CNPJ)")
            continue
        print(f"   {ticker:<8} {cnpj}  VP/cota R$ {vp:>10,.2f}  {comp}  {(nome or '?')[:42]}")

    if sem_vinculo:
        print(f"   sem vínculo ticker->CNPJ: {', '.join(sem_vinculo)}")
    if sem_informe:
        print(f"   com vínculo mas sem VP: {', '.join(sem_informe)}")
    print("   Nome que não bate com o papel = ISIN mal deduzido. Corrija em "
          "cadastro_fii_manual.json: {\"XPML\": \"<cnpj certo>\"}")


def main(anos):
    registros = {}
    tickers = {}
    for ano in anos:
        parcial, codigos = processar_ano(ano)
        tickers.update(codigos)
        for cnpj, valores in parcial.items():
            atual = registros.get(cnpj)
            if not atual or valores["competencia"] > atual["competencia"]:
                registros[cnpj] = valores
    if not registros:
        raise SystemExit(
            "Nenhum dado obtido. Rode:  python atualizar_fundos_cvm.py --inspecionar\n"
            "e mande a saida — o layout do informe mudou além do que os padrões cobrem.")
    gravar(registros, tickers)


if __name__ == "__main__":
    import datetime

    atual = datetime.date.today().year
    restante = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--inspecionar" in sys.argv:
        inspecionar(int(restante[0]) if restante else atual)
        raise SystemExit(0)

    anos_alvo = [int(a) for a in restante] or [atual - 1, atual]
    print(f"Anos: {anos_alvo}")
    main(anos_alvo)
