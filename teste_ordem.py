import time
import hmac
import hashlib
import requests
from urllib.parse import urlencode

API_KEY = "EzBJbkeTQfvUbSEHA6eoosqjGN0zt8GTSy7or9qFru3qwMIqcR14AaMlQiinuVvE"
SECRET_KEY = "azelfd1YmqLuZMOH0cSMI8lZGQUctzepArROBLv0tIrrKPkvsfuDW6ZUvNHKit0L"
BASE_URL = "https://testnet.binance.vision"

def assinar(params: dict, secret: str) -> str:
    query = urlencode(params)
    sig = hmac.new(secret.encode('utf-8'), query.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{query}&signature={sig}"

headers = {"X-MBX-APIKEY": API_KEY}

print("==================================================")
print("     ALPHAFORGE: DISPARO COMPLETO NA TESTNET      ")
print("==================================================")

# 1. Consulta de saldo e conectividade
ts = int(time.time() * 1000)
q_acc = assinar({"timestamp": ts}, SECRET_KEY)
res_acc = requests.get(f"{BASE_URL}/api/v3/account?{q_acc}", headers=headers).json()

if "balances" not in res_acc:
    print(f"❌ Falha de autenticação: {res_acc}")
    exit()

saldos = {b['asset']: float(b['free']) for b in res_acc['balances'] if float(b['free']) > 0}
print(f"✅ Conectado! Saldo USDT: ${saldos.get('USDT', 0.0):,.2f} | BTC: {saldos.get('BTC', 0.0)}")

# 2. Cotação de mercado
res_ticker = requests.get(f"{BASE_URL}/api/v3/ticker/price?symbol=BTCUSDT").json()
preco_atual = float(res_ticker['price'])
print(f"Preço BTCUSDT: ${preco_atual:,.2f}")

quantidade = 0.001
preco_alvo = round(preco_atual * 1.03, 2)
preco_stop = round(preco_atual * 0.985, 2)
preco_stop_limit = round(preco_stop * 0.998, 2)

# 3. Compra a mercado
print("\n[1/2] Executando COMPRA A MERCADO (0.001 BTC)...")
params_compra = {
    "symbol": "BTCUSDT",
    "side": "BUY",
    "type": "MARKET",
    "quantity": f"{quantidade:.3f}",
    "timestamp": int(time.time() * 1000)
}
q_compra = assinar(params_compra, SECRET_KEY)
res_compra = requests.post(f"{BASE_URL}/api/v3/order?{q_compra}", headers=headers).json()

if "orderId" in res_compra:
    print(f"✅ Compra efetuada! Order ID: {res_compra['orderId']} | Status: {res_compra['status']}")
else:
    print(f"❌ Falha na compra: {res_compra}")
    exit()

# 4. Blindagem OCO (Take Profit + Stop Loss)
print("\n[2/2] Armamento da blindagem OCO...")
params_oco = {
    "symbol": "BTCUSDT",
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
    print(f"Status do grupo: {res_oco.get('listOrderStatus')}")
    print(f"Take Profit agendado em: ${preco_alvo:,.2f}")
    print(f"Stop Loss agendado em: ${preco_stop:,.2f}")
    print("\n✅ CICLO EXECUTADO COM ÊXITO COMPLETO!")
else:
    print(f"❌ Retorno da Binance para OCO: {res_oco}")