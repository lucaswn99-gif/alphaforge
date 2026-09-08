"""
Camada única de acesso a dados de mercado (Yahoo Finance).

Centraliza o que antes estava duplicado (e divergente) em routers/equity.py e
routers/wealth.py:

  * cache TTL em memória  -> evita re-baixar tudo a cada request e é a principal
                             defesa contra o rate limit do Yahoo;
  * retry com backoff     -> requisição que falha volta como dict vazio, não erro;
                             sem retry o ticker some silenciosamente do scanner;
  * ROE em cascata        -> resolve o "ROE zerado" (ver calcular_roe);
  * DY normalizado        -> a escala de `dividendYield` mudou entre versões do
                             yfinance; aqui a inferência é determinística;
  * ausência != zero      -> dado indisponível retorna None, nunca 0.0.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

import numpy as np
import pandas as pd
import yfinance as yf

# ---------------------------------------------------------------------------
# Cache TTL thread-safe
# ---------------------------------------------------------------------------

TTL_INFO = 900      # fundamentos mudam por trimestre; 15 min é folgado
TTL_PRECOS = 300    # preços intradiários; 5 min
TTL_BALANCO = 3600  # demonstrações; 1 h

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cache_ttl(chave: str, ttl: int, produtor: Callable[[], Any]) -> Any:
    """Retorna o valor cacheado ou executa `produtor` e cacheia o resultado."""
    agora = time.monotonic()
    with _cache_lock:
        item = _cache.get(chave)
        if item is not None and agora - item[0] < ttl:
            return item[1]

    valor = produtor()

    with _cache_lock:
        _cache[chave] = (time.monotonic(), valor)
    return valor


def limpar_cache() -> int:
    """Descarta todo o cache. Retorna quantas entradas foram removidas."""
    with _cache_lock:
        n = len(_cache)
        _cache.clear()
    return n


# ---------------------------------------------------------------------------
# Acesso bruto ao Yahoo, com retry
# ---------------------------------------------------------------------------

# O Yahoo derruba rajadas de requisições. Um semáforo global limita a
# concorrência real, independente de quantos ThreadPoolExecutor existam.
_limite_yahoo = threading.Semaphore(8)


def _com_retry(fn: Callable[[], Any], tentativas: int = 3, base: float = 0.6) -> Any:
    """Executa `fn` com backoff exponencial + jitter. Retorna None se falhar."""
    for i in range(tentativas):
        try:
            with _limite_yahoo:
                return fn()
        except Exception:
            if i == tentativas - 1:
                return None
            time.sleep(base * (2 ** i) + random.uniform(0, 0.3))
    return None


def obter_info(ticker_yf: str) -> dict:
    """`Ticker.info` com cache e retry. Retorna {} se indisponível."""

    def _buscar() -> dict:
        info = _com_retry(lambda: yf.Ticker(ticker_yf).info)
        if not isinstance(info, dict):
            return {}
        # `info` mínimo (só quoteType/symbol) significa resposta truncada pelo
        # rate limit, não empresa sem dados. Não cacheia lixo por 15 min.
        return info if len(info) > 8 else {}

    return cache_ttl(f"info:{ticker_yf}", TTL_INFO, _buscar) or {}


def obter_balanco(ticker_yf: str) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """(balance_sheet, financials) trimestrais com fallback anual. Cacheado."""

    def _buscar():
        tk = yf.Ticker(ticker_yf)
        bs = _com_retry(lambda: tk.quarterly_balance_sheet)
        fin = _com_retry(lambda: tk.quarterly_financials)
        if bs is None or (hasattr(bs, "empty") and bs.empty):
            bs = _com_retry(lambda: tk.balance_sheet)
        if fin is None or (hasattr(fin, "empty") and fin.empty):
            fin = _com_retry(lambda: tk.financials)
        return bs, fin

    return cache_ttl(f"bal:{ticker_yf}", TTL_BALANCO, _buscar) or (None, None)


def baixar_precos(tickers: list[str], period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
    """`yf.download` em lote, cacheado e tolerante a falha."""
    chave = f"px:{period}:{interval}:{','.join(sorted(set(tickers)))}"

    def _buscar() -> pd.DataFrame:
        df = _com_retry(
            lambda: yf.download(
                tickers,
                period=period,
                interval=interval,
                progress=False,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
            ),
            tentativas=2,
        )
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()

    return cache_ttl(chave, TTL_PRECOS, _buscar)


# ---------------------------------------------------------------------------
# Normalizações
# ---------------------------------------------------------------------------

def _num(valor: Any) -> float | None:
    """Converte para float finito, ou None. Trata NaN, inf, string e None."""
    if valor is None:
        return None
    try:
        f = float(valor)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(f):
        return None
    return f


def _positivo(valor: Any) -> float | None:
    f = _num(valor)
    return f if f is not None and f > 0 else None


def calcular_roe(info: dict, bs: pd.DataFrame | None = None,
                 fin: pd.DataFrame | None = None) -> tuple[float | None, str]:
    """
    ROE em % (ex.: 18.4 = 18,4%), com a origem do número.

    Cascata — cada degrau resolve um caso em que o anterior falha:

    1. `returnOnEquity` do info. Vem em decimal. Ausente para boa parte do
       mercado brasileiro no Yahoo, que é a causa raiz do ROE zerado.

    2. P/VP ÷ P/L. Identidade contábil exata:
           (Preço/VPA) ÷ (Preço/LPA) = LPA/VPA = ROE
       Independe de contagem de ações, então funciona também para units
       (KLBN11, SANB11, BPAC11) onde bookValue e sharesOutstanding estão em
       bases diferentes. É o degrau que salva a maioria dos casos.

    3. Lucro líquido ÷ (VPA × ações). Usa netIncomeToCommon, bookValue e
       sharesOutstanding — todos presentes no mesmo `info`, custo zero.

    4. Demonstrações: Net Income TTM ÷ Stockholders Equity. Só na auditoria
       individual, porque custa duas chamadas de rede por ticker.

    O código antigo tentava `info.get("totalStockholderEquity")`, chave que
    NÃO EXISTE no dict do Yahoo. O `or 1` transformava isso em patrimônio = 1,
    e o resultado era ou 0.0 (quando netIncomeToCommon também faltava) ou um
    ROE na casa dos trilhões de %. Nunca o valor certo.
    """
    # 1
    roe = _num(info.get("returnOnEquity"))
    if roe is not None and roe != 0 and abs(roe) < 30:  # sanidade: 30 = 3000%
        return roe * 100, "info.returnOnEquity"

    # 2
    pvp = _positivo(info.get("priceToBook"))
    pl = _num(info.get("trailingPE"))
    if pvp is not None and pl is not None and pl != 0:
        candidato = (pvp / pl) * 100
        if abs(candidato) < 3000:
            return candidato, "P/VP ÷ P/L"

    # 3
    lucro = _num(info.get("netIncomeToCommon"))
    vpa = _positivo(info.get("bookValue"))
    acoes = _positivo(info.get("sharesOutstanding"))
    if lucro is not None and vpa is not None and acoes is not None:
        patrimonio = vpa * acoes
        if patrimonio > 0:
            candidato = (lucro / patrimonio) * 100
            if abs(candidato) < 3000:
                return candidato, "netIncomeToCommon ÷ (VPA × ações)"

    # 4
    if bs is not None and fin is not None:
        patrimonio = _linha_demonstracao(bs, ["Stockholders Equity",
                                              "Total Stockholder Equity",
                                              "Common Stock Equity"])
        lucro_ttm = _linha_demonstracao(fin, ["Net Income",
                                              "Net Income Common Stockholders",
                                              "Net Income From Continuing Operation Net Minority Interest"],
                                        somar_periodos=4)
        if patrimonio and patrimonio > 0 and lucro_ttm is not None:
            candidato = (lucro_ttm / patrimonio) * 100
            if abs(candidato) < 3000:
                return candidato, "Balanço (Lucro TTM ÷ PL)"

    return None, "indisponível"


def _linha_demonstracao(df: pd.DataFrame | None, nomes: list[str],
                        somar_periodos: int = 1) -> float | None:
    """Lê uma linha do balanço/DRE tentando nomes alternativos do Yahoo."""
    if df is None or not hasattr(df, "empty") or df.empty:
        return None
    for nome in nomes:
        if nome not in df.index:
            continue
        serie = df.loc[nome].dropna()
        if serie.empty:
            continue
        if somar_periodos > 1:
            # colunas vêm do mais recente para o mais antigo
            janela = serie.iloc[:somar_periodos]
            if len(janela) < somar_periodos:
                # anual: uma coluna já é o ano cheio
                return _num(serie.iloc[0])
            return _num(janela.sum())
        return _num(serie.iloc[0])
    return None


def calcular_dy(info: dict, preco: float | None = None) -> float | None:
    """
    Dividend yield em % (ex.: 6.2 = 6,2%).

    O campo `dividendYield` do Yahoo mudou de escala entre versões do yfinance
    (decimal em versões antigas, percentual nas recentes). O heurístico antigo
    (`raw*100`, depois `/100` se passar de 100) só acerta por acidente na faixa
    de 1% a 100%: um DY real de 0,5% virava 50%.

    Aqui a ordem é do inequívoco para o ambíguo:
    1. trailingAnnualDividendRate ÷ preço  — duas grandezas absolutas, sem escala;
    2. trailingAnnualDividendYield         — sempre decimal;
    3. dividendYield com escala inferida por comparação com (1)/(2).
    """
    preco = _positivo(preco) or _positivo(info.get("currentPrice")) \
        or _positivo(info.get("regularMarketPrice")) or _positivo(info.get("previousClose"))

    taxa = _num(info.get("trailingAnnualDividendRate"))
    if taxa is not None and taxa > 0 and preco:
        return (taxa / preco) * 100

    dy_dec = _num(info.get("trailingAnnualDividendYield"))
    if dy_dec is not None and dy_dec > 0:
        return dy_dec * 100

    bruto = _num(info.get("dividendYield"))
    if bruto is None or bruto <= 0:
        return None
    # DY acima de 100% não existe na prática: se o número já passa de 1,
    # ele veio em formato percentual.
    return bruto if bruto > 1 else bruto * 100


def rsi_wilder(close: pd.Series, periodo: int = 14) -> pd.Series:
    """
    RSI com suavização de Wilder (EMA alpha=1/n), que é a definição original e
    a usada por TradingView, MetaTrader e Profit.

    O código antigo usava média móvel simples dos ganhos/perdas — isso produz
    um número diferente do que qualquer plataforma mostra, e o score dá ±10
    pontos em cima dos cortes 35/70.
    """
    delta = close.diff()
    ganho = delta.clip(lower=0)
    perda = (-delta).clip(lower=0)

    media_ganho = ganho.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()
    media_perda = perda.ewm(alpha=1 / periodo, adjust=False, min_periods=periodo).mean()

    rs = media_ganho / media_perda.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    # perda média zero = alta ininterrupta = RSI 100
    return rsi.where(media_perda != 0, 100.0)


def extrair_fundamentos(ticker_yf: str, profundo: bool = False) -> dict | None:
    """
    Fundamentos normalizados. Campo indisponível vem como None, nunca 0.0 —
    a distinção entre "sem dado" e "dado ruim" é o que impedia o scanner de
    classificar corretamente.

    profundo=True busca as demonstrações (2 chamadas extras). Use só na
    auditoria individual, nunca no scanner de 90+ ativos.
    """
    info = obter_info(ticker_yf)
    if not info:
        return None

    preco = (_positivo(info.get("currentPrice"))
             or _positivo(info.get("regularMarketPrice"))
             or _positivo(info.get("previousClose")))

    bs = fin = None
    if profundo:
        bs, fin = obter_balanco(ticker_yf)

    roe, roe_origem = calcular_roe(info, bs, fin)

    pl = _num(info.get("trailingPE"))
    if pl is None:
        lpa = _num(info.get("trailingEps"))
        if lpa and preco:
            pl = preco / lpa

    pvp = _positivo(info.get("priceToBook"))
    if pvp is None:
        vpa = _positivo(info.get("bookValue"))
        if vpa and preco:
            pvp = preco / vpa

    margem = _num(info.get("profitMargins"))
    if margem is None:
        lucro = _num(info.get("netIncomeToCommon"))
        receita = _positivo(info.get("totalRevenue"))
        if lucro is not None and receita:
            margem = lucro / receita

    return {
        "nome": info.get("shortName") or info.get("longName") or ticker_yf.replace(".SA", ""),
        "setor": info.get("sector"),
        "preco": preco,
        "pl": pl,
        "pvp": pvp,
        "roe": roe,
        "roe_origem": roe_origem,
        "dy": calcular_dy(info, preco),
        "margem_liq": margem * 100 if margem is not None else None,
        "ev_ebitda": _num(info.get("enterpriseToEbitda")),
    }
