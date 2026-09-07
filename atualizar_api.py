codigo = '''from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import yfinance as yf
import pandas_ta as ta
import pandas as pd
import numpy as np
import requests
from statsmodels.tsa.stattools import adfuller
import joblib
import urllib3
import mplfinance as mpf
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="Alphaforge Quantamental & Trade Engine")

PASTA_GRAFICOS = "graficos"
os.makedirs(PASTA_GRAFICOS, exist_ok=True)
app.mount("/graficos", StaticFiles(directory=PASTA_GRAFICOS), name="graficos")

try:
    modelo = joblib.load('modelo_alphaforge.pkl')
except Exception:
    modelo = None

ACOES_IBOV = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "BBAS3", "WEGE3", "ABEV3", "RENT3", "B3SA3", "PRIO3",
    "SUZB3", "GGBR4", "CSNA3", "JBSS3", "KLBN11", "EQTL3", "CPLE6", "SBSP3", "EMBR3", "RDOR3",
    "RADL3", "LREN3", "MGLU3", "VBBR3", "HAPV3", "CCRO3", "VIVT3", "CPFE3", "ENGI11", "CMIG4",
    "ELET3", "ELET6", "BBSE3", "CXSE3", "TIMS3", "ALOS3", "MULT3", "CYRE3", "EZTC3", "MRVE3",
    "COGN3", "YDUQ3", "RAIL3", "CSAN3", "BEEF3", "MRFG3", "BRFS3", "SMTO3", "SLCE3", "DXCO3",
    "USIM5", "GOAU4", "TOTS3", "POSI3", "LWSA3", "RECV3", "BRAV3", "AZZA3", "UGPA3", "ASAI3",
    "CRFB3", "VIVA3", "ARZZ3", "FLRY3", "CVCB3", "PCAR3", "QUAL3", "IRBR3", "BRAP4", "SANB11"
]

def calcular_adf(serie):
    try:
        return float(adfuller(serie, autolag='AIC')[1])
    except:
        return 0.5

def extrair_fundamentos(ticker_sa: str) -> dict:
    try:
        t = yf.Ticker(ticker_sa)
        info = t.info
        if not info or len(info) < 5:
            return {"status": "Indisponivel"}
            
        pl = info.get('trailingPE', None)
        pvp = info.get('priceToBook', None)
        dy = info.get('dividendYield', None)
        roe = info.get('returnOnEquity', None)
        margem_liq = info.get('profitMargins', None)
        ev_ebitda = info.get('enterpriseToEbitda', None)
        setor = info.get('sector', 'Nao Informado')
        
        score = 5.0
        racional_fund = []
        
        if pl is not None and pl > 0:
            if pl < 10:
                score += 1.5
                racional_fund.append(f"P/L muito atrativo ({pl:.1f}x), empresa barata.")
            elif pl > 25:
                score -= 1.0
                racional_fund.append(f"P/L esticado ({pl:.1f}x), alto multiplo.")
        elif pl is not None and pl < 0:
            score -= 2.0
            racional_fund.append("Empresa reportando prejuizo liquido recente.")
            
        if pvp is not None:
            if 0 < pvp <= 1.5:
                score += 1.0
                racional_fund.append(f"P/VP descontado ({pvp:.2f}x).")
            elif pvp > 4.0:
                score -= 0.5
                racional_fund.append(f"P/VP elevado ({pvp:.2f}x).")
                
        if dy is not None and dy > 0:
            dy_pct = dy * 100 if dy < 1.0 else dy
            if dy_pct >= 6.0:
                score += 1.5
                racional_fund.append(f"Forte pagadora de dividendos (DY: {dy_pct:.2f}% a.a.).")
            elif dy_pct >= 3.0:
                score += 0.5
                
        if roe is not None:
            roe_pct = roe * 100 if roe < 1.0 else roe
            if roe_pct >= 15.0:
                score += 1.0
                racional_fund.append(f"ROE elevado ({roe_pct:.1f}%), alta rentabilidade sobre o capital.")
            elif roe_pct < 5.0:
                score -= 1.0
                racional_fund.append(f"ROE baixo ({roe_pct:.1f}%), pouca eficiencia.")
                
        if margem_liq is not None:
            margem_pct = margem_liq * 100 if margem_liq < 1.0 else margem_liq
            if margem_pct >= 15.0:
                score += 0.5
                racional_fund.append(f"Margem liquida saudavel ({margem_pct:.1f}%).")
            elif margem_pct < 0:
                score -= 1.0
                
        score = max(0.0, min(10.0, score))
        
        if score >= 7.5:
            qualidade = "Excelente (Solida e Barata)"
        elif score >= 5.5:
            qualidade = "Boa / Neutra"
        else:
            qualidade = "Fraca / Risco Fundamentalista"
            
        return {
            "status": "Disponivel",
            "setor": setor,
            "score_fundamentalista": round(score, 1),
            "qualidade": qualidade,
            "multiplos": {
                "pl": f"{pl:.2f}x" if pl else "N/A",
                "pvp": f"{pvp:.2f}x" if pvp else "N/A",
                "dividend_yield": f"{dy*100:.2f}%" if dy and dy < 1 else (f"{dy:.2f}%" if dy else "0.00%"),
                "roe": f"{roe*100:.2f}%" if roe and roe < 1 else (f"{roe:.2f}%" if roe else "N/A"),
                "margem_liquida": f"{margem_liq*100:.2f}%" if margem_liq and margem_liq < 1 else (f"{margem_liq:.2f}%" if margem_liq else "N/A"),
                "ev_ebitda": f"{ev_ebitda:.2f}x" if ev_ebitda else "N/A"
            },
            "destaques": racional_fund
        }
    except Exception as e:
        return {"status": "Erro", "detalhe": str(e)}

def obter_dados_binance(symbol: str, limit: int = 365) -> pd.DataFrame:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1d&limit={limit}"
    resposta = requests.get(url, timeout=10, verify=False)
    dados = resposta.json()
    
    df = pd.DataFrame(dados, columns=[
        'Open_time', 'Open', 'High', 'Low', 'Close', 'Volume',
        'Close_time', 'Quote_asset_volume', 'Number_of_trades',
        'Taker_buy_base', 'Taker_buy_quote', 'Ignore'
    ])
    df['Date'] = pd.to_datetime(df['Open_time'], unit='ms')
    df.set_index('Date', inplace=True)
    for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[col] = df[col].astype(float)
    return df[['Open', 'High', 'Low', 'Close', 'Volume']]

def extrair_features(df: pd.DataFrame):
    if df['Volume'].iloc[-1] == 0 and len(df) > 50:
        df = df.iloc[:-1].copy()

    df['EMA_9'] = ta.ema(df['Close'], length=9)
    df['EMA_21'] = ta.ema(df['Close'], length=21)
    df['DIST_EMA9'] = (df['Close'] - df['EMA_9']) / df['EMA_9']
    df['DIST_EMA21'] = (df['Close'] - df['EMA_21']) / df['EMA_21']
    df['DIST_EMAS'] = (df['EMA_9'] - df['EMA_21']) / df['EMA_21']
    
    df['RSI_14'] = ta.rsi(df['Close'], length=14) / 100.0
    macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
    df['MACD_REL'] = macd['MACD_12_26_9'] / df['Close']
    
    adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
    df['ADX_14'] = adx_df['ADX_14'] / 100.0
    df['DMP_14'] = adx_df['DMP_14'] / 100.0
    df['DMN_14'] = adx_df['DMN_14'] / 100.0
    
    donchian = ta.donchian(df['High'], df['Low'], lower_length=20, upper_length=20)
    df['DCL_20'] = donchian['DCL_20_20']
    df['DCU_20'] = donchian['DCU_20_20']
    amplitude = (df['DCU_20'] - df['DCL_20']).replace(0, np.nan)
    df['DONCHIAN_POS'] = (df['Close'] - df['DCL_20']) / amplitude
    
    media_vol = df['Volume'].rolling(window=20).mean().replace(0, np.nan)
    df['VOL_REL'] = (df['Volume'] / media_vol).fillna(1.0)
    
    df['ADF_PVAL'] = df['Close'].rolling(window=30).apply(calcular_adf, raw=False)
    
    atr_df = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    df['ATR_VALOR'] = atr_df
    df['ATR_REL'] = atr_df / df['Close']
    
    df['RET_1D'] = df['Close'].pct_change(1)
    df['RET_5D'] = df['Close'].pct_change(5)
    
    return df.dropna()

def gerar_grafico_tecnico(df: pd.DataFrame, ticker: str) -> str:
    caminho_imagem = os.path.join(PASTA_GRAFICOS, f"{ticker}.png")
    df_plot = df.iloc[-60:].copy()
    
    adicionais = [
        mpf.make_addplot(df_plot['EMA_9'], color='cyan', width=1.0, panel=0),
        mpf.make_addplot(df_plot['EMA_21'], color='orange', width=1.0, panel=0),
        mpf.make_addplot(df_plot['DCU_20'], color='green', linestyle='--', width=0.8, panel=0),
        mpf.make_addplot(df_plot['DCL_20'], color='red', linestyle='--', width=0.8, panel=0),
        mpf.make_addplot(df_plot['RSI_14'] * 100, color='magenta', panel=2, ylabel='RSI'),
        mpf.make_addplot(df_plot['ADX_14'] * 100, color='yellow', panel=3, ylabel='ADX')
    ]
    
    estilo = mpf.make_mpf_style(base_mpf_style='nightclouds', rc={'font.size': 8})
    
    mpf.plot(
        df_plot,
        type='candle',
        volume=True,
        addplot=adicionais,
        style=estilo,
        title=f"Alphaforge Chart: {ticker}",
        savefig=dict(fname=caminho_imagem, dpi=120, bbox_inches='tight'),
        panel_ratios=(4, 1, 1, 1),
        figsize=(10, 8)
    )
    return f"/graficos/{ticker}.png"

def calcular_plano_de_trade(preco_atual: float, atr: float, sinal: str) -> dict:
    distancia_risco = 1.5 * atr
    percentual_risco = (distancia_risco / preco_atual) * 100
    
    if sinal == "COMPRA FORTE":
        stop_loss = preco_atual - distancia_risco
        alvo_1 = preco_atual + (distancia_risco * 1.5)
        alvo_2 = preco_atual + (distancia_risco * 3.0)
        direcao = "COMPRA"
    elif sinal in ["VENDA", "VENDA / DESCARTE"]:
        stop_loss = preco_atual + distancia_risco
        alvo_1 = preco_atual - (distancia_risco * 1.5)
        alvo_2 = preco_atual - (distancia_risco * 3.0)
        direcao = "VENDA / PROTECAO"
    else:
        return {
            "status": "Aguardando Conviccao",
            "mensagem": "Ativo em zona neutra. Nao ha plano de trade de alta assimetria no momento."
        }
        
    return {
        "status": "Ativo",
        "operacao": direcao,
        "preco_entrada": round(preco_atual, 2),
        "stop_loss": round(stop_loss, 2),
        "alvo_1_parcial": round(alvo_1, 2),
        "alvo_2_final": round(alvo_2, 2),
        "risco_estimado_pct": f"{percentual_risco:.2f}%",
        "relacao_risco_retorno": "1 : 2.0 (Parcial 1:1.5 | Final 1:3.0)",
        "volatilidade_atr": round(atr, 2),
        "estrategia_execucao": f"Ao atingir o Alvo 1 ({round(alvo_1, 2)}), realizar 50% do lote e mover o Stop Loss para o ponto de entrada ({round(preco_atual, 2)})."
    }

@app.get("/")
def ping():
    return {"status": "Alphaforge Quant & Trade Engine Operacional"}

@app.get("/analisar/{ativo}")
def analisar_ativo(ativo: str):
    if not modelo:
        return {"erro": "Modelo nao encontrado. Execute treinar_modelo.py primeiro."}
    
    ticker_input = ativo.upper().strip()
    try:
        eh_cripto = ticker_input.endswith("USDT") or ticker_input in ["BTC", "ETH", "SOL", "BNB", "ADA", "XRP"]
        
        if eh_cripto:
            symbol = ticker_input if ticker_input.endswith("USDT") else f"{ticker_input}USDT"
            df = obter_dados_binance(symbol, limit=365)
            ticker_exibicao = symbol
            origem = "Binance API Oficial"
            fundamentos = {"tipo": "Criptoativo", "mensagem": "Nao se aplica analise contabil fundamentalista."}
        else:
            ticker_b3 = ticker_input if ticker_input.endswith(".SA") else f"{ticker_input}.SA"
            ticker = yf.Ticker(ticker_b3)
            df = ticker.history(period="1y")
            ticker_exibicao = ticker_b3
            origem = "B3 / Yahoo"
            fundamentos = extrair_fundamentos(ticker_b3)
            
        if len(df) < 50:
            return {"erro": f"Historico insuficiente para {ticker_exibicao}."}
        
        df = extrair_features(df)
        ultimo = df.iloc[-1]
        
        nome_arquivo = ticker_exibicao.replace(".SA", "")
        url_grafico = gerar_grafico_tecnico(df, nome_arquivo)
        
        features_input = pd.DataFrame([{
            'DIST_EMA9': ultimo['DIST_EMA9'], 'DIST_EMA21': ultimo['DIST_EMA21'], 'DIST_EMAS': ultimo['DIST_EMAS'],
            'RSI_14': ultimo['RSI_14'], 'MACD_REL': ultimo['MACD_REL'], 'ADX_14': ultimo['ADX_14'],
            'DMP_14': ultimo['DMP_14'], 'DMN_14': ultimo['DMN_14'], 'DONCHIAN_POS': ultimo['DONCHIAN_POS'],
            'VOL_REL': ultimo['VOL_REL'], 'ADF_PVAL': ultimo['ADF_PVAL'], 'ATR_REL': ultimo['ATR_REL'],
            'RET_1D': ultimo['RET_1D'], 'RET_5D': ultimo['RET_5D']
        }])
        
        probabilidade_alta = float(modelo.predict_proba(features_input)[0][1])
        rsi_val = ultimo['RSI_14'] * 100
        adx_val = ultimo['ADX_14'] * 100
        pos_donchian = ultimo['DONCHIAN_POS'] * 100
        preco_fechamento = float(ultimo['Close'])
        atr_valor = float(ultimo['ATR_VALOR'])
        
        racional = []
        if rsi_val > 70: racional.append(f"RSI sobrecomprado ({rsi_val:.1f}), risco de correcao.")
        elif rsi_val < 30: racional.append(f"RSI sobrevendido ({rsi_val:.1f}), potencial repique.")
        if adx_val > 30: racional.append(f"Tendencia direcional forte (ADX: {adx_val:.1f}).")
        else: racional.append(f"Mercado em consolidacao (ADX: {adx_val:.1f}).")
        if pos_donchian >= 85: racional.append("Preco testando topo do Canal de Donchian.")
        elif pos_donchian <= 15: racional.append("Preco testando fundo do Canal de Donchian.")
        
        if probabilidade_alta >= 0.60:
            sinal = "COMPRA FORTE"
            acao = "Montar posicao visando alvos quantitativos de 5 dias."
        elif probabilidade_alta <= 0.35:
            sinal = "VENDA / DESCARTE"
            acao = "Evitar compras ou liquidar; probabilidade de queda."
        else:
            sinal = "NEUTRO"
            acao = "Aguardar assimetria estatistica."
            
        plano_trade = calcular_plano_de_trade(preco_fechamento, atr_valor, sinal)
            
        return {
            "ativo": ticker_exibicao,
            "origem_dados": origem,
            "preco_atual": round(preco_fechamento, 2),
            "probabilidade_alta": f"{probabilidade_alta * 100:.2f}%",
            "sinal": sinal,
            "recomendacao_acao": acao,
            "grafico_url": f"http://127.0.0.1:8000{url_grafico}",
            "plano_de_trade_quantitativo": plano_trade,
            "analise_tecnica": {
                "racional": racional,
                "rsi": round(rsi_val, 2),
                "adx": round(adx_val, 2),
                "posicao_donchian": f"{pos_donchian:.1f}%",
                "volume_relativo": f"{float(ultimo['VOL_REL']):.2f}x",
                "regime_adf": "Reversao a Media" if ultimo['ADF_PVAL'] < 0.05 else "Em Tendencia"
            },
            "analise_fundamentalista": fundamentos
        }
    except Exception as e:
        return {"erro": f"Falha na analise: {str(e)}"}

def escanear_um_ativo(ticker: str):
    try:
        ticker_sa = f"{ticker}.SA"
        t = yf.Ticker(ticker_sa)
        df = t.history(period="6mo")
        if len(df) < 50:
            return None
        
        df = extrair_features(df)
        ultimo = df.iloc[-1]
        
        features_input = pd.DataFrame([{
            'DIST_EMA9': ultimo['DIST_EMA9'], 'DIST_EMA21': ultimo['DIST_EMA21'], 'DIST_EMAS': ultimo['DIST_EMAS'],
            'RSI_14': ultimo['RSI_14'], 'MACD_REL': ultimo['MACD_REL'], 'ADX_14': ultimo['ADX_14'],
            'DMP_14': ultimo['DMP_14'], 'DMN_14': ultimo['DMN_14'], 'DONCHIAN_POS': ultimo['DONCHIAN_POS'],
            'VOL_REL': ultimo['VOL_REL'], 'ADF_PVAL': ultimo['ADF_PVAL'], 'ATR_REL': ultimo['ATR_REL'],
            'RET_1D': ultimo['RET_1D'], 'RET_5D': ultimo['RET_5D']
        }])
        
        prob = float(modelo.predict_proba(features_input)[0][1])
        preco = float(ultimo['Close'])
        atr = float(ultimo['ATR_VALOR'])
        
        if prob >= 0.60:
            sinal = "COMPRA FORTE"
            stop = round(preco - (1.5 * atr), 2)
            alvo = round(preco + (3.0 * atr), 2)
        elif prob <= 0.35:
            sinal = "VENDA"
            stop = round(preco + (1.5 * atr), 2)
            alvo = round(preco - (3.0 * atr), 2)
        else:
            sinal = "NEUTRO"
            stop = None
            alvo = None
            
        return {
            "ticker": ticker,
            "preco": round(preco, 2),
            "probabilidade_alta": round(prob * 100, 2),
            "sinal": sinal,
            "stop_sugerido": stop,
            "alvo_sugerido": alvo,
            "rsi": round(float(ultimo['RSI_14'] * 100), 1),
            "adx": round(float(ultimo['ADX_14'] * 100), 1),
            "volume_rel": round(float(ultimo['VOL_REL']), 2)
        }
    except Exception:
        return None

@app.get("/scanner/ibov")
def scanner_ibovespa():
    if not modelo:
        return {"erro": "Modelo nao carregado."}
    
    resultados = []
    with ThreadPoolExecutor(max_workers=12) as executor:
        futuros = [executor.submit(escanear_um_ativo, ticker) for ticker in ACOES_IBOV]
        for f in as_completed(futuros):
            res = f.result()
            if res:
                resultados.append(res)
                
    resultados = sorted(resultados, key=lambda x: x['probabilidade_alta'], reverse=True)
    
    compras = [r for r in resultados if r['sinal'] == "COMPRA FORTE"]
    vendas = [r for r in resultados if r['sinal'] == "VENDA"]
    
    return {
        "total_ativos_escaner": len(resultados),
        "total_compras_fortes": len(compras),
        "total_vendas": len(vendas),
        "top_oportunidades_compra": compras[:5],
        "top_riscos_venda": vendas[-5:] if vendas else [],
        "todos_ativos_ranqueados": resultados
    }
'''

with open('api.py', 'w', encoding='utf-8') as f:
    f.write(codigo)
print(">>> API ATUALIZADA: PLANO DE TRADE QUANTITATIVO (ATR) INTEGRADO! <<<")