import os
from binance.client import Client

API_KEY = "BfaTKAzVTwEZv3k7RbJxETJIeuDGvUlyRCyDxBnRbJ1bQbfRXIGq2RzPc25kz6qZ"
SECRET_KEY = "iNK4xSwwwZNmPP6w00OKDJrL98afytx5TJmiGiCiqAsUFN6Qgh2EP9KRL35KSARz"

print("==================================================")
print("     DIAGNÓSTICO DE CREDENCIAIS TESTNET           ")
print("==================================================")

# 1. Teste Spot Testnet (Forçando endpoint oficial testnet.binance.vision)
print("\n[1/2] Testando na Binance SPOT TESTNET...")
try:
    c_spot = Client(API_KEY, SECRET_KEY, testnet=True)
    c_spot.API_URL = 'https://testnet.binance.vision/api'
    acc = c_spot.get_account()
    print(">>> SUCESSO: Conectado à SPOT TESTNET! <<<")
    saldos = [f"{b['asset']}: {float(b['free']):.2f}" for b in acc['balances'] if float(b['free']) > 0]
    print(f"Saldos disponíveis: {', '.join(saldos[:6])}")
except Exception as e:
    print(f"Falha Spot Testnet: {e}")

# 2. Teste Futures Testnet (testnet.binancefuture.com)
print("\n[2/2] Testando na Binance FUTURES TESTNET...")
try:
    c_fut = Client(API_KEY, SECRET_KEY, testnet=True)
    c_fut.FUTURES_URL = 'https://testnet.binancefuture.com/fapi'
    acc_f = c_fut.futures_account()
    print(">>> SUCESSO: Conectado à FUTURES TESTNET! <<<")
    print(f"Saldo USDT: ${float(acc_f.get('totalWalletBalance', 0)):.2f}")
except Exception as e:
    print(f"Falha Futures Testnet: {e}")

print("\n==================================================")