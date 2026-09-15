"""Cota diária dos fundos de investimento cadastrados na carteira — Etapa B
do item 3 do escopo (ver docstring de `modules/fundos.py`).

Fonte: o **Informe Diário de Fundos** da CVM (`inf_diario_fi`), um arquivo
por mês, cota de TODOS os ~20 mil fundos registrados no Brasil. Isso é ordem
de grandeza maior que qualquer outra base deste projeto — e a esmagadora
maioria desses ~20 mil fundos nunca vai aparecer na carteira de um cliente.
Por isso este coletor **filtra pelo CNPJ que já está cadastrado em
`fundos`** (a tabela de `contas.db`, todas as contas) em vez de guardar o
universo inteiro: o banco cresce com o que é de fato usado, não com o
mercado inteiro.

    python atualizar_cota_fundos_cvm.py                    # mês atual + anterior
    python atualizar_cota_fundos_cvm.py 202401 202412       # intervalo (AAAAMM)
    python atualizar_cota_fundos_cvm.py --cnpj 12345678000199 202301
    python atualizar_cota_fundos_cvm.py --inspecionar 202609

`--cnpj` acrescenta CNPJs além dos já cadastrados — útil para pré-coletar
antes de o assessor cadastrar a posição, ou para conferir um fundo específico
sem tocar o resto do banco.

Roda FORA do serviço web (mesmo desenho de `atualizar_fundos_cvm.py`, o
coletor de FII) e grava `cota_fundos_cvm.db`, que a API só lê
(`modules/cota_cvm.py`). Ao contrário daquele coletor — que RECONSTRÓI a
base inteira a cada execução porque é só a foto do "estado atual" —, este
ACRESCENTA: é uma série no tempo, e uma execução não deve apagar meses já
coletados em execuções anteriores.

COMO ACHA O ARQUIVO
--------------------
A partir de 2021 a CVM publica um zip por mês:
  https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_AAAAMM.zip
Para competências anteriores a 2021, o arquivo é um zip por ANO, num
subdiretório HIST/, contendo os CSVs mensais dentro:
  https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/HIST/inf_diario_fi_AAAA.zip
Este script tenta o caminho mensal primeiro e cai para o histórico anual
quando aquele não existe (404) — a mesma ideia de fallback, só que aqui a
CVM já documenta os dois formatos, não é uma heurística.

LAYOUT (dicionário de dados oficial, campo usado entre parênteses)
--------------------------------------------------------------------
CNPJ_FUNDO_CLASSE (chave — o nome mudou de CNPJ_FUNDO com a Resolução 175,
mesmo conteúdo), DT_COMPTC (data, usada), VL_QUOTA (cota, usada — NUMERIC de
12 casas: não arredonda ao gravar), VL_PATRIM_LIQ e NR_COTST (guardados só
como contexto, não usados em conta nenhuma ainda). Casamento de coluna por
padrão (regex), não por nome fixo — mesma defesa de `atualizar_fundos_cvm.py`
contra a CVM renomear colunas entre revisões normativas.
"""

import io
import re
import sqlite3
import sys
import unicodedata
import zipfile
from datetime import date, datetime, timezone

import pandas as pd
import requests

URL_MENSAL = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/inf_diario_fi_{aaaamm}.zip"
URL_HIST_ANUAL = "https://dados.cvm.gov.br/dados/FI/DOC/INF_DIARIO/DADOS/HIST/inf_diario_fi_{ano}.zip"
BANCO = "cota_fundos_cvm.db"
TIMEOUT = 180

PADROES_ALVO = {
    "cnpj": [r"^cnpj([ _]?(do)?[ _]?fundo)?([ _]?classe)?$"],
    "data": [r"^dt[ _]?comptc$", r"^(data|dt)[ _]?(de[ _]?)?(referencia|competencia)$"],
    "cota": [r"^vl[ _]?quota$", r"^valor[ _]?(da[ _]?)?cota$"],
    "patrimonio_liquido": [r"^vl[ _]?patrim[ _]?liq$", r"^patrimonio[ _]?liquido$"],
    "numero_cotistas": [r"^nr[ _]?cotst$", r"^numero[ _]?(de[ _]?)?cotistas$"],
}


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = texto.replace("_", " ").replace("-", " ")
    return " ".join(texto.lower().split())


def _mapear_colunas(colunas):
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


def _so_digitos(valor):
    return "".join(ch for ch in str(valor or "") if ch.isdigit())


def _para_numero(valor):
    """A CVM alterna ponto e vírgula como separador decimal (mesma regra de
    `atualizar_fundos_cvm.py`)."""
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


def _mes_anterior(aaaamm):
    ano, mes = aaaamm // 100, aaaamm % 100
    return (ano - 1) * 100 + 12 if mes == 1 else ano * 100 + (mes - 1)


def _meses_no_intervalo(inicio_aaaamm, fim_aaaamm):
    ano, mes = inicio_aaaamm // 100, inicio_aaaamm % 100
    ano_fim, mes_fim = fim_aaaamm // 100, fim_aaaamm % 100
    meses = []
    while (ano, mes) <= (ano_fim, mes_fim):
        meses.append(ano * 100 + mes)
        mes += 1
        if mes > 12:
            mes = 1
            ano += 1
    return meses


def _baixar(url, rotulo):
    print(f"[{rotulo}] baixando {url}")
    try:
        resposta = requests.get(url, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        print(f"[{rotulo}] falha de rede: {type(exc).__name__}: {exc}")
        return None
    if resposta.status_code == 404:
        return 404
    if resposta.status_code != 200:
        print(f"[{rotulo}] CVM respondeu {resposta.status_code} — pulando")
        return None
    return zipfile.ZipFile(io.BytesIO(resposta.content))


def _csvs_do_mes(aaaamm):
    """[(nome_csv, bytes_do_csv), ...] — normalmente um só CSV (caminho
    mensal), ou o CSV certo dentro do zip anual (caminho HIST)."""
    ano, mes = aaaamm // 100, aaaamm % 100

    arquivo_zip = _baixar(URL_MENSAL.format(aaaamm=aaaamm), aaaamm)
    if isinstance(arquivo_zip, zipfile.ZipFile):
        return [(nome, arquivo_zip.open(nome).read())
                for nome in arquivo_zip.namelist() if nome.lower().endswith(".csv")]
    if arquivo_zip not in (None, 404):
        return []  # zip corrompido ou outro erro já reportado por _baixar

    # Caminho mensal não existe (404, típico de antes de 2021) — tenta o
    # histórico anual, que empacota os 12 meses juntos.
    arquivo_zip = _baixar(URL_HIST_ANUAL.format(ano=ano), f"{ano} (HIST)")
    if not isinstance(arquivo_zip, zipfile.ZipFile):
        return []
    alvo = f"{aaaamm:06d}"
    saida = []
    for nome in arquivo_zip.namelist():
        if not nome.lower().endswith(".csv"):
            continue
        # O CSV mensal dentro do zip anual traz a competência no próprio nome
        # (ex.: inf_diario_fi_202006.csv) — só extrai o mês pedido.
        if alvo in nome or re.search(rf"{ano}[-_]?{mes:02d}", nome):
            saida.append((nome, arquivo_zip.open(nome).read()))
    return saida


def inspecionar(aaaamm):
    csvs = _csvs_do_mes(aaaamm)
    if not csvs:
        print(f"[{aaaamm}] nenhum CSV encontrado")
        return
    for nome, bruto in csvs:
        print(f"\n===== {nome} =====")
        try:
            amostra = pd.read_csv(io.BytesIO(bruto), sep=";", encoding="iso-8859-1",
                                  dtype=str, nrows=2)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {type(exc).__name__}: {exc}")
            continue
        for coluna in amostra.columns:
            exemplo = amostra[coluna].iloc[0] if len(amostra) else ""
            print(f"   {coluna:<24} | {str(exemplo)[:34]}")
        print(f"   -> campos reconhecidos: {_mapear_colunas(amostra.columns)}")


def processar_mes(aaaamm, cnpjs_alvo):
    """[(cnpj, data_iso, cota, pl, cotistas), ...] só para `cnpjs_alvo`."""
    csvs = _csvs_do_mes(aaaamm)
    if not csvs:
        print(f"[{aaaamm}] sem arquivo (fora do intervalo publicado pela CVM?)")
        return []

    linhas_saida = []
    for nome, bruto in csvs:
        try:
            dados = pd.read_csv(io.BytesIO(bruto), sep=";", encoding="iso-8859-1", dtype=str)
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {nome}: {type(exc).__name__}")
            continue

        mapa = _mapear_colunas(dados.columns)
        if "cnpj" not in mapa or "data" not in mapa or "cota" not in mapa:
            print(f"   - {nome}: sem CNPJ/data/cota reconhecidos, ignorado")
            continue

        # Renomeia para os nomes canônicos (em vez de indexar por posição de
        # coluna, que quebra silenciosamente se a ordem mudar) e mantém só o
        # que foi reconhecido.
        opcionais = [c for c in ("patrimonio_liquido", "numero_cotistas") if c in mapa]
        campos = ["cnpj", "data", "cota"] + opcionais
        recorte = dados[[mapa[c] for c in campos]].copy()
        recorte.columns = campos

        # Filtra pelos CNPJs alvo ANTES de iterar linha a linha — o arquivo
        # inteiro tem ~20 mil fundos por dia útil, e só alguns interessam.
        recorte["cnpj"] = recorte["cnpj"].map(_so_digitos)
        recorte = recorte[recorte["cnpj"].isin(cnpjs_alvo)]

        for registro in recorte.to_dict("records"):
            cnpj = registro["cnpj"]
            data_iso = str(registro["data"])[:10]
            cota = _para_numero(registro["cota"])
            if len(cnpj) != 14 or not data_iso or cota is None or cota <= 0:
                continue
            pl = _para_numero(registro["patrimonio_liquido"]) \
                if "patrimonio_liquido" in opcionais else None
            cotistas = _para_numero(registro["numero_cotistas"]) \
                if "numero_cotistas" in opcionais else None
            linhas_saida.append((cnpj, data_iso, cota, pl,
                                 int(cotistas) if cotistas is not None else None))

    encontrados = {cnpj for cnpj, *_ in linhas_saida}
    print(f"[{aaaamm}] {len(linhas_saida)} linhas, {len(encontrados)}/{len(cnpjs_alvo)} "
          "CNPJs alvo encontrados")
    return linhas_saida


def _cnpjs_cadastrados():
    """CNPJs de `fundos`, de TODAS as contas — o coletor serve a base
    inteira, não uma conta por vez."""
    from modules import contas
    contas.iniciar()
    with contas._conectar() as cx:
        linhas = cx.execute("SELECT DISTINCT cnpj FROM fundos").fetchall()
    return {l["cnpj"] for l in linhas}


def gravar(linhas, banco=BANCO):
    conexao = sqlite3.connect(banco)
    conexao.execute("""
        CREATE TABLE IF NOT EXISTS cotas (
            cnpj TEXT NOT NULL,
            data TEXT NOT NULL,
            valor_cota REAL NOT NULL,
            patrimonio_liquido REAL,
            numero_cotistas INTEGER,
            atualizado_em TEXT NOT NULL,
            PRIMARY KEY (cnpj, data)
        )
    """)
    conexao.execute("CREATE INDEX IF NOT EXISTS idx_cotas_cnpj ON cotas(cnpj)")
    agora = datetime.now(timezone.utc).isoformat()
    conexao.executemany(
        "INSERT OR REPLACE INTO cotas "
        "(cnpj, data, valor_cota, patrimonio_liquido, numero_cotistas, atualizado_em) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(cnpj, data_iso, cota, pl, cotistas, agora)
         for cnpj, data_iso, cota, pl, cotistas in linhas])
    conexao.commit()

    total = conexao.execute("SELECT COUNT(*) FROM cotas").fetchone()[0]
    fundos_distintos = conexao.execute("SELECT COUNT(DISTINCT cnpj) FROM cotas").fetchone()[0]
    print(f"\n{len(linhas)} linhas gravadas/atualizadas nesta execução.")
    print(f"banco {banco}: {total} linhas no total, {fundos_distintos} fundos distintos.")
    conexao.close()


def main(meses, cnpjs_extras):
    cadastrados = _cnpjs_cadastrados()
    cnpjs_alvo = cadastrados | {_so_digitos(c) for c in cnpjs_extras}
    if not cnpjs_alvo:
        print("Nenhum CNPJ cadastrado em `fundos` e nenhum --cnpj informado — "
              "nada para coletar (não faz sentido baixar o mercado inteiro).")
        return

    print(f"CNPJs alvo: {len(cnpjs_alvo)} "
          f"({len(cadastrados)} cadastrados + {len(cnpjs_alvo) - len(cadastrados)} extras)")

    todas_linhas = []
    vistos_algum_dado = set()
    for aaaamm in meses:
        linhas = processar_mes(aaaamm, cnpjs_alvo)
        todas_linhas.extend(linhas)
        vistos_algum_dado.update(cnpj for cnpj, *_ in linhas)

    if not todas_linhas:
        raise SystemExit(
            "Nenhum dado obtido em nenhum mês do intervalo. Rode:\n"
            "  python atualizar_cota_fundos_cvm.py --inspecionar <AAAAMM>\n"
            "e confira se o layout do informe mudou, ou se o CNPJ está certo.")

    sem_dado = cnpjs_alvo - vistos_algum_dado
    if sem_dado:
        print(f"\n{len(sem_dado)} CNPJ(s) alvo sem NENHUM dado no intervalo pedido "
              "(confira se o CNPJ está certo, ou peça um intervalo mais antigo):")
        for cnpj in sorted(sem_dado):
            print(f"   {cnpj}")

    gravar(todas_linhas)


if __name__ == "__main__":
    hoje = date.today()
    atual_aaaamm = hoje.year * 100 + hoje.month

    argv_sem_flags = []
    cnpjs_extras = []
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--cnpj":
            i += 1
            cnpjs_extras.append(sys.argv[i])
        elif arg != "--inspecionar":
            argv_sem_flags.append(arg)
        i += 1

    if "--inspecionar" in sys.argv:
        alvo = int(argv_sem_flags[0]) if argv_sem_flags else atual_aaaamm
        inspecionar(alvo)
        raise SystemExit(0)

    if len(argv_sem_flags) >= 2:
        meses = _meses_no_intervalo(int(argv_sem_flags[0]), int(argv_sem_flags[1]))
    elif len(argv_sem_flags) == 1:
        meses = [int(argv_sem_flags[0])]
    else:
        meses = [_mes_anterior(atual_aaaamm), atual_aaaamm]

    print(f"Meses: {meses}")
    main(meses, cnpjs_extras)
