import concurrent.futures
from fastapi import APIRouter
import yfinance as yf
import pandas as pd
import numpy as np

router = APIRouter(prefix="/renda-variavel", tags=["Renda Variável & Ações"])

TICKER_ALIASES = {
    "RRRP3": "BRAV3",
    "EMBJ3": "EMBR3",
    "AXIA3": "ELET3"
}

UNIVERSO_100_ACOES = [
    "VALE3", "PETR4", "ITUB4", "PETR3", "PRIO3", "BBDC4", "BBAS3", "B3SA3", "BPAC11", "AXIA3",
    "ITUB3", "ELET3", "ELET6", "ABEV3", "WEGE3", "RENT3", "JBSS3", "SUZB3", "SANB11", "EQTL3",
    "ITSA4", "ITSA3", "BBSE3", "RDOR3", "EMBJ3", "VIVT3", "RADL3", "RAIL3", "KLBN11", "GGBR4",
    "GGBR3", "CSNA3", "CPLE6", "CPLE3", "CMIG4", "CMIG3", "ENGI11", "TAEE11", "CPFE3", "SBSP3",
    "VBBR3", "UGPA3", "CSAN3", "AURE3", "ENEV3", "EGIE3", "SAPR4", "SAPR3", "CSMG3", "TOTS3",
    "MGLU3", "LREN3", "ASAI3", "CRFB3", "GMAT3", "AZZA3", "VIVA3", "CEAB3", "ALPA4", "CYRE3",
    "MRVE3", "DIRR3", "CURY3", "EVEN3", "EZTC3", "MULT3", "ALOS3", "IGTI11", "MOVI3", "SIMH3",
    "VAMO3", "CCRO3", "ECOR3", "JSLG3", "GGPS3", "CMIN3", "USIM5", "SLCE3", "SMTO3", "MRFG3",
    "BRFS3", "BEEF3", "RECV3", "RRRP3", "IRBR3", "PSSA3", "CXSE3", "ABCB4", "BPAN4", "BRSR6",
    "POMO4", "TUPY3", "GOAU4", "ALUP11", "STBP3", "JHSF3"
]

def calcular_score_quantamental(pl, pvp, roe, dy, margem_liq, tendencia_grafica, rsi_val, destruicao_historica=False):
    """Motor de Decisão ÚNICO. Garante que Scanner e Auditoria Individual tenham a mesma recomendação."""
    score = 50
    alertas_risco = []
    pontos_positivos = []

    em_prejuizo = margem_liq < 0 or roe <= 0
    if margem_liq < 0: alertas_risco.append(f"Margem Líquida negativa ({margem_liq:.2f}%).")
    if roe <= 0: alertas_risco.append("ROE zerado ou negativo.")
    if destruicao_historica: alertas_risco.append("Destruição contínua de capital recente (Value Trap).")

    # Múltiplos
    if 0 < pl < 12 and not em_prejuizo: 
        score += 15
        pontos_positivos.append(f"P/L atrativo ({pl:.1f}x)")
    elif pl > 25: 
        score -= 10
        alertas_risco.append(f"P/L elevado ({pl:.1f}x)")
        
    if 0 < pvp < 1.8 and not em_prejuizo:
        score += 10
        pontos_positivos.append(f"P/VP descontado ({pvp:.2f}x)")
        
    if roe >= 15:
        score += 15
        pontos_positivos.append(f"Alta rentabilidade (ROE {roe:.1f}%)")
        
    if dy >= 6 and not em_prejuizo:
        score += 10
        pontos_positivos.append(f"Bons dividendos ({dy:.1f}%)")

    # Técnico / Gráfico
    if tendencia_grafica == "ALTA":
        score += 10
        pontos_positivos.append("Tendência Gráfica de ALTA (Preço > SMA50)")
    else:
        score -= 5
        alertas_risco.append("Tendência Gráfica de BAIXA (Preço < SMA50)")

    if rsi_val < 35:
        score += 10
        pontos_positivos.append(f"Sobrevenda (RSI {rsi_val}) - Possível ponto de entrada")
    elif rsi_val > 70:
        score -= 10
        alertas_risco.append(f"Sobrecompra (RSI {rsi_val}) - Papel esticado")

    # Limites
    score = max(5, min(98, score))
    if em_prejuizo or destruicao_historica:
        score = min(score, 35)

    # Classificação
    if score >= 75: veredito = "COMPRA FORTE"
    elif score >= 60: veredito = "COMPRA"
    elif score <= 40: veredito = "VENDA"
    else: veredito = "NEUTRO"

    if em_prejuizo or destruicao_historica: veredito = "VENDA / ALTO RISCO"

    return score, veredito, pontos_positivos, alertas_risco

def extrair_fundamentos_seguro(ticker_yf):
    try:
        tk = yf.Ticker(ticker_yf)
        info = tk.info
        if not info or 'symbol' not in info:
            return None
            
        raw_dy = (info.get("dividendYield") or 0.0) * 100
        dy = raw_dy if raw_dy < 100 else raw_dy / 100

        return {
            "pl": info.get("trailingPE") or 0.0,
            "pvp": info.get("priceToBook") or 0.0,
            "roe": (info.get("returnOnEquity") or 0.0) * 100,
            "margem_liq": (info.get("profitMargins") or 0.0) * 100,
            "dy": dy,
            "nome": info.get("shortName", ticker_yf.replace(".SA", ""))
        }
    except:
        return None

@router.get("/scanner-quantamental")
def executar_scanner():
    """Varre as 100 ações protegendo contra Rate Limits da API do Yahoo."""
    tickers_unicos = list(dict.fromkeys(UNIVERSO_100_ACOES))
    tickers_yf_map = {t: f"{TICKER_ALIASES.get(t, t)}.SA" for t in tickers_unicos}
    lista_yf = list(tickers_yf_map.values())
    
    try:
        # Download VETORIZADO (1 requisição única puxa todos os preços)
        df_prices = yf.download(lista_yf, period="6mo", interval="1d", progress=False, group_by='ticker')
        
        # Puxa os múltiplos paralelamente sem quebrar o limite da API
        info_data = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
            futures = {executor.submit(extrair_fundamentos_seguro, tyf): tyf for tyf in lista_yf}
            for future in concurrent.futures.as_completed(futures):
                tyf = futures[future]
                res = future.result()
                if res: info_data[tyf] = res

        resultados = []
        for ticker_orig, ticker_yf in tickers_yf_map.items():
            if ticker_yf not in info_data:
                continue
                
            try:
                # Trata estrutura do dataframe de preços
                if len(lista_yf) > 1 and ticker_yf in df_prices.columns.levels[0]:
                    sub_df = df_prices[ticker_yf].dropna()
                else:
                    sub_df = df_prices.dropna()

                if len(sub_df) < 20: continue

                preco = float(sub_df['Close'].iloc[-1])
                sma50 = float(sub_df['Close'].rolling(50).mean().iloc[-1]) if len(sub_df) >= 50 else preco
                
                delta = sub_df['Close'].diff()
                gain = (delta.where(delta > 0, 0)).rolling(14).mean()
                loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
                rs = gain / loss
                rsi_series = 100 - (100 / (1 + rs))
                rsi_val = round(float(rsi_series.iloc[-1]), 1) if not pd.isna(rsi_series.iloc[-1]) else 50.0

                tendencia = "ALTA" if preco > sma50 else "BAIXA"
                info = info_data[ticker_yf]

                score, veredito, _, _ = calcular_score_quantamental(
                    pl=info["pl"], pvp=info["pvp"], roe=info["roe"], dy=info["dy"], 
                    margem_liq=info["margem_liq"], tendencia_grafica=tendencia, rsi_val=rsi_val
                )

                resultados.append({
                    "ticker": ticker_orig,
                    "nome": info["nome"],
                    "preco": round(preco, 2),
                    "pl": round(info["pl"], 2) if info["pl"] else None,
                    "pvp": round(info["pvp"], 2) if info["pvp"] else None,
                    "roe": round(info["roe"], 2),
                    "dy": round(info["dy"], 2),
                    "tendencia_grafica": tendencia,
                    "rsi": rsi_val,
                    "score_geral": score,
                    "veredito": veredito
                })
            except Exception:
                continue

        resultados.sort(key=lambda x: x["score_geral"], reverse=True)
        return {"total": len(resultados), "oportunidades": resultados}
    except Exception as e:
        return {"erro": str(e), "total": 0, "oportunidades": []}

@router.get("/acao/{ticker}")
def auditar_acao(ticker: str):
    ticker_clean = ticker.upper().strip()
    ticker_consulta = TICKER_ALIASES.get(ticker_clean, ticker_clean)
    ticker_yf = ticker_consulta if ticker_consulta.endswith(".SA") else f"{ticker_consulta}.SA"
    
    try:
        ativo = yf.Ticker(ticker_yf)
        info = ativo.info
        
        preco = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
        pl = info.get("trailingPE") or 0.0
        pvp = info.get("priceToBook") or 0.0
        ev_ebitda = info.get("enterpriseToEbitda") or 0.0
        roe = (info.get("returnOnEquity") or 0.0) * 100
        margem_liq = (info.get("profitMargins") or 0.0) * 100
        
        raw_dy = (info.get("dividendYield") or 0.0) * 100
        dy = raw_dy if raw_dy < 100 else raw_dy / 100

        # Histórico pesado de 10 Anos (somente puxado na análise individual)
        historico = ativo.history(period="10y", interval="1d")
        if historico.empty: return {"erro": "Sem dados históricos disponíveis."}

        if preco == 0.0: preco = float(historico['Close'].iloc[-1])

        dados_10_anos = []
        retornos_anuais = []
        historico['Ano'] = historico.index.year
        anual = historico.groupby('Ano')['Close'].agg(['first', 'last', 'max', 'min'])
        
        for ano, row in anual.iterrows():
            variacao = ((row['last'] - row['first']) / row['first']) * 100
            retornos_anuais.append(variacao)
            dados_10_anos.append({
                "ano": int(ano),
                "fechamento": round(float(row['last']), 2),
                "maxima": round(float(row['max']), 2),
                "minima": round(float(row['min']), 2),
                "retorno_ano_pct": round(float(variacao), 2)
            })

        historico['SMA50'] = historico['Close'].rolling(window=50).mean()
        delta = historico['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        historico['RSI'] = 100 - (100 / (1 + rs))
        
        sma50 = float(historico['SMA50'].iloc[-1]) if len(historico) >= 50 else preco
        rsi_val = round(float(historico['RSI'].iloc[-1]), 1) if not pd.isna(historico['RSI'].iloc[-1]) else 50.0
        tendencia = "ALTA" if preco > sma50 else "BAIXA"

        destruicao_historica = len(retornos_anuais) >= 3 and np.mean(retornos_anuais[-3:]) < -30

        # Usa O EXATO MESMO MOTOR do Scanner
        score, veredito, pontos_positivos, alertas_risco = calcular_score_quantamental(
            pl, pvp, roe, dy, margem_liq, tendencia, rsi_val, destruicao_historica
        )
        
        racional_final = pontos_positivos + alertas_risco

        return {
            "ticker": ticker_clean,
            "nome_empresa": info.get("shortName", ticker_clean),
            "setor": info.get("sector", "N/A"),
            "preco_atual": round(preco, 2),
            "recomendacao": veredito,
            "analise_racional": racional_final,
            "multiplos": {
                "p_l": round(pl, 2) if pl else None,
                "p_vp": round(pvp, 2) if pvp else None,
                "ev_ebitda": round(ev_ebitda, 2) if ev_ebitda else None,
                "dividend_yield_pct": round(dy, 2),
                "roe_pct": round(roe, 2),
                "margem_liquida_pct": round(margem_liq, 2)
            },
            "historico_10_anos": dados_10_anos
        }
    except Exception as e:
        return {"erro": f"Erro ao processar ativo: {str(e)}"}
@router.get("/ticker-tape")
def get_ticker_tape():
    """Busca cotações em tempo real para a barra de rolagem (Ticker Tape)"""
    # Principais termômetros do mercado
    tickers = ["^BVSP", "USDBRL=X", "PETR4.SA", "VALE3.SA", "ITUB4.SA", "WEGE3.SA", "BBDC4.SA", "BBAS3.SA", "ELET3.SA", "RENT3.SA"]
    
    try:
        # Puxa 5 dias para garantir que teremos o fechamento de ontem e o de hoje
        dados = yf.download(tickers, period="5d", interval="1d", progress=False)['Close']
        
        resultados = []
        for t in tickers:
            try:
                # Limpa os dados vazios (feriados/fds) e pega os dois últimos dias úteis
                validos = dados[t].dropna()
                if len(validos) >= 2:
                    preco_atual = float(validos.iloc[-1])
                    preco_anterior = float(validos.iloc[-2])
                    
                    variacao = ((preco_atual - preco_anterior) / preco_anterior) * 100
                    
                    # Nomes limpos para a tela
                    nome = t.replace(".SA", "").replace("^BVSP", "IBOV").replace("USDBRL=X", "USD/BRL")
                    
                    resultados.append({
                        "ativo": nome,
                        "preco": preco_atual,
                        "variacao": round(variacao, 2)
                    })
            except:
                continue

        # Trecho de cálculo e tratamento defensivo para ROE e Valuation

info = ativo.info

# 1. Tratamento seguro de ROE (extrai ou calcula via Lucro Líquido / Patrimônio Líquido)
roe_bruto = info.get("returnOnEquity")
if roe_bruto is not None and roe_bruto != 0:
    roe = round(float(roe_bruto) * 100, 2)
else:
    # Fallback: calcula ROE manualmente se houver lucro e patrimônio líquido
    net_income = info.get("netIncomeToCommon") or 0
    total_equity = info.get("totalStockholderEquity") or 1
    if total_equity > 0 and net_income != 0:
        roe = round((net_income / total_equity) * 100, 2)
    else:
        roe = 0.0

# 2. Tratamento seguro de Preço Justo e Graham (não travar se algum múltiplo falhar)
lpa = info.get("trailingEps") or 0.0
vpa = info.get("bookValue") or 0.0
preco_atual = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0

preco_graham = None
desconto_graham = None

if lpa > 0 and vpa > 0:
    try:
        preco_graham = round(float(np.sqrt(22.5 * lpa * vpa)), 2)
        if preco_atual > 0:
            desconto_graham = round(((preco_graham - preco_atual) / preco_graham) * 100, 2)
    except Exception:
        preco_graham = None

# 3. Motor de Recomendação Robusto (não trava mesmo se o ROE for neutro/negativo)
pvp = info.get("priceToBook") or 1.0
pl = info.get("trailingPE") or 0.0

if preco_atual <= 0:
    recomendacao = "DADOS INDISPONÍVEIS"
elif desconto_graham is not None and desconto_graham > 15:
    recomendacao = "COMPRA FORTE (Subavaliado por Graham)"
elif pvp < 0.90 and roe > 5.0:
    recomendacao = "COMPRA (Desconto Patrimonial com Rentabilidade)"
elif pvp < 0.80:
    recomendacao = "COMPRA ESPECULATIVA (Forte Desconto P/VP)"
elif pl > 15.0 or pvp > 2.0:
    recomendacao = "REALIZAR / NEUTRO (Múltiplos Esticados)"
else:
    recomendacao = "MANTER (Preço de Equilíbrio)"
        return resultados
    except Exception as e:
        return []
