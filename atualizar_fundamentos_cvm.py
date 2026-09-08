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
           "ESCALA_MOEDA", "CD_CONTA", "VL_CONTA"]

# Conta da CVM -> campo nosso. Só o que o motor de score consome.
CONTAS = {
    "BPA": {
        "1": "ativo_total",
        "1.01": "ativo_circulante",
    },
    "BPP": {
        "2.01": "passivo_circulante",
        "2.02": "passivo_nao_circulante",
        "2.03": "patrimonio_liquido",
    },
    "DRE": {
        "3.01": "receita_liquida",
        "3.05": "ebit",
        "3.06": "resultado_financeiro",
        "3.11": "lucro_liquido",
        "3.99.01.01": "lpa_on",
    },
}

# Empresa que não publica 3.11 costuma trazer o lucro em 3.13 (atribuído aos
# sócios da controladora). Serve como segunda opção, nunca sobrescreve 3.11.
CONTAS_ALTERNATIVAS = {"3.13": "lucro_liquido"}

ESCALAS = {"MIL": 1_000.0, "MILHAR": 1_000.0, "UNIDADE": 1.0, "UNIT": 1.0}

CAMPOS = ["ativo_total", "ativo_circulante", "passivo_circulante",
          "passivo_nao_circulante", "patrimonio_liquido", "receita_liquida",
          "ebit", "resultado_financeiro", "lucro_liquido", "lpa_on"]


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
                bloco = bloco[bloco["CD_CONTA"].str.strip().isin(interessa)]
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
                    campo = mapa_contas.get(conta)
                    alternativo = campo is None
                    if alternativo:
                        campo = alternativas.get(conta)
                    if not campo:
                        continue

                    # LPA já vem em reais por ação: escala não se aplica.
                    if campo != "lpa_on":
                        valor *= _escala(linha.ESCALA_MOEDA)

                    registro = coletado[(cnpj, int(ano))]
                    if alternativo and campo in registro:
                        continue  # a conta principal manda
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
        nome_csv = f"dfp_cia_aberta_{grupo}_con_{ano}.csv"
        alternativas = CONTAS_ALTERNATIVAS if grupo == "DRE" else None
        parcial = ler_demonstrativo(arquivo_zip, nome_csv, mapa, alternativas)
        print(f"   {grupo}: {len(parcial)} companhias")
        for chave, valores in parcial.items():
            registros[chave].update(valores)

    return registros


def gravar(registros, banco=BANCO):
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    colunas = ", ".join(f"{campo} REAL" for campo in CAMPOS)
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


def main(anos):
    registros = {}
    for ano in anos:
        parcial = processar_ano(ano)
        for chave, valores in parcial.items():
            registros.setdefault(chave, {}).update(valores)
    if not registros:
        raise SystemExit("Nenhum dado obtido da CVM.")
    gravar(registros)


if __name__ == "__main__":
    argumentos = [a for a in sys.argv[1:] if a.isdigit()]
    if argumentos:
        anos_alvo = [int(a) for a in argumentos]
    else:
        from datetime import date
        atual = date.today().year
        anos_alvo = [atual - 3, atual - 2, atual - 1]
    print(f"Exercícios: {anos_alvo}")
    main(anos_alvo)
