"""Constrói a base de fundamentos a partir da DFP da CVM.

    python atualizar_fundamentos_cvm.py            # últimos 3 exercícios
    python atualizar_fundamentos_cvm.py 2024 2025

Por que existe: o Yahoo recusa o endpoint de múltiplos (`quoteSummary`) quando
a chamada vem de IP de datacenter — no Render, 0 de 100 papéis retornaram
fundamento. A CVM é fonte oficial, gratuita e não bloqueia servidor. E o número
passa a vir de balanço auditado em vez de um blob agregado.

Este script é para rodar FORA do serviço web (na sua máquina, ou como job
agendado). Ele grava `fundamentos_cvm.db`, que é lido pela API. A DFP é anual:
regenerar uma vez por trimestre é mais que suficiente.

Duas coisas que a versão anterior do coletor errava e aqui são tratadas:

* ESCALA_MOEDA. A CVM publica valores em MIL ou UNIDADE conforme a empresa.
  Ignorar isso mistura companhias em escalas diferentes na mesma tabela — e
  quebra qualquer conta que cruze com preço, como o P/L.
* "Passivo total". A conta 2 da CVM é o total do passivo E do patrimônio (bate
  com o ativo total). O passivo de terceiros, que é o que entra no Altman, é
  2.01 + 2.02.
"""

import io
import sqlite3
import sys
import zipfile
from collections import defaultdict

import pandas as pd
import requests

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
BANCO = "fundamentos_cvm.db"
TIMEOUT = 180
TAMANHO_BLOCO = 200_000  # linhas por chunk: a DFP passa de 1 milhão

COLUNAS = ["CNPJ_CIA", "DENOM_CIA", "DT_FIM_EXERC", "ORDEM_EXERC",
           "ESCALA_MOEDA", "CD_CONTA", "DS_CONTA", "VL_CONTA"]

# Conta da CVM -> campo nosso. Só o que o motor de score consome.
CONTAS = {
    "BPA": {
        "1": "ativo_total",
        "1.01": "ativo_circulante",
        "1.01.01": "caixa",
    },
    "BPP": {
        "2.01": "passivo_circulante",
        "2.02": "passivo_nao_circulante",
        "2.03": "patrimonio_liquido",
        # Dívida onerosa. Mapeada SÓ por código, de propósito: a descrição
        # "Empréstimos e Financiamentos" aparece nas duas contas, e registrar
        # a descrição faria as duas caírem no mesmo campo — a de curto prazo
        # sobrescreveria a de longo, ou o contrário, conforme a ordem do
        # arquivo. Por código não há ambiguidade.
        "2.01.04": "divida_curto_prazo",
        "2.02.01": "divida_longo_prazo",
        "2.03.05": "lucros_acumulados",
    },
    "DRE": {
        "3.01": "receita_liquida",
        "3.05": "ebit",
        "3.06": "resultado_financeiro",
        "3.06.02": "despesa_financeira",
        "3.11": "lucro_liquido",
        "3.99.01.01": "lpa_on",
    },
}

# Empresa que não publica 3.11 costuma trazer o lucro em 3.13 (atribuído aos
# sócios da controladora). Serve como segunda opção, nunca sobrescreve 3.11.
CONTAS_ALTERNATIVAS = {"3.13": "lucro_liquido"}

ESCALAS = {"MIL": 1_000.0, "MILHAR": 1_000.0, "UNIDADE": 1.0, "UNIT": 1.0}

# Casar pela DESCRIÇÃO da conta, não só pelo código. Banco usa plano de contas
# diferente: a conta 2.03 de uma indústria é patrimônio líquido, e na DFP de um
# banco significa outra coisa — foi assim que o Itaú apareceu com patrimônio de
# R$ 2,3 trilhões, que é o ativo dele. A descrição a CVM padroniza.
# Comparação é por igualdade exata do texto normalizado (sem acento, minúsculo).
DESCRICOES = {
    "ativo_total": ("ativo total",),
    "ativo_circulante": ("ativo circulante",),
    "passivo_circulante": ("passivo circulante",),
    "passivo_nao_circulante": ("passivo nao circulante",),
    "patrimonio_liquido": (
        "patrimonio liquido consolidado",
        "patrimonio liquido",
    ),
    "receita_liquida": (
        "receita de venda de bens e/ou servicos",
        "receitas da intermediacao financeira",
        "receita liquida de vendas e servicos",
        "receitas de intermediacao financeira",
    ),
    "lucro_liquido": (
        "lucro/prejuizo consolidado do periodo",
        "lucro ou prejuizo liquido consolidado do periodo",
        "lucro/prejuizo do periodo",
    ),
    "ebit": (
        "resultado antes do resultado financeiro e dos tributos",
    ),
    "resultado_financeiro": ("resultado financeiro",),
    "lucros_acumulados": (
        "lucros/prejuizos acumulados",
        "lucros ou prejuizos acumulados",
        "reservas de lucros",
    ),
    "caixa": (
        "caixa e equivalentes de caixa",
    ),
}


def _normalizar_texto(bruto):
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(bruto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.lower().split())


DESCRICAO_PARA_CAMPO = {}
for _campo, _textos in DESCRICOES.items():
    for _texto in _textos:
        DESCRICAO_PARA_CAMPO.setdefault(_normalizar_texto(_texto), _campo)

CAMPOS = ["ativo_total", "ativo_circulante", "passivo_circulante",
          "passivo_nao_circulante", "patrimonio_liquido", "receita_liquida",
          "ebit", "resultado_financeiro", "lucro_liquido", "lpa_on",
          # Crédito: alavancagem, cobertura de juros e Altman Z''. Sem estes
          # o laudo de emissor dependia do Yahoo, que não responde do Render.
          "caixa", "divida_curto_prazo", "divida_longo_prazo",
          "despesa_financeira", "lucros_acumulados"]


def baixar_zip(ano):
    url = URL_DFP.format(ano=ano)
    print(f"[{ano}] baixando {url}")
    resposta = requests.get(url, timeout=TIMEOUT)
    if resposta.status_code != 200:
        print(f"[{ano}] CVM respondeu {resposta.status_code} — pulando")
        return None
    return zipfile.ZipFile(io.BytesIO(resposta.content))


def _escala(valor):
    return ESCALAS.get(str(valor or "").strip().upper(), 1.0)


def ler_demonstrativo(arquivo_zip, nome_csv, mapa_contas, alternativas=None):
    """Lê um CSV da DFP em blocos e devolve {(cnpj, ano): {campo: valor}}.

    Filtra cedo — só ORDEM_EXERC 'ÚLTIMO' e as contas que interessam — para o
    arquivo inteiro nunca precisar caber na memória.
    """
    alternativas = alternativas or {}
    interessa = set(mapa_contas) | set(alternativas)
    coletado = defaultdict(dict)

    try:
        with arquivo_zip.open(nome_csv) as fluxo:
            blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                 usecols=COLUNAS, dtype=str, chunksize=TAMANHO_BLOCO)
            for bloco in blocos:
                bloco = bloco[bloco["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                # Aceita por código OU por descrição. Filtrar só por código
                # descartava a linha do banco antes de olhar a descrição — que
                # é justamente o que corrige o plano de contas diferente.
                por_codigo = bloco["CD_CONTA"].str.strip().isin(interessa)
                por_texto = bloco["DS_CONTA"].map(_normalizar_texto).isin(DESCRICAO_PARA_CAMPO)
                bloco = bloco[por_codigo | por_texto]
                if bloco.empty:
                    continue

                for linha in bloco.itertuples(index=False):
                    cnpj = "".join(ch for ch in str(linha.CNPJ_CIA) if ch.isdigit())
                    if len(cnpj) != 14:
                        continue
                    ano = str(linha.DT_FIM_EXERC)[:4]
                    if not ano.isdigit():
                        continue

                    try:
                        valor = float(str(linha.VL_CONTA).replace(",", "."))
                    except (TypeError, ValueError):
                        continue

                    conta = str(linha.CD_CONTA).strip()
                    descricao = _normalizar_texto(linha.DS_CONTA)

                    # Prioridade: descrição padronizada > código da conta.
                    campo = DESCRICAO_PARA_CAMPO.get(descricao)
                    por_descricao = campo is not None
                    alternativo = False
                    if campo is None:
                        campo = mapa_contas.get(conta)
                        if campo is None:
                            campo = alternativas.get(conta)
                            alternativo = campo is not None
                    if not campo:
                        continue

                    # LPA já vem em reais por ação: escala não se aplica.
                    if campo != "lpa_on":
                        valor *= _escala(linha.ESCALA_MOEDA)

                    registro = coletado[(cnpj, int(ano))]
                    # Valor vindo da descrição nunca é sobrescrito por código.
                    if campo in registro and not por_descricao:
                        continue
                    if campo in registro and alternativo:
                        continue
                    registro[campo] = valor
                    registro.setdefault("denom_cia", str(linha.DENOM_CIA).strip())
    except KeyError:
        print(f"   ! {nome_csv} não está no zip")
    except Exception as exc:  # noqa: BLE001
        print(f"   ! falha lendo {nome_csv}: {type(exc).__name__}: {exc}")

    return coletado


def processar_ano(ano):
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return {}

    registros = defaultdict(dict)
    for grupo, mapa in CONTAS.items():
        alternativas = CONTAS_ALTERNATIVAS if grupo == "DRE" else None

        consolidado = ler_demonstrativo(
            arquivo_zip, f"dfp_cia_aberta_{grupo}_con_{ano}.csv", mapa, alternativas)
        print(f"   {grupo} consolidado: {len(consolidado)} companhias")

        # Empresa sem controlada não publica consolidado — só o individual.
        # Era por isso que Sanepar e Assaí não apareciam na base.
        individual = ler_demonstrativo(
            arquivo_zip, f"dfp_cia_aberta_{grupo}_ind_{ano}.csv", mapa, alternativas)
        novas = set(individual) - set(consolidado)
        if novas:
            print(f"   {grupo} individual:   +{len(novas)} companhias sem consolidado")

        for chave, valores in consolidado.items():
            registros[chave].update(valores)
        for chave in novas:
            registros[chave].update(individual[chave])

    return registros


def gravar(registros, banco=BANCO):
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    colunas = ", ".join(f"{campo} REAL" for campo in CAMPOS)
    # A base é reconstruída inteira a cada execução, e o CREATE IF NOT EXISTS
    # não altera o esquema de uma tabela que já existe. Sem este DROP, adicionar
    # um campo em CAMPOS fazia a coleta inteira morrer no INSERT com
    # "table fundamentos has no column named caixa" — que foi exatamente o que
    # aconteceu ao incluir os campos de crédito.
    cursor.execute("DROP TABLE IF EXISTS fundamentos")
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS fundamentos (
            cnpj TEXT NOT NULL,
            ano INTEGER NOT NULL,
            denom_cia TEXT,
            {colunas},
            PRIMARY KEY (cnpj, ano)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cnpj ON fundamentos(cnpj)")

    linhas = []
    for (cnpj, ano), valores in registros.items():
        linhas.append([cnpj, ano, valores.get("denom_cia")]
                      + [valores.get(campo) for campo in CAMPOS])

    marcadores = ", ".join("?" * (3 + len(CAMPOS)))
    cursor.executemany(
        f"INSERT OR REPLACE INTO fundamentos "
        f"(cnpj, ano, denom_cia, {', '.join(CAMPOS)}) VALUES ({marcadores})",
        linhas,
    )
    conexao.commit()

    print(f"\n{len(linhas)} registros gravados em {banco}")
    for campo in CAMPOS:
        preenchidos = cursor.execute(
            f"SELECT COUNT(*) FROM fundamentos WHERE {campo} IS NOT NULL").fetchone()[0]
        print(f"   {campo:<24} {preenchidos}")
    conexao.close()


def _bi(valor):
    return "—" if valor is None else f"{valor / 1e9:.1f}"


def relatorio_qualidade(banco=BANCO):
    """Checagens que denunciam mapeamento errado de conta.

    Patrimônio maior que o ativo é impossível; patrimônio acima de 90% do ativo
    é implausível para qualquer companhia operacional. Foi assim que o erro dos
    bancos apareceu — vale deixar a checagem no script.
    """
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM fundamentos").fetchone()[0] or 1

    checagens = [
        ("patrimônio > ativo (impossível)",
         "patrimonio_liquido IS NOT NULL AND ativo_total IS NOT NULL "
         "AND patrimonio_liquido > ativo_total"),
        ("patrimônio > 90% do ativo (implausível)",
         "patrimonio_liquido IS NOT NULL AND ativo_total > 0 "
         "AND patrimonio_liquido > 0.9 * ativo_total"),
        ("LPA zerado", "lpa_on = 0"),
        ("sem lucro líquido", "lucro_liquido IS NULL"),
        ("sem patrimônio líquido", "patrimonio_liquido IS NULL"),
    ]
    print("\nQualidade:")
    for rotulo, condicao in checagens:
        n = cursor.execute(f"SELECT COUNT(*) FROM fundamentos WHERE {condicao}").fetchone()[0]
        marca = "  <-- revisar" if n > total * 0.05 else ""
        print(f"   {rotulo:<40} {n:>5}  {n / total:>6.1%}{marca}")

    print("\nAmostra (maiores ativos do exercício mais recente):")
    for nome, ativo, pl, receita, lucro, lpa in cursor.execute(
            """SELECT denom_cia, ativo_total, patrimonio_liquido, receita_liquida,
                      lucro_liquido, lpa_on
               FROM fundamentos WHERE ano = (SELECT MAX(ano) FROM fundamentos)
               ORDER BY ativo_total DESC LIMIT 8"""):
        roe = (lucro / pl * 100) if (lucro is not None and pl) else None
        print(f"   {(nome or '')[:30]:<30} ativo {_bi(ativo):>8}bi  PL {_bi(pl):>8}bi  "
              f"receita {_bi(receita):>7}bi  lucro {_bi(lucro):>7}bi  "
              f"ROE {'—' if roe is None else format(roe, '.1f') + '%':>7}  LPA {lpa}")
    conexao.close()


def inspecionar(cnpj_alvo, ano):
    """Despeja o plano de contas de UMA companhia.

        python atualizar_fundamentos_cvm.py --inspecionar 60872504000123 2025

    Serve para descobrir como um emissor publica as contas quando o mapeamento
    padrão erra — em vez de continuar adivinhando o layout.
    """
    cnpj_alvo = "".join(ch for ch in str(cnpj_alvo) if ch.isdigit())
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return
    for grupo in ("BPA", "BPP", "DRE"):
        nome_csv = f"dfp_cia_aberta_{grupo}_con_{ano}.csv"
        print(f"\n===== {grupo} =====")
        try:
            with arquivo_zip.open(nome_csv) as fluxo:
                blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                     usecols=COLUNAS, dtype=str, chunksize=TAMANHO_BLOCO)
                for bloco in blocos:
                    alvo = bloco[
                        bloco["CNPJ_CIA"].str.replace(r"\D", "", regex=True) == cnpj_alvo]
                    alvo = alvo[alvo["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                    for linha in alvo.itertuples(index=False):
                        conta = str(linha.CD_CONTA).strip()
                        # Só o topo da árvore: 1, 1.01, 2.03, 3.11, 3.99.01.01
                        if conta.count(".") > 2:
                            continue
                        try:
                            valor = float(str(linha.VL_CONTA).replace(",", "."))
                        except (TypeError, ValueError):
                            continue
                        print(f"   {conta:<12} {str(linha.DS_CONTA)[:52]:<52} "
                              f"{valor:>18,.0f}  [{linha.ESCALA_MOEDA}]")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {type(exc).__name__}: {exc}")


def main(anos):
    registros = {}
    for ano in anos:
        parcial = processar_ano(ano)
        for chave, valores in parcial.items():
            registros.setdefault(chave, {}).update(valores)
    if not registros:
        raise SystemExit("Nenhum dado obtido da CVM.")
    gravar(registros)
    relatorio_qualidade()


if __name__ == "__main__":
    if "--inspecionar" in sys.argv:
        posicao = sys.argv.index("--inspecionar")
        restante = sys.argv[posicao + 1:]
        if not restante:
            raise SystemExit("uso: --inspecionar <cnpj> [ano]")
        cnpj = restante[0]
        ano_alvo = int(restante[1]) if len(restante) > 1 else 2025
        inspecionar(cnpj, ano_alvo)
        raise SystemExit(0)

    argumentos = [a for a in sys.argv[1:] if a.isdigit()]
    if argumentos:
        anos_alvo = [int(a) for a in argumentos]
    else:
        from datetime import date
        atual = date.today().year
        anos_alvo = [atual - 3, atual - 2, atual - 1]
    print(f"Exercícios: {anos_alvo}")
    main(anos_alvo)
