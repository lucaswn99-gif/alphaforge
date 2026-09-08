"""Diagnóstico do scanner: descobre o que o Yahoo e o B3 realmente devolvem.

    python diagnostico.py

Não altera nada. A saída é curta de propósito, para colar no chat.
"""
import sys
import time
import traceback


def secao(titulo):
    print("\n" + "=" * 62)
    print(titulo)
    print("=" * 62)


def versoes():
    secao("1. AMBIENTE")
    print(f"python      {sys.version.split()[0]}")
    for nome in ("yfinance", "pandas", "numpy", "requests", "curl_cffi"):
        try:
            mod = __import__(nome)
            print(f"{nome:<11} {getattr(mod, '__version__', '?')}")
        except Exception as exc:
            print(f"{nome:<11} AUSENTE ({type(exc).__name__})")


def composicao_b3():
    secao("2. CARTEIRA DO IBOV (B3)")
    try:
        from modules import composicao_ibov
    except Exception:
        print("nao consegui importar modules/composicao_ibov.py")
        traceback.print_exc(limit=2)
        return
    inicio = time.time()
    try:
        codigos, origem, idade = composicao_ibov.obter_composicao(forcar=True)
        print(f"origem={origem}  papeis={len(codigos)}  {time.time() - inicio:.1f}s")
        print("primeiros:", ", ".join(codigos[:8]))
    except Exception:
        print("EXCECAO:")
        traceback.print_exc(limit=3)


def precos():
    secao("3. yf.download (serie de precos)")
    import yfinance as yf
    alvos = ["PETR4.SA", "VALE3.SA", "ITUB4.SA"]
    inicio = time.time()
    try:
        df = yf.download(alvos, period="3y", interval="1d", progress=False,
                         group_by="ticker", auto_adjust=True)
        print(f"tempo={time.time() - inicio:.1f}s  vazio={df.empty}  shape={getattr(df, 'shape', None)}")
        if not df.empty:
            import pandas as pd
            multi = isinstance(df.columns, pd.MultiIndex)
            print("MultiIndex:", multi)
            for alvo in alvos:
                try:
                    sub = df[alvo] if multi else df
                    fechamentos = sub["Close"].dropna()
                    ultimo = float(fechamentos.iloc[-1])
                    print(f"  {alvo}: {len(fechamentos)} pregoes, ultimo {ultimo:.2f}, "
                          f"data {fechamentos.index[-1].date()}")
                except Exception as exc:
                    print(f"  {alvo}: FALHOU ({type(exc).__name__}: {exc})")
    except Exception:
        print("EXCECAO:")
        traceback.print_exc(limit=3)


CAMPOS = ["shortName", "trailingPE", "priceToBook", "returnOnEquity", "profitMargins",
          "dividendYield", "dividendRate", "currentPrice", "netIncomeToCommon",
          "bookValue", "sharesOutstanding", "marketCap", "totalStockholderEquity"]


def fundamentos():
    secao("4. Ticker.info (multiplos) - o endpoint que costuma ser limitado")
    import yfinance as yf
    for alvo in ["PETR4.SA", "VALE3.SA", "ITUB4.SA"]:
        inicio = time.time()
        try:
            info = yf.Ticker(alvo).info or {}
            print(f"\n{alvo}  tempo={time.time() - inicio:.1f}s  chaves={len(info)}")
            if not info:
                print("  .info veio VAZIO")
                continue
            for campo in CAMPOS:
                valor = info.get(campo, "<ausente>")
                if isinstance(valor, float):
                    valor = f"{valor:.6g}"
                print(f"    {campo:<24} {valor}")
        except Exception as exc:
            print(f"\n{alvo}  tempo={time.time() - inicio:.1f}s")
            print(f"  EXCECAO {type(exc).__name__}: {str(exc)[:200]}")


def roe_calculado():
    secao("5. ROE pelo caminho do scanner")
    try:
        from routers import equity
    except Exception:
        print("nao consegui importar routers/equity.py")
        traceback.print_exc(limit=2)
        return
    import yfinance as yf
    for alvo in ["PETR4.SA", "VALE3.SA", "ITUB4.SA"]:
        try:
            info = yf.Ticker(alvo).info or {}
            roe = equity.calcular_roe(info)
            pat = equity._derivar_patrimonio_liquido(info)
            dy = equity.normalizar_dy(info)
            print(f"{alvo}: roe={roe}  patrimonio_derivado={pat}  dy={dy}")
        except Exception as exc:
            print(f"{alvo}: EXCECAO {type(exc).__name__}: {str(exc)[:120]}")


def scanner_completo():
    secao("6. SCANNER COMPLETO (pode levar alguns minutos)")
    try:
        from routers import equity
    except Exception:
        print("nao consegui importar routers/equity.py")
        return
    inicio = time.time()
    try:
        r = equity.executar_scanner(forcar=True)
    except Exception:
        print("EXCECAO:")
        traceback.print_exc(limit=4)
        return
    print(f"tempo={time.time() - inicio:.1f}s")
    print(f"solicitados={r.get('solicitados')}  total={r.get('total')}  "
          f"com_fundamentos={r.get('com_fundamentos')}  origem={r.get('origem_composicao')}")
    falhas = r.get("falhas", [])
    print(f"falhas (sem preco)={len(falhas)}")
    for f in falhas[:10]:
        print(f"   {f['ticker']:<8} {f['simbolo']:<12} {f['motivo']}")
    sem = r.get("sem_fundamentos", [])
    print(f"sem multiplos={len(sem)}")
    for f in sem[:10]:
        print(f"   {f['ticker']:<8} {f['simbolo']:<12} {f['motivo']}")
    print("top 5:")
    for linha in r.get("oportunidades", [])[:5]:
        print(f"   {linha['ticker']:<8} preco={linha['preco']:<9} pl={linha['pl']} "
              f"roe={linha['roe']} score={linha['score_geral']} {linha['veredito']}")


if __name__ == "__main__":
    versoes()
    composicao_b3()
    precos()
    fundamentos()
    roe_calculado()
    if "--rapido" not in sys.argv:
        scanner_completo()
    print("\nfim.")
