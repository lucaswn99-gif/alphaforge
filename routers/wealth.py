import concurrent.futures
from fastapi import APIRouter
import yfinance as yf
import pandas as pd
import numpy as np

router = APIRouter(prefix="/wealth", tags=["Gestão de Patrimônio & Fundos"])

# Cestas de Ativos Monitorados
FIIS_TIJOLO = ["HGLG11", "BTLG11", "XPML11", "VISC11", "ALZR11", "KNRI11", "VILG11", "MALL11", "PVBI11", "BRCR11"]
FIIS_PAPEL  = ["KNIP11", "KNCR11", "IRDM11", "CPTS11", "MXRF11", "HGCR11", "MCCI11", "RECR11", "CVBI11", "VRTA11"]
ETFS_B3     = ["BOVA11", "IVVB11", "SMAL11", "NASD11", "HASH11", "DIVO11", "SPXI11", "GOLD11", "XINA11", "BINA11"]

def extrair_dados_fundo(ticker, tipo="FII"):
    try:
        tk = yf.Ticker(f"{ticker}.SA")
        info = tk.info
        preco = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
        
        if preco == 0.0:
            # Fallback caso o Yahoo não retorne o preço no info
            hist = tk.history(period="5d")
            if not hist.empty:
                preco = float(hist['Close'].iloc[-1])
            else:
                return None

        if tipo == "FII":
            pvp = info.get("priceToBook") or 0.0
            raw_dy = (info.get("dividendYield") or 0.0) * 100
            dy = raw_dy if raw_dy < 100 else raw_dy / 100
            
            # Lógica de Ponto de Entrada para FII (Baseado no VPA - Valor Patrimonial)
            if pvp > 0:
                vpa = preco / pvp
                ponto_entrada = vpa
                if pvp < 1.0:
                    recomendacao = "COMPRA"
                elif pvp <= 1.05:
                    recomendacao = "NEUTRO"
                else:
                    recomendacao = "AGUARDAR"
            else:
                ponto_entrada = preco
                recomendacao = "N/A"

            return {
                "ticker": ticker,
                "preco": round(preco, 2),
                "pvp": round(pvp, 2) if pvp else "-",
                "dy": round(dy, 2) if dy else "-",
                "ponto_entrada": round(ponto_entrada, 2),
                "recomendacao": recomendacao,
                "desconto": True if pvp and 0 < pvp < 1 else False
            }
            
        else: # ETF
            nome = info.get("shortName", ticker)
            
            # Lógica de Ponto de Entrada para ETF (Baseado em Média Móvel 20 dias - Pullback)
            hist = tk.history(period="2mo")
            if not hist.empty and len(hist) >= 20:
                sma20 = float(hist['Close'].rolling(20).mean().iloc[-1])
                ponto_entrada = sma20
                recomendacao = "COMPRA" if preco <= sma20 else "AGUARDAR"
            else:
                ponto_entrada = preco
                recomendacao = "NEUTRO"

            return {
                "ticker": ticker,
                "nome": nome.replace(" FDO INV", "").replace(" FI DE", "").replace(" ISHARES", "").strip()[:15],
                "preco": round(preco, 2),
                "ponto_entrada": round(ponto_entrada, 2),
                "recomendacao": recomendacao
            }
    except Exception:
        return None

@router.get("/fundos")
def radar_fundos():
    """Varre FIIs e ETFs em paralelo gerando recomendações."""
    tijolo, papel, etfs = [], [], []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=30) as executor:
        f_tijolo = {executor.submit(extrair_dados_fundo, t, "FII"): t for t in FIIS_TIJOLO}
        f_papel = {executor.submit(extrair_dados_fundo, t, "FII"): t for t in FIIS_PAPEL}
        f_etfs = {executor.submit(extrair_dados_fundo, t, "ETF"): t for t in ETFS_B3}
        
        for future in concurrent.futures.as_completed(f_tijolo):
            res = future.result()
            if res: tijolo.append(res)
            
        for future in concurrent.futures.as_completed(f_papel):
            res = future.result()
            if res: papel.append(res)

        for future in concurrent.futures.as_completed(f_etfs):
            res = future.result()
            if res: etfs.append(res)

    tijolo.sort(key=lambda x: x['dy'] if isinstance(x['dy'], float) else 0, reverse=True)
    papel.sort(key=lambda x: x['dy'] if isinstance(x['dy'], float) else 0, reverse=True)
    etfs.sort(key=lambda x: x['ticker'])
    
    return {"tijolo": tijolo, "papel": papel, "etfs": etfs}

@router.get("/otimizar-portfolio")
def otimizar_markowitz(tickers: str):
    """
    Otimização de Portfólio via Simulação de Monte Carlo (Markowitz).
    """
    lista_tickers = [t.strip().upper() for t in tickers.split(",")]
    lista_yf = [f"{t}.SA" if not t.endswith(".SA") else t for t in lista_tickers]
    
    if len(lista_yf) < 2:
        return {"erro": "Insira pelo menos 2 ativos separados por vírgula."}

    try:
        dados = yf.download(lista_yf, period="2y", interval="1d", progress=False)['Close']
        if dados.empty:
            return {"erro": "Falha ao baixar histórico dos ativos."}

        dados = dados.ffill().dropna()
        retornos = dados.pct_change().dropna()
        retornos_medios = retornos.mean() * 252
        matriz_covariancia = retornos.cov() * 252

        num_portfolios = 5000
        taxa_livre_risco = 0.1050  # Selic base 10.50%

        resultados = np.zeros((3, num_portfolios))
        pesos_record = []

        for i in range(num_portfolios):
            pesos = np.random.random(len(lista_yf))
            pesos /= np.sum(pesos)
            pesos_record.append(pesos)

            retorno_port = np.sum(retornos_medios * pesos)
            std_dev_port = np.sqrt(np.dot(pesos.T, np.dot(matriz_covariancia, pesos)))
            sharpe_ratio = (retorno_port - taxa_livre_risco) / std_dev_port

            resultados[0,i] = retorno_port
            resultados[1,i] = std_dev_port
            resultados[2,i] = sharpe_ratio

        indice_max_sharpe = np.argmax(resultados[2])
        pesos_otimos = pesos_record[indice_max_sharpe]
        
        alocacao = []
        for i in range(len(lista_yf)):
            ticker_limpo = lista_yf[i].replace(".SA", "")
            alocacao.append({
                "ativo": ticker_limpo,
                "peso_pct": round(pesos_otimos[i] * 100, 2)
            })

        alocacao.sort(key=lambda x: x["peso_pct"], reverse=True)

        return {
            "retorno_esperado_aa": round(resultados[0, indice_max_sharpe] * 100, 2),
            "volatilidade_aa": round(resultados[1, indice_max_sharpe] * 100, 2),
            "sharpe_ratio": round(resultados[2, indice_max_sharpe], 2),
            "alocacao_otima": alocacao
        }
    except Exception as e:
        return {"erro": f"Erro matemático: {str(e)}"}