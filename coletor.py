import sqlite3
import yfinance as yf
import requests
import base64
from datetime import datetime
import urllib3

# Remove os avisos de segurança ao consultar sites do governo/B3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def criar_banco():
    conn = sqlite3.connect('dados_mercado.db')
    cursor = conn.cursor()

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cotacoes_b3 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ativo TEXT,
        data TEXT,
        abertura REAL,
        maxima REAL,
        minima REAL,
        fechamento REAL,
        volume INTEGER
    )
    ''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cotacoes_cripto (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ativo TEXT,
        data TEXT,
        preco REAL
    )
    ''')
    
    conn.commit()
    return conn

def carregar_tickers_da_b3():
    print("Conectando aos servidores da B3 para ler a carteira IBOV de hoje...")
    try:
        # A B3 pede um código codificado em Base64 para saber qual índice queremos
        # O código abaixo é a tradução de: {"index":"IBOV","language":"pt-br"}
        payload = base64.b64encode(b'{"index":"IBOV","language":"pt-br"}').decode('utf-8')
        url = f"https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{payload}"
        
        # Disfarçamos nosso script Python como se fosse um navegador comum (Google Chrome)
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        resposta = requests.get(url, headers=headers, verify=False)
        dados = resposta.json()
        
        # Extrai os códigos limpos do JSON que a B3 devolve e coloca o .SA
        tickers_com_sa = []
        for item in dados.get('results', []):
            codigo = item.get('cod', '').strip()
            # Ignora linhas de resumo que a B3 costuma mandar com o código vazio
            if codigo and codigo.isalnum():
                tickers_com_sa.append(f"{codigo}.SA")
                
        print(f" -> SUCESSO! O robô hackeou {len(tickers_com_sa)} ações direto do Ibovespa.")
        return tickers_com_sa
    except Exception as e:
        print(f" -> Erro ao conectar na B3: {e}")
        return []

def coletar_b3(conn, ativo):
    print(f"Buscando: {ativo}...")
    try:
        ticker = yf.Ticker(ativo)
        dados = ticker.history(period="1d")
        
        if not dados.empty:
            data_atual = dados.index[-1].strftime('%Y-%m-%d')
            abertura = float(dados['Open'].iloc[-1])
            maxima = float(dados['High'].iloc[-1])
            minima = float(dados['Low'].iloc[-1])
            fechamento = float(dados['Close'].iloc[-1])
            volume = int(dados['Volume'].iloc[-1])

            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO cotacoes_b3 (ativo, data, abertura, maxima, minima, fechamento, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (ativo, data_atual, abertura, maxima, minima, fechamento, volume))
            conn.commit()
            print(f" -> OK: {ativo} salvo.")
        else:
            print(f" -> Sem dados hoje para {ativo}.")
    except Exception as e:
        print(f" -> Erro ao coletar {ativo}: {e}")

def coletar_cripto(conn, ativo):
    print(f"Buscando Cripto: {ativo}...")
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={ativo}"
        resposta = requests.get(url).json()
        
        if 'price' in resposta:
            preco = float(resposta['price'])
            data_atual = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            cursor = conn.cursor()
            cursor.execute('''
            INSERT INTO cotacoes_cripto (ativo, data, preco)
            VALUES (?, ?, ?)
            ''', (ativo, data_atual, preco))
            conn.commit()
            print(f" -> OK: {ativo} a ${preco:.2f} salvo.")
    except Exception as e:
        print(f" -> Erro ao coletar {ativo}: {e}")

if __name__ == "__main__":
    print("=== Iniciando Motor ETL Autônomo do Alphaforge ===")
    
    conexao = criar_banco()
    
    # 1. Pede para o robô ir na B3 buscar a lista sozinho
    acoes_b3 = carregar_tickers_da_b3()
    
    # 2. Criptomoedas fixas (essas raramente mudam)
    criptomoedas = [
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "ADAUSDT", "XRPUSDT"
    ]
    
    # 3. Baixa os dados de preço de tudo que foi encontrado
    if acoes_b3:
        print(f"\n--- Coletando Preços das {len(acoes_b3)} Ações ---")
        for acao in acoes_b3:
            coletar_b3(conexao, acao)
            
    print(f"\n--- Coletando Preços das {len(criptomoedas)} Criptomoedas ---")
    for cripto in criptomoedas:
        coletar_cripto(conexao, cripto)
    
    conexao.close()
    print("\n=== Coleta Finalizada com Sucesso ===")