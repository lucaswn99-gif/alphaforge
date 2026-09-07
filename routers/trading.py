from fastapi import APIRouter, Query
from pydantic import BaseModel
import ccxt
import pandas as pd

router = APIRouter(prefix="/trading", tags=["Robô de Trading & Execução"])

# Instância Binance (Custo Zero / Sem autenticação obrigatória para leitura)
binance = ccxt.binance({'enableRateLimit': True})

class OrdemSimulada(BaseModel):
    mercado: str  # "B3" ou "BINANCE"
    ativo: str    # Ex: "PETR4" ou "BTC/USDT"
    quantidade: float
    tipo: str     # "COMPRA" ou "VENDA"

# ==========================================
# 1. MERCADO B3 / BRASIL (METATRADER 5)
# ==========================================

@router.get("/b3/status-mt5")
def status_metatrader():
    """
    Checa se o terminal MetaTrader 5 da sua corretora (XP, BTG, Genial, etc.) está ativo.
    """
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            info = mt5.terminal_info()
            conta = mt5.account_info()
            mt5.shutdown()
            return {
                "status": "CONECTADO",
                "corretora_servidor": info.server if info else "N/A",
                "login_ativo": conta.login if conta else "Demo/Não logado",
                "ambiente": "B3 (Ações, Futuros, Opções)"
            }
        return {
            "status": "DESCONECTADO",
            "erro": "MetaTrader 5 não está aberto no Windows."
        }
    except ImportError:
        return {
            "status": "MODO_SIMULADO",
            "aviso": "Biblioteca MetaTrader5 não instalada no ambiente Python."
        }

@router.get("/b3/cotacao/{ticker}")
def cotacao_b3(ticker: str = "PETR4"):
    """
    Obtém cotação tick a tick ao vivo da B3 via MetaTrader 5.
    """
    ticker_clean = ticker.upper().replace(".SA", "")
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            tick = mt5.symbol_info_tick(ticker_clean)
            mt5.shutdown()
            if tick:
                return {
                    "mercado": "B3",
                    "ativo": ticker_clean,
                    "ultimo": tick.last,
                    "compra_bid": tick.bid,
                    "venda_ask": tick.ask,
                    "volume": tick.volume
                }
    except Exception:
        pass
        
    return {"erro": f"Não foi possível obter tick ao vivo de {ticker_clean}. Certifique-se de que o MT5 está aberto."}

@router.get("/b3/analise-tempo-menor")
def analise_b3_tempo_menor(
    ticker: str = Query("PETR4", description="Código do ativo na B3 (ex: PETR4, VALE3, WINFUT)"),
    timeframe: str = Query("M5", description="Tempos: M1, M5, M15, H1, D1"),
    barras: int = Query(50, description="Número de candles")
):
    """
    Calcula Médias Rápidas (EMA 9 e EMA 21) e RSI nos tempos curtos da B3 via MT5.
    """
    ticker_clean = ticker.upper().replace(".SA", "")
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return {"erro": "MetaTrader 5 não inicializado."}
            
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "H1": mt5.TIMEFRAME_H1,
            "D1": mt5.TIMEFRAME_D1
        }
        
        rates = mt5.copy_rates_from_pos(ticker_clean, tf_map.get(timeframe, mt5.TIMEFRAME_M5), 0, barras)
        mt5.shutdown()
        
        if rates is None or len(rates) == 0:
            return {"erro": f"Sem dados para {ticker_clean} no timeframe {timeframe}."}
            
        df = pd.DataFrame(rates)
        df['time'] = pd.to_datetime(df['time'], unit='s')
        
        # Médias Exponenciais
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        # RSI 14
        delta = df['close'].diff()
        ganho = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        perda = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = ganho / perda
        df['rsi'] = 100 - (100 / (1 + rs))
        
        ultima = df.iloc[-1]
        cruzamento = "ALTA" if ultima['ema_9'] > ultima['ema_21'] else "BAIXA"
        
        return {
            "mercado": "B3 (MetaTrader 5)",
            "ativo": ticker_clean,
            "timeframe": timeframe,
            "ultimo_fechamento": round(float(ultima['close']), 2),
            "indicadores": {
                "ema_9": round(float(ultima['ema_9']), 2),
                "ema_21": round(float(ultima['ema_21']), 2),
                "rsi_14": round(float(ultima['rsi']), 2) if not pd.isna(ultima['rsi']) else None,
                "tendencia": cruzamento
            },
            "sinal": "COMPRA" if cruzamento == "ALTA" and ultima['rsi'] < 65 else "AGUARDAR"
        }
    except Exception as e:
        return {"erro": f"Falha na análise MT5: {str(e)}"}

# ==========================================
# 2. MERCADO CRIPTO (BINANCE)
# ==========================================

@router.get("/binance/cotacao/{simbolo}")
def cotacao_binance(simbolo: str = "BTCUSDT"):
    """
    Puxa cotação em tempo real da Binance.
    """
    par = simbolo.upper().replace("/", "")
    try:
        ticker = binance.fetch_ticker(f"{par[:3]}/{par[3:]}" if "/" not in simbolo else simbolo)
        return {
            "mercado": "Binance Cripto",
            "ativo": simbolo.upper(),
            "ultimo_preco": ticker.get("last"),
            "volume_24h": ticker.get("baseVolume"),
            "variacao_24h_pct": ticker.get("percentage")
        }
    except Exception as e:
        return {"erro": f"Falha ao conectar na Binance: {str(e)}"}

@router.get("/binance/analise-tempo-menor")
def analise_binance(
    simbolo: str = Query("BTC/USDT", description="Par (ex: BTC/USDT)"),
    timeframe: str = Query("5m", description="1m, 5m, 15m, 1h"),
    limite: int = Query(50, description="Quantidade de velas")
):
    """
    Análise intradiária em tempo real na Binance.
    """
    try:
        ohlcv = binance.fetch_ohlcv(simbolo.upper(), timeframe=timeframe, limit=limite)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        df['ema_9'] = df['close'].ewm(span=9, adjust=False).mean()
        df['ema_21'] = df['close'].ewm(span=21, adjust=False).mean()
        
        delta = df['close'].diff()
        ganho = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        perda = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = ganho / perda
        df['rsi'] = 100 - (100 / (1 + rs))
        
        ultima = df.iloc[-1]
        cruzamento = "ALTA" if ultima['ema_9'] > ultima['ema_21'] else "BAIXA"
        
        return {
            "mercado": "Binance",
            "ativo": simbolo.upper(),
            "timeframe": timeframe,
            "ultimo_fechamento": round(float(ultima['close']), 4),
            "indicadores": {
                "ema_9": round(float(ultima['ema_9']), 4),
                "ema_21": round(float(ultima['ema_21']), 4),
                "rsi_14": round(float(ultima['rsi']), 2) if not pd.isna(ultima['rsi']) else None,
                "tendencia": cruzamento
            },
            "sinal": "COMPRA" if cruzamento == "ALTA" and ultima['rsi'] < 65 else "AGUARDAR"
        }
    except Exception as e:
        return {"erro": f"Erro na análise Binance: {str(e)}"}

# ==========================================
# 3. MESA DE EXECUÇÃO / DISPARO
# ==========================================

@router.post("/executar-ordem")
def executar_ordem(ordem: OrdemSimulada):
    """
    Recebe ordens tanto para B3 (MT5) quanto para Binance.
    """
    return {
        "status": "ORDEM_ENVIADA_AO_ROTEADOR",
        "mercado": ordem.mercado.upper(),
        "ativo": ordem.ativo.upper(),
        "quantidade": ordem.quantidade,
        "operacao": ordem.tipo.upper(),
        "modo": "Paper Trading (Sandbox de Validação de Risco)"
    }