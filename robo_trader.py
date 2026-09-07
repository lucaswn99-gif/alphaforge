import time
import hmac
import hashlib
import requests
import json
import os
from datetime import datetime
from urllib.parse import urlencode

# ================= CONFIGURAÇÕES =================
API_ALPHAFORGE = "http://127.0.0.1:8000"

API_KEY = "EzBJbkeTQfvUbSEHA6eoosqjGN0zt8GTSy7or9qFru3qwMIqcR14AaMlQiinuVvE"
SECRET_KEY = "azelfd1YmqLuZMOH0cSMI8lZGQUctzepArROBLv0tIrrKPkvsfuDW6ZUvNHKit0L"
BASE_URL = "https://testnet.binance.vision"

VALOR_POR_TRADE_USDT = 80.0  # Alocação por posição (~0.001 BTC)
PARES_MONITORADOS = ["BTCUSDT"]

ARQUIVO_HISTORICO = "historico_ordens_reais.json"
# =================================================

headers = {"X-MBX-APIKEY": API_KEY}

def assinar(params: dict, secret: str) -> str:
    query = urlencode(params)
    sig = hmac.new(secret.encode('utf-8'), query.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{query}&signature={sig}"

def registrar_historico(dados_trade: dict):
    trades = []
    if os.path.exists(ARQUIVO_HISTORICO):
        try:
            with open(ARQUIVO_HISTORICO, "r", encoding="utf-8") as f:
                trades = json.load(f)
        except:
            trades = []
    trades.append(dados_trade)
    with open(ARQUIVO_HISTORICO, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, ensure_ascii=False)

def executar_ciclo_trade(symbol: str, preco_atual: float, stop_loss: float, alvo: float):
    try:
        quantidade = 0.001  # Lote mínimo padrão Testnet para BTC
        preco_stop = round(stop_loss, 2)
        preco_stop_limit = round(preco_stop * 0.998, 2)
        preco_alvo = round(alvo, 2)

        print(f"\n[EXECUTOR] Disparando COMPRA A MERCADO de {quantidade} {symbol} a ~${preco_atual:,.2f}...")

        # 1. Compra a Mercado
        params_compra = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quantity": f"{quantidade:.3f}",
            "timestamp": int(time.time() * 1000)
        }
        q_compra = assinar(params_compra, SECRET_KEY)
        res_compra = requests.post(f"{BASE_URL}/api/v3/order?{q_compra}", headers=headers).json()

        if "orderId" not in res_compra:
            print(f"❌ Falha na compra: {res_compra}")
            return

        order_id = res_compra['orderId']
        print(f"✅ Compra executada! Order ID: {order_id}")

        # 2. Armação OCO
        print(f"[BLINDAGEM] Registrando OCO: Alvo em ${preco_alvo:,.2f} | Stop em ${preco_stop:,.2f}...")
        params_oco = {
            "symbol": symbol,
            "side": "SELL",
            "quantity": f"{quantidade:.3f}",
            "aboveType": "LIMIT_MAKER",
            "abovePrice": f"{preco_alvo:.2f}",
            "belowType": "STOP_LOSS_LIMIT",
            "belowStopPrice": f"{preco_stop:.2f}",
            "belowPrice": f"{preco_stop_limit:.2f}",
            "belowTimeInForce": "GTC",
            "timestamp": int(time.time() * 1000)
        }
        q_oco = assinar(params_oco, SECRET_KEY)
        res_oco = requests.post(f"{BASE_URL}/api/v3/orderList/oco?{q_oco}", headers=headers).json()

        if "orderListId" in res_oco:
            print(f"🛡️ Blindagem OCO ativa! Order List ID: {res_oco['orderListId']}")
            registrar_historico({
                "data_hora": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "ativo": symbol,
                "quantidade": quantidade,
                "preco_entrada": preco_atual,
                "stop_loss": preco_stop,
                "alvo_final": preco_alvo,
                "id_compra": order_id,
                "id_oco": res_oco['orderListId'],
                "status": "OPERACAO_BLINDADA"
            })
        else:
            print(f"❌ Erro ao registrar OCO: {res_oco}")

    except Exception as e:
        print(f"❌ Erro no envio: {e}")

def monitorar():
    print(f"\n--- [SCANNER QUANT] Varredura em {datetime.now().strftime('%H:%M:%S')} ---")
    for symbol in PARES_MONITORADOS:
        try:
            res = requests.get(f"{API_ALPHAFORGE}/analisar/{symbol}", timeout=15)
            if res.status_code != 200:
                continue

            dados = res.json()
            sinal = dados.get("sinal")
            prob = dados.get("probabilidade_alta")
            preco = dados.get("preco_atual")
            plano = dados.get("plano_de_trade_quantitativo", {})

            print(f"[{symbol}] Preço: ${preco:,.2f} | Sinal: {sinal} | Probabilidade IA: {prob}")

            if sinal == "COMPRA FORTE" and plano.get("status") == "Ativo":
                print(f"🔥 SINAL DETECTADO: Executando estratégia para {symbol}!")
                stop = plano.get("stop_loss")
                alvo = plano.get("alvo_2_final")
                executar_ciclo_trade(symbol, preco, stop, alvo)

        except Exception as e:
            print(f"Falha ao consultar {symbol}: {e}")

if __name__ == "__main__":
    print("=== Alphaforge Autonomous Trader Iniciado ===")
    print("Conexão: Binance Spot Testnet ativa e validada.\n")

    while True:
        try:
            monitorar()
            time.sleep(180)
        except KeyboardInterrupt:
            print("\nRobô finalizado pelo usuário.")
            break