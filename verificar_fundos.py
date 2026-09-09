"""Trava de sanidade da base de FIIs, antes de subir qualquer coisa.

    python verificar_fundos.py        # sai 0 se a base está utilizável

Existe porque a base de FII já foi ao ar com cota de R$ 0,02: a coluna do
informe é o valor POR COTA, e uma versão anterior dividia de novo pelo número
de cotas. O erro passou nos testes — os testes cobriam a função, não o dado.

Isto olha o dado. Sai diferente de zero quando algo não fecha, e o script que
sobe para o GitHub só continua se este passar.
"""

import sqlite3
import statistics
import sys

BANCO = "fundos_cvm.db"

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


def falhar(mensagem):
    print(f"FALHOU: {mensagem}")
    sys.exit(1)


def main():
    try:
        conexao = sqlite3.connect(BANCO)
        conexao.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        falhar(f"não abriu {BANCO}: {exc}")

    linhas = conexao.execute(
        "SELECT vp_por_cota, competencia FROM fundos WHERE vp_por_cota > 0").fetchall()
    if len(linhas) < FUNDOS_MINIMOS:
        falhar(f"só {len(linhas)} fundos na base (mínimo {FUNDOS_MINIMOS})")

    valores = [linha["vp_por_cota"] for linha in linhas]
    mediana = statistics.median(valores)
    print(f"{len(valores)} fundos, VP/cota mediano R$ {mediana:,.2f}")

    if not MEDIANA_MINIMA <= mediana <= MEDIANA_MAXIMA:
        falhar(f"VP/cota mediano R$ {mediana:,.2f} fora da faixa "
               f"[{MEDIANA_MINIMA}, {MEDIANA_MAXIMA}] — a leitura da coluna virou")

    dentro = sum(1 for v in valores if 1.0 <= v <= 5_000.0)
    fracao = dentro / len(valores)
    print(f"{fracao:.0%} dos fundos com VP/cota plausível")
    if fracao < FRACAO_MINIMA_PLAUSIVEL:
        falhar(f"só {fracao:.0%} dos fundos com VP/cota plausível "
               f"(mínimo {FRACAO_MINIMA_PLAUSIVEL:.0%})")

    competencia = max(linha["competencia"] for linha in linhas)
    print(f"competência mais recente: {competencia}")

    try:
        mapa = {linha["ticker"] for linha in
                conexao.execute("SELECT ticker FROM tickers").fetchall()}
    except sqlite3.Error:
        mapa = set()
    print(f"{len(mapa)} tickers mapeados pelo ISIN")

    faltando = [t for t in TICKERS_ESPERADOS if t not in mapa]
    if faltando:
        falhar(f"tickers conhecidos ausentes do mapa: {', '.join(faltando)}")

    conexao.close()
    print("OK — base utilizável")
    return 0


if __name__ == "__main__":
    sys.exit(main())
