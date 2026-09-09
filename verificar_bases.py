"""Trava de sanidade das bases da CVM, antes de subir qualquer coisa.

    python verificar_bases.py     # sai 0 se as duas bases estão utilizáveis

Existe porque erro de dado passa nos testes. Já aconteceu duas vezes:

  1. a base de FII foi ao ar com cota de R$ 0,02 — a coluna do informe é o
     valor POR COTA e uma versão anterior dividia de novo pelo número de cotas;
  2. a base de fundamentos foi ao ar SEM as colunas de crédito — o
     `CREATE TABLE IF NOT EXISTS` não altera o esquema de uma tabela que já
     existe, a coleta morreu no INSERT, e o push aconteceu assim mesmo porque
     a trava anterior só olhava a base de FII.

Os testes cobriam as funções; ninguém olhava o dado. Isto olha o dado. Sai
diferente de zero quando algo não fecha, e o script que sobe para o GitHub só
continua se este passar.
"""

import sqlite3
import statistics
import sys

BANCO_FII = "fundos_cvm.db"
BANCO_FUNDAMENTOS = "fundamentos_cvm.db"

# --- base de FII ---------------------------------------------------------
# Cota de FII no Brasil vive entre poucos reais e alguns milhares. Uma mediana
# fora disso significa que a leitura da coluna virou, não que o mercado mudou.
MEDIANA_MINIMA = 5.0
MEDIANA_MAXIMA = 1_000.0
FUNDOS_MINIMOS = 500
FRACAO_MINIMA_PLAUSIVEL = 0.80

# Fundos grandes e líquidos: se o ISIN virou ticker corretamente, estes têm que
# estar no mapa. Não checamos o VALOR deles — só que o vínculo existe.
TICKERS_ESPERADOS = ("HGLG11", "MXRF11", "KNRI11", "XPML11", "BTLG11",
                     "VISC11", "HGRU11", "KNCR11")

# --- base de fundamentos -------------------------------------------------
COMPANHIAS_MINIMAS = 300
# Colunas que o scanner e o laudo de crédito consomem. Faltar uma significa
# que a base é de uma versão anterior do coletor.
COLUNAS_OBRIGATORIAS = (
    "patrimonio_liquido", "lucro_liquido", "receita_liquida", "lpa_on",
    "ativo_total", "ativo_circulante", "passivo_circulante", "ebit",
    "caixa", "divida_curto_prazo", "divida_longo_prazo",
    "despesa_financeira", "lucros_acumulados",
    # Sem esta, um exercício de impairment é indistinguível de empresa cara.
    "perdas_nao_recorrentes",
)
# Coluna declarada mas vazia em toda a base é o mesmo que coluna ausente.
PREENCHIMENTO_MINIMO = {
    "patrimonio_liquido": 0.80, "lucro_liquido": 0.80, "ativo_total": 0.80,
    # Dívida e caixa: nem toda companhia tem dívida onerosa, e banco usa outro
    # plano de contas — um piso alto aqui reprovaria uma base correta.
    "divida_longo_prazo": 0.30, "caixa": 0.50, "ebit": 0.50,
}

_falhas = []


def falhar(mensagem):
    _falhas.append(mensagem)
    print(f"FALHOU: {mensagem}")


def _conectar(caminho):
    try:
        conexao = sqlite3.connect(caminho)
        conexao.row_factory = sqlite3.Row
        return conexao
    except sqlite3.Error as exc:
        falhar(f"não abriu {caminho}: {exc}")
        return None


def verificar_fii():
    print("\n--- base de FII (Informe Mensal) ---")
    conexao = _conectar(BANCO_FII)
    if conexao is None:
        return
    try:
        linhas = conexao.execute(
            "SELECT vp_por_cota, competencia FROM fundos WHERE vp_por_cota > 0").fetchall()
    except sqlite3.Error as exc:
        falhar(f"{BANCO_FII} sem tabela `fundos` utilizável: {exc}")
        return

    if len(linhas) < FUNDOS_MINIMOS:
        falhar(f"só {len(linhas)} fundos na base (mínimo {FUNDOS_MINIMOS})")
        return

    valores = [linha["vp_por_cota"] for linha in linhas]
    mediana = statistics.median(valores)
    print(f"{len(valores)} fundos, VP/cota mediano R$ {mediana:,.2f}")
    if not MEDIANA_MINIMA <= mediana <= MEDIANA_MAXIMA:
        falhar(f"VP/cota mediano R$ {mediana:,.2f} fora da faixa "
               f"[{MEDIANA_MINIMA}, {MEDIANA_MAXIMA}] — a leitura da coluna virou")

    fracao = sum(1 for v in valores if 1.0 <= v <= 5_000.0) / len(valores)
    print(f"{fracao:.0%} dos fundos com VP/cota plausível")
    if fracao < FRACAO_MINIMA_PLAUSIVEL:
        falhar(f"só {fracao:.0%} dos fundos com VP/cota plausível "
               f"(mínimo {FRACAO_MINIMA_PLAUSIVEL:.0%})")

    print(f"competência mais recente: {max(l['competencia'] for l in linhas)}")

    try:
        mapa = {l["ticker"] for l in conexao.execute("SELECT ticker FROM tickers")}
    except sqlite3.Error:
        mapa = set()
    print(f"{len(mapa)} tickers mapeados pelo ISIN")
    faltando = [t for t in TICKERS_ESPERADOS if t not in mapa]
    if faltando:
        falhar(f"tickers conhecidos ausentes do mapa: {', '.join(faltando)}")
    conexao.close()


def verificar_fundamentos():
    print("\n--- base de fundamentos (DFP) ---")
    conexao = _conectar(BANCO_FUNDAMENTOS)
    if conexao is None:
        return
    try:
        colunas = {c["name"] for c in conexao.execute("PRAGMA table_info(fundamentos)")}
    except sqlite3.Error as exc:
        falhar(f"{BANCO_FUNDAMENTOS} sem tabela `fundamentos`: {exc}")
        return

    if not colunas:
        falhar(f"{BANCO_FUNDAMENTOS} não tem a tabela `fundamentos`")
        return

    ausentes = [c for c in COLUNAS_OBRIGATORIAS if c not in colunas]
    if ausentes:
        falhar("base de uma versão anterior do coletor — faltam as colunas: "
               + ", ".join(ausentes)
               + ". Rode `python atualizar_fundamentos_cvm.py` de novo.")
        return

    total = conexao.execute("SELECT COUNT(*) FROM fundamentos").fetchone()[0]
    companhias = conexao.execute("SELECT COUNT(DISTINCT cnpj) FROM fundamentos").fetchone()[0]
    print(f"{total} exercícios, {companhias} companhias")
    if companhias < COMPANHIAS_MINIMAS:
        falhar(f"só {companhias} companhias (mínimo {COMPANHIAS_MINIMAS})")

    # Coluna declarada mas vazia engana tanto quanto coluna ausente.
    for campo, piso in PREENCHIMENTO_MINIMO.items():
        preenchidos = conexao.execute(
            f"SELECT COUNT(*) FROM fundamentos WHERE {campo} IS NOT NULL").fetchone()[0]
        fracao = preenchidos / total if total else 0.0
        marca = "ok " if fracao >= piso else "BAIXO"
        print(f"   {marca} {campo:<22} {fracao:>5.0%} preenchido (piso {piso:.0%})")
        if fracao < piso:
            falhar(f"coluna `{campo}` preenchida em só {fracao:.0%} das linhas")

    exercicio = conexao.execute("SELECT MAX(ano) FROM fundamentos").fetchone()[0]
    print(f"exercício mais recente: {exercicio}")
    conexao.close()


def main():
    verificar_fii()
    verificar_fundamentos()
    if _falhas:
        print(f"\n{len(_falhas)} problema(s) — nada deve subir assim.")
        return 1
    print("\nOK — as duas bases estão utilizáveis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
