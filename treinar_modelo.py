import yfinance as yf
import pandas as pd
import pandas_ta as ta
import lightgbm as lgb
from sklearn.model_selection import train_test_split
from statsmodels.tsa.stattools import adfuller
import joblib
import warnings
import numpy as np

warnings.filterwarnings('ignore')

def calcular_adf_pvalue(serie):
    try:
        return float(adfuller(serie, autolag='AIC')[1])
    except:
        return 0.5

def extrair_features_estacionarias(ativos):
    print("Processando histórico com 14 variáveis normalizadas...")
    df_completo = pd.DataFrame()
    
    for ativo in ativos:
        try:
            ticker = yf.Ticker(ativo)
            df = ticker.history(period="10y")
            
            if len(df) < 120:
                continue
                
            # 1. Distâncias Percentuais das Médias
            ema_9 = ta.ema(df['Close'], length=9)
            ema_21 = ta.ema(df['Close'], length=21)
            df['DIST_EMA9'] = (df['Close'] - ema_9) / ema_9
            df['DIST_EMA21'] = (df['Close'] - ema_21) / ema_21
            df['DIST_EMAS'] = (ema_9 - ema_21) / ema_21
            
            # 2. Osciladores Normalizados
            df['RSI_14'] = ta.rsi(df['Close'], length=14) / 100.0
            macd = ta.macd(df['Close'], fast=12, slow=26, signal=9)
            df['MACD_REL'] = macd['MACD_12_26_9'] / df['Close']
            
            # 3. Força Direcional (ADX / DMI)
            adx_df = ta.adx(df['High'], df['Low'], df['Close'], length=14)
            df['ADX_14'] = adx_df['ADX_14'] / 100.0
            df['DMP_14'] = adx_df['DMP_14'] / 100.0
            df['DMN_14'] = adx_df['DMN_14'] / 100.0
            
            # 4. Donchian Relativo
            donchian = ta.donchian(df['High'], df['Low'], lower_length=20, upper_length=20)
            dcl = donchian['DCL_20_20']
            dcu = donchian['DCU_20_20']
            amplitude = (dcu - dcl).replace(0, np.nan)
            df['DONCHIAN_POS'] = (df['Close'] - dcl) / amplitude
            
            # 5. Volume Relativo
            df['VOL_REL'] = df['Volume'] / df['Volume'].rolling(window=20).mean()
            
            # 6. ADF (Estacionariedade)
            df['ADF_PVAL'] = df['Close'].rolling(window=30).apply(calcular_adf_pvalue, raw=False)
            
            # 7. Volatilidade ATR Relativa
            atr_df = ta.atr(df['High'], df['Low'], df['Close'], length=14)
            df['ATR_REL'] = atr_df / df['Close']
            
            # 8. Retornos Passados
            df['RET_1D'] = df['Close'].pct_change(1)
            df['RET_5D'] = df['Close'].pct_change(5)
            
            # Alvo: Retorno acumulado em 5 dias >= 1.5%
            retorno_futuro_5d = (df['Close'].shift(-5) - df['Close']) / df['Close']
            df['Alvo'] = (retorno_futuro_5d >= 0.015).astype(int)
            
            df = df.dropna()
            df_completo = pd.concat([df_completo, df])
            print(f" -> {ativo} normalizado ({len(df)} candles).")
            
        except Exception as e:
            print(f"Erro em {ativo}: {e}")
            
    return df_completo

if __name__ == "__main__":
    print("=== Alphaforge: Treinamento com 14 Features ===")
    
    ativos = [
        "PETR4.SA", "VALE3.SA", "ITUB4.SA", "BBDC4.SA", "BBAS3.SA", 
        "WEGE3.SA", "ABEV3.SA", "RENT3.SA", "B3SA3.SA", "PRIO3.SA"
    ]
    
    dados = extrair_features_estacionarias(ativos)
    
    features = [
        'DIST_EMA9', 'DIST_EMA21', 'DIST_EMAS',
        'RSI_14', 'MACD_REL', 'ADX_14', 'DMP_14', 'DMN_14',
        'DONCHIAN_POS', 'VOL_REL', 'ADF_PVAL', 'ATR_REL',
        'RET_1D', 'RET_5D'
    ]
    
    X = dados[features]
    y = dados['Alvo']
    
    print(f"\nConfirmando dimensoes: {len(features)} variaveis selecionadas.")
    
    X_treino, X_teste, y_treino, y_teste = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    modelo = lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.015,
        num_leaves=20,
        max_depth=5,
        min_child_samples=80,
        subsample=0.8,
        colsample_bytree=0.7,
        random_state=42
    )
    
    modelo.fit(X_treino, y_treino)
    
    probs = modelo.predict_proba(X_teste)[:, 1]
    for corte in [0.55, 0.60, 0.65, 0.70]:
        mascara = probs >= corte
        total = np.sum(mascara)
        if total > 0:
            taxa = np.mean(y_teste[mascara] == 1) * 100
            print(f"Probabilidade >= {corte*100:.0f}% -> Assertividade: {taxa:.2f}% ({total} trades)")
            
    joblib.dump(modelo, 'modelo_alphaforge.pkl')
    print("\nArquivo 'modelo_alphaforge.pkl' gerado com sucesso com 14 features.")