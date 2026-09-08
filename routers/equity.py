"""Renda Variável: scanner quantamental e auditoria individual.

Regra central do módulo: o scanner e a auditoria individual compartilham o
MESMO motor de decisão (`calcular_score_quantamental`) *e* as mesmas entradas
derivadas (RSI de Wilder, SMA50 e destruição histórica de capital). Se uma das
duas rotas parar de usar um destes helpers, os vereditos voltam a divergir.

O universo varrido é a carteira teórica do IBOV lida do B3 (ver
`modules/composicao_ibov.py`) somada aos papéis fora do índice que
acompanhamos por conta própria.
"""

import concurrent.futures
import random
import threading
import time

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Query

from modules import composicao_ibov

router = APIRouter(prefix="/renda-variavel", tags=["Renda Variável & Ações"])

# Código atual no B3 -> símbolo que o Yahoo publica. Confirmados.
TICKER_ALIASES = {
    "AXIA3": "ELET3",   # Eletrobras ON; o Yahoo ainda usa o código antigo
    "EMBJ3": "EMBR3",   # Embraer ON
    "RRRP3": "BRAV3",   # 3R -> Brava; entra caso alguém digite o código antigo
}

# Candidatos de 2ª tentativa para papéis renomeados no B3 que o Yahoo pode
# publicar sob qualquer um dos dois códigos. Não são confirmados: o scanner
# tenta o primário, e só quem falhar é reconsultado com o candidato abaixo.
# O que não resolver em nenhuma das duas passadas aparece em `falhas`.
TICKER_FALLBACKS = {
    "AXIA3": "AXIA3",   # caso o Yahoo migre para o código novo
    "EMBJ3": "EMBJ3",
    "MOTV3": "CCRO3",   # Motiva (ex-CCR)
    "MBRF3": "MRFG3",   # MBRF (Marfrig + BRF)
    "ISAE4": "TRPL4",   # ISA Energia (ex-Transmissão Paulista)
    "NATU3": "NTCO3",   # Natura (ex-Natura &Co)
    "BRAV3": "RRRP3",
}

# Papéis fora do IBOV que seguimos por conta própria (liquidez, teses setoriais
# e as classes de ação que o índice não carrega). Somados à carteira do índice.
ACOES_FORA_DO_INDICE = [
    "ITUB3", "ELET6", "JBSS3", "ITSA3", "GGBR3", "CPLE6", "CMIG3", "SAPR4", "SAPR3",
    "CRFB3", "GMAT3", "ALPA4", "EVEN3", "EZTC3", "MOVI3", "SIMH3", "ECOR3", "JSLG3",
    "GGPS3", "SLCE3", "SMTO3", "RECV3", "IRBR3", "ABCB4", "BPAN4", "BRSR6", "TUPY3",
    "ALUP11", "STBP3", "JHSF3",
]

RSI_PERIODO = 14
SMA_PERIODO = 50
# 3 anos: mínimo para calcular destruição de capital (3 anos civis) sem
# encarecer a chamada vetorizada. A auditoria individual segue puxando 10 anos.
SCANNER_HISTORICO = "3y"
# 25 threads batendo em Ticker.info era o que disparava o rate limit do Yahoo.
SCANNER_MAX_WORKERS = 8
SCANNER_TENTATIVAS = 3
SCANNER_CACHE_TTL = 900  # 15 min
TAPE_CACHE_TTL = 45

CAMPOS_UTEIS_INFO = (
    "trailingPE", "priceToBook", "returnOnEquity", "profitMargins",
    "currentPrice", "regularMarketPrice", "previousClose", "shortName",
)


# --------------------------------------------------------------------------- #
# Universo
# --------------------------------------------------------------------------- #

def candidatos_yahoo(codigo):
    """Símbolos do Yahoo a tentar para um código do B3, em ordem de prioridade."""
    vistos, saida = set(), []
    for candidato in (TICKER_ALIASES.get(codigo), codigo, TICKER_FALLBACKS.get(codigo)):
        if candidato and candidato not in vistos:
            vistos.add(candidato)
            saida.append(f"{candidato}.SA")
    return saida


def montar_universo(forcar=False):
    """Devolve (codigos, meta) — carteira do IBOV somada aos papéis extras.

    A deduplicação é por código do B3 e, mais adiante, por símbolo do Yahoo:
    AXIA3 e ELET3 resolvem para ELET3.SA, e sem isso a mesma empresa aparecia
    em duas linhas do scanner.
    """
    ibov, origem, idade = composicao_ibov.obter_composicao(forcar=forcar)

    codigos, vistos = [], set()
    for codigo in list(ibov) + list(ACOES_FORA_DO_INDICE):
        codigo = str(codigo).strip().upper()
        if codigo and codigo not in vistos:
            vistos.add(codigo)
            codigos.append(codigo)

    meta = {
        "origem_composicao": origem,
        "idade_composicao_segundos": idade,
        "papeis_no_indice": len(ibov),
        "papeis_fora_do_indice": len(ACOES_FORA_DO_INDICE),
    }
    return codigos, meta


def resolver_universo(forcar=False):
    """{símbolo_yahoo_primário: código_b3}, deduplicado por símbolo."""
    codigos, _ = montar_universo(forcar=forcar)
    mapa = {}
    for codigo in codigos:
        candidatos = candidatos_yahoo(codigo)
        if candidatos:
            mapa.setdefault(candidatos[0], codigo)
    return mapa


# --------------------------------------------------------------------------- #
# Helpers de dados
# --------------------------------------------------------------------------- #

def normalizar_dy(info, preco_ref=0.0):
    """Dividend yield em pontos percentuais (8.5 == 8,5% a.a.).

    O yfinance já trocou a convenção de `dividendYield` (fração decimal ->
    pontos percentuais) entre versões. A heurística antiga (`*100`, e se o
    resultado passasse de 100 dividir de novo) transformava um DY de 0,5% em
    50% e dava o bônus de dividendos a papéis que quase não pagam. Por isso a
    fonte primária aqui é dividendRate/preço, que não é ambígua em nenhuma
    versão.
    """
    preco = (
        info.get("currentPrice")
        or info.get("regularMarketPrice")
        or info.get("previousClose")
        or preco_ref
        or 0.0
    )
    taxa = info.get("dividendRate") or info.get("trailingAnnualDividendRate") or 0.0
    try:
        if taxa and preco:
            dy = float(taxa) / float(preco) * 100.0
            if 0.0 <= dy < 100.0:
                return dy
    except (TypeError, ValueError, ZeroDivisionError):
        pass

    # trailingAnnualDividendYield é sempre fração decimal, em qualquer versão.
    trailing = info.get("trailingAnnualDividendYield")
    try:
        if trailing:
            return float(trailing) * 100.0
    except (TypeError, ValueError):
        pass

    # Último recurso: dividendYield puro. Com yfinance >= 0.2.51 (e toda a
    # linha 1.x, que é a pinada no requirements.txt) o campo já vem em pontos
    # percentuais; só dividimos se vier absurdo.
    try:
        bruto = float(info.get("dividendYield") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if bruto <= 0:
        return 0.0
    return bruto / 100.0 if bruto > 100.0 else bruto


def calcular_rsi_wilder(close, periodo=RSI_PERIODO):
    """RSI com suavização de Wilder (EMA com alpha = 1/n).

    A versão anterior usava média móvel simples, que diverge do RSI exibido em
    qualquer terminal — e as bandas 35/70 do score foram calibradas para o RSI
    de Wilder.
    """
    if close is None or len(close) < periodo + 1:
        return 50.0

    delta = close.diff().dropna()
    if delta.empty:
        return 50.0

    ganho = delta.clip(lower=0.0)
    perda = (-delta).clip(lower=0.0)

    media_ganho = ganho.ewm(alpha=1.0 / periodo, adjust=False, min_periods=periodo).mean()
    media_perda = perda.ewm(alpha=1.0 / periodo, adjust=False, min_periods=periodo).mean()

    try:
        g = float(media_ganho.iloc[-1])
        p = float(media_perda.iloc[-1])
    except (IndexError, TypeError, ValueError):
        return 50.0

    if pd.isna(g) or pd.isna(p):
        return 50.0
    if p == 0.0:
        return 100.0 if g > 0 else 50.0

    return round(100.0 - (100.0 / (1.0 + (g / p))), 1)


def serie_anual(close):
    """Agrega os fechamentos por ano civil (abertura, fechamento, máx, mín)."""
    if close is None or len(close) == 0:
        return []

    agrupado = close.groupby(close.index.year).agg(["first", "last", "max", "min"])

    linhas = []
    for ano, row in agrupado.iterrows():
        try:
            primeiro = float(row["first"])
            ultimo = float(row["last"])
        except (TypeError, ValueError):
            continue
        if primeiro == 0 or pd.isna(primeiro) or pd.isna(ultimo):
            continue
        variacao = ((ultimo - primeiro) / primeiro) * 100.0
        linhas.append({
            "ano": int(ano),
            "fechamento": round(ultimo, 2),
            "maxima": round(float(row["max"]), 2),
            "minima": round(float(row["min"]), 2),
            "retorno_ano_pct": round(variacao, 2),
        })
    return linhas


def houve_destruicao_de_capital(anual):
    """Média dos 3 últimos anos civis pior que -30% => value trap.

    Usado pelo scanner E pela auditoria individual. Enquanto só a auditoria
    calculava isso, o mesmo papel saía "COMPRA" na varredura e
    "VENDA / ALTO RISCO" ao ser clicado.
    """
    retornos = [linha["retorno_ano_pct"] for linha in anual]
    if len(retornos) < 3:
        return False
    return float(np.mean(retornos[-3:])) < -30.0


ROE_MAXIMO_PLAUSIVEL = 200.0


def _derivar_patrimonio_liquido(info):
    """Patrimônio líquido a partir do que o Yahoo expõe em `.info`.

    `totalStockholderEquity` é campo de balanço e quase nunca vem no payload de
    `.info`, então há dois derivados: VPA x ações em circulação, e valor de
    mercado / P/VP.
    """
    try:
        contabil = info.get("totalStockholderEquity")
        if contabil and float(contabil) > 0:
            return float(contabil)
    except (TypeError, ValueError):
        pass

    try:
        vpa = float(info.get("bookValue") or 0.0)
        acoes = float(info.get("sharesOutstanding") or 0.0)
        if vpa > 0 and acoes > 0:
            return vpa * acoes
    except (TypeError, ValueError):
        pass

    try:
        valor_mercado = float(info.get("marketCap") or 0.0)
        pvp = float(info.get("priceToBook") or 0.0)
        if valor_mercado > 0 and pvp > 0:
            return valor_mercado / pvp
    except (TypeError, ValueError):
        pass

    return None


def calcular_roe(info):
    """ROE em pontos percentuais, ou None quando não há como apurar.

    O Yahoo omite `returnOnEquity` de vários papéis (PETR4 entre eles). Tratar
    a ausência como ROE=0 fazia o motor marcar a empresa como em prejuízo e
    cravar VENDA / ALTO RISCO. Aqui a ausência vira None, que o motor pontua
    como "não apurado" em vez de "negativo".

    O teto de plausibilidade existe porque a derivação por lucro/patrimônio
    explode quando o patrimônio não vem: um denominador de 1 produz ROE na casa
    dos trilhões — que ainda por cima ganharia o bônus de alta rentabilidade.
    """
    try:
        bruto = info.get("returnOnEquity")
        if bruto not in (None, 0):
            roe = float(bruto) * 100.0
            if abs(roe) <= ROE_MAXIMO_PLAUSIVEL:
                return roe
    except (TypeError, ValueError):
        pass

    try:
        lucro = float(info.get("netIncomeToCommon") or 0.0)
    except (TypeError, ValueError):
        lucro = 0.0

    patrimonio = _derivar_patrimonio_liquido(info)
    if lucro and patrimonio and patrimonio > 0:
        roe = (lucro / patrimonio) * 100.0
        if abs(roe) <= ROE_MAXIMO_PLAUSIVEL:
            return roe

    return None


def _info_tem_conteudo(info):
    """Payload parcial do Yahoo ainda é aproveitável.

    A checagem anterior exigia a chave 'symbol', que o Yahoo omite em respostas
    parciais perfeitamente válidas — e o papel sumia do scanner sem aviso.
    """
    if not isinstance(info, dict):
        return False
    return any(info.get(campo) is not None for campo in CAMPOS_UTEIS_INFO)


def _normalizar_info(info, simbolo):
    def _num(chave, escala=1.0):
        try:
            return float(info.get(chave) or 0.0) * escala
        except (TypeError, ValueError):
            return 0.0

    return {
        "pl": _num("trailingPE"),
        "pvp": _num("priceToBook"),
        "roe": calcular_roe(info),
        "margem_liq": _num("profitMargins", 100.0),
        "dy": normalizar_dy(info),
        "nome": info.get("shortName") or simbolo.replace(".SA", ""),
        "setor": info.get("sector") or "N/A",
    }


def extrair_fundamentos(simbolo, tentativas=SCANNER_TENTATIVAS):
    """Puxa os múltiplos de um papel. Devolve (dados, motivo_da_falha).

    Devolver o motivo é o ponto: com `except:` nu, um ticker barrado por rate
    limit era indistinguível de um ticker deslistado, e o total do scanner
    encolhia em silêncio.
    """
    motivo = "sem dados"
    for tentativa in range(tentativas):
        try:
            info = yf.Ticker(simbolo).info or {}
        except Exception as exc:  # noqa: BLE001 - o motivo vai para o relatório
            motivo = type(exc).__name__
        else:
            if _info_tem_conteudo(info):
                return _normalizar_info(info, simbolo), None
            motivo = "payload sem múltiplos"

        if tentativa < tentativas - 1:
            # Backoff com jitter: o rate limit do Yahoo é por janela curta.
            time.sleep((2 ** tentativa) * 0.75 + random.uniform(0.0, 0.5))

    return None, motivo


def fatiar_precos(df, simbolo):
    """Extrai o sub-dataframe OHLCV de um ticker do download vetorizado.

    dropna(subset=["Close"]) em vez de dropna(): o how="any" anterior derrubava
    o pregão inteiro por um único NaN em Volume, encurtando a série a ponto de
    o papel ser descartado por "histórico insuficiente".
    """
    try:
        if isinstance(df.columns, pd.MultiIndex):
            if simbolo not in df.columns.get_level_values(0):
                return None
            sub = df[simbolo]
        else:
            sub = df
        if "Close" not in sub.columns:
            return None
        return sub.dropna(subset=["Close"])
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# Motor de decisão (único)
# --------------------------------------------------------------------------- #

def calcular_score_quantamental(pl, pvp, roe, dy, margem_liq, tendencia_grafica,
                                rsi_val, destruicao_historica=False):
    """Motor de Decisão ÚNICO. Scanner e auditoria individual passam por aqui
    com o mesmo conjunto de entradas, inclusive `destruicao_historica`.

    `roe` aceita None: significa "não apurado", que é diferente de zero. Um ROE
    ausente na fonte não pode ser lido como prejuízo.
    """
    score = 50
    alertas_risco = []
    pontos_positivos = []

    roe_apurado = roe is not None
    roe_valor = float(roe) if roe_apurado else 0.0

    em_prejuizo = margem_liq < 0 or (roe_apurado and roe_valor <= 0)
    if margem_liq < 0:
        alertas_risco.append(f"Margem Líquida negativa ({margem_liq:.2f}%).")
    if roe_apurado and roe_valor <= 0:
        alertas_risco.append("ROE zerado ou negativo.")
    if not roe_apurado:
        alertas_risco.append("ROE não apurado na fonte - indicador não pontuado.")
    if destruicao_historica:
        alertas_risco.append("Destruição contínua de capital recente (Value Trap).")

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

    if roe_apurado and roe_valor >= 15:
        score += 15
        pontos_positivos.append(f"Alta rentabilidade (ROE {roe_valor:.1f}%)")

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
    if score >= 75:
        veredito = "COMPRA FORTE"
    elif score >= 60:
        veredito = "COMPRA"
    elif score <= 40:
        veredito = "VENDA"
    else:
        veredito = "NEUTRO"

    if em_prejuizo or destruicao_historica:
        veredito = "VENDA / ALTO RISCO"

    return score, veredito, pontos_positivos, alertas_risco


# --------------------------------------------------------------------------- #
# Coleta
# --------------------------------------------------------------------------- #

def coletar_bloco(mapa):
    """Varre um bloco {símbolo_yahoo: código_b3}.

    Devolve (resultados, falhas), cada falha como {"ticker", "simbolo", "motivo"}.
    Uma única chamada vetorizada de preços + coleta de múltiplos em paralelo.
    """
    if not mapa:
        return [], []

    simbolos = list(mapa.keys())

    try:
        df_precos = yf.download(
            simbolos,
            period=SCANNER_HISTORICO,
            interval="1d",
            progress=False,
            group_by="ticker",
            auto_adjust=True,
        )
    except Exception as exc:  # noqa: BLE001
        motivo = f"download: {type(exc).__name__}"
        return [], [{"ticker": c, "simbolo": s, "motivo": motivo} for s, c in mapa.items()]

    if df_precos is None or df_precos.empty:
        return [], [{"ticker": c, "simbolo": s, "motivo": "sem série de preço"}
                    for s, c in mapa.items()]

    fundamentos, falhas = {}, []
    with concurrent.futures.ThreadPoolExecutor(max_workers=SCANNER_MAX_WORKERS) as pool:
        futuros = {pool.submit(extrair_fundamentos, s): s for s in simbolos}
        for futuro in concurrent.futures.as_completed(futuros):
            simbolo = futuros[futuro]
            try:
                dados, motivo = futuro.result()
            except Exception as exc:  # noqa: BLE001
                dados, motivo = None, type(exc).__name__
            if dados:
                fundamentos[simbolo] = dados
            else:
                falhas.append({"ticker": mapa[simbolo], "simbolo": simbolo, "motivo": motivo})

    resultados = []
    for simbolo, codigo in mapa.items():
        info = fundamentos.get(simbolo)
        if info is None:
            continue

        sub = fatiar_precos(df_precos, simbolo)
        if sub is None or len(sub) < SMA_PERIODO:
            falhas.append({"ticker": codigo, "simbolo": simbolo,
                           "motivo": "histórico insuficiente para SMA50"})
            continue

        try:
            close = sub["Close"]
            preco = float(close.iloc[-1])
            sma50 = float(close.rolling(SMA_PERIODO).mean().iloc[-1])
            if pd.isna(sma50):
                falhas.append({"ticker": codigo, "simbolo": simbolo, "motivo": "SMA50 indisponível"})
                continue

            tendencia = "ALTA" if preco > sma50 else "BAIXA"
            rsi_val = calcular_rsi_wilder(close)
            anual = serie_anual(close)
            destruicao = houve_destruicao_de_capital(anual)

            score, veredito, _, alertas = calcular_score_quantamental(
                pl=info["pl"], pvp=info["pvp"], roe=info["roe"], dy=info["dy"],
                margem_liq=info["margem_liq"], tendencia_grafica=tendencia,
                rsi_val=rsi_val, destruicao_historica=destruicao,
            )

            resultados.append({
                "ticker": codigo,
                "simbolo_yahoo": simbolo,
                "nome": info["nome"],
                "setor": info["setor"],
                "preco": round(preco, 2),
                "pl": round(info["pl"], 2) if info["pl"] else None,
                "pvp": round(info["pvp"], 2) if info["pvp"] else None,
                "roe": round(info["roe"], 2) if info["roe"] is not None else None,
                "dy": round(info["dy"], 2),
                "tendencia_grafica": tendencia,
                "rsi": rsi_val,
                "destruicao_capital": destruicao,
                "score_geral": score,
                "veredito": veredito,
                "alertas": alertas,
            })
        except Exception as exc:  # noqa: BLE001
            falhas.append({"ticker": codigo, "simbolo": simbolo,
                           "motivo": f"cálculo: {type(exc).__name__}"})

    return resultados, falhas


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

_cache_scanner = {"carimbo": 0.0, "payload": None}
_lock_scanner = threading.Lock()

_cache_tape = {"carimbo": 0.0, "payload": None}
_lock_tape = threading.Lock()


@router.get("/composicao-ibov")
def ver_composicao(forcar: bool = Query(False, description="Ignora o cache de 12h da carteira")):
    """Carteira teórica do IBOV que o scanner está usando, e de onde ela veio."""
    codigos, meta = montar_universo(forcar=forcar)
    return {**meta, "total": len(codigos), "codigos": codigos}


@router.get("/scanner-quantamental")
def executar_scanner(forcar: bool = Query(False, description="Ignora o cache de 15 minutos")):
    """Varre o universo em duas passadas: símbolo primário e, para quem falhou,
    o código alternativo. Falhas são relatadas, nunca escondidas."""
    agora = time.time()
    with _lock_scanner:
        payload_cache = _cache_scanner["payload"]
        idade = agora - _cache_scanner["carimbo"]
    if payload_cache is not None and idade < SCANNER_CACHE_TTL and not forcar:
        return {**payload_cache, "cache": True, "idade_segundos": int(idade)}

    codigos, meta = montar_universo(forcar=forcar)

    # 1ª passada: símbolo preferencial de cada código.
    mapa_primario = {}
    for codigo in codigos:
        candidatos = candidatos_yahoo(codigo)
        if candidatos:
            mapa_primario.setdefault(candidatos[0], codigo)

    resultados, falhas = coletar_bloco(mapa_primario)
    resolvidos = {linha["ticker"] for linha in resultados}

    # 2ª passada: só quem falhou e tem um símbolo alternativo. Evita manter
    # tabela de renomeação à mão — o que não resolver vira falha explícita.
    mapa_alternativo = {}
    for falha in falhas:
        codigo = falha["ticker"]
        if codigo in resolvidos:
            continue
        candidatos = candidatos_yahoo(codigo)
        alternativos = [c for c in candidatos if c != falha["simbolo"]]
        if alternativos:
            mapa_alternativo.setdefault(alternativos[0], codigo)

    falhas_finais = [f for f in falhas if f["ticker"] not in mapa_alternativo]

    if mapa_alternativo:
        extras, falhas_extras = coletar_bloco(mapa_alternativo)
        resultados.extend(extras)
        resolvidos.update(linha["ticker"] for linha in extras)
        falhas_finais.extend(f for f in falhas_extras if f["ticker"] not in resolvidos)

    falhas_finais = [f for f in falhas_finais if f["ticker"] not in resolvidos]
    # Deduplica falhas por código, preservando o primeiro motivo visto.
    vistos, falhas_unicas = set(), []
    for falha in falhas_finais:
        if falha["ticker"] not in vistos:
            vistos.add(falha["ticker"])
            falhas_unicas.append(falha)

    resultados.sort(key=lambda item: item["score_geral"], reverse=True)

    payload = {
        **meta,
        "total": len(resultados),
        "solicitados": len(codigos),
        "falhas": sorted(falhas_unicas, key=lambda f: f["ticker"]),
        "oportunidades": resultados,
    }

    with _lock_scanner:
        _cache_scanner["payload"] = payload
        _cache_scanner["carimbo"] = time.time()

    return {**payload, "cache": False, "idade_segundos": 0}


@router.get("/acao/{ticker}")
def auditar_acao(ticker: str):
    ticker_clean = ticker.upper().strip()
    candidatos = candidatos_yahoo(ticker_clean) or [f"{ticker_clean}.SA"]

    ultimo_erro = "Sem dados históricos disponíveis."
    for simbolo in candidatos:
        try:
            resposta = _auditar_simbolo(ticker_clean, simbolo)
        except Exception as exc:  # noqa: BLE001
            ultimo_erro = f"Erro ao processar ativo: {exc}"
            continue
        if "erro" not in resposta:
            return resposta
        ultimo_erro = resposta["erro"]

    return {"erro": ultimo_erro}


def _auditar_simbolo(ticker_clean, simbolo):
    ativo = yf.Ticker(simbolo)
    try:
        info = ativo.info or {}
    except Exception:  # noqa: BLE001
        info = {}

    historico = ativo.history(period="10y", interval="1d", auto_adjust=True)
    if historico is None or historico.empty or "Close" not in historico.columns:
        return {"erro": "Sem dados históricos disponíveis."}

    close = historico["Close"].dropna()
    if close.empty:
        return {"erro": "Sem dados históricos disponíveis."}

    try:
        preco = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0.0)
    except (TypeError, ValueError):
        preco = 0.0
    if preco == 0.0:
        preco = float(close.iloc[-1])

    def _num(chave, escala=1.0):
        try:
            return float(info.get(chave) or 0.0) * escala
        except (TypeError, ValueError):
            return 0.0

    pl = _num("trailingPE")
    pvp = _num("priceToBook")
    ev_ebitda = _num("enterpriseToEbitda")
    roe = calcular_roe(info)
    margem_liq = _num("profitMargins", 100.0)
    dy = normalizar_dy(info, preco_ref=preco)

    dados_10_anos = serie_anual(close)
    destruicao_historica = houve_destruicao_de_capital(dados_10_anos)

    rsi_val = calcular_rsi_wilder(close)
    if len(close) >= SMA_PERIODO:
        sma50 = float(close.rolling(SMA_PERIODO).mean().iloc[-1])
    else:
        # Sem SMA50 confiável, não afirmamos tendência de alta.
        sma50 = preco
    tendencia = "ALTA" if preco > sma50 else "BAIXA"

    # Exatamente o mesmo motor e as mesmas entradas do scanner.
    score, veredito, pontos_positivos, alertas_risco = calcular_score_quantamental(
        pl, pvp, roe, dy, margem_liq, tendencia, rsi_val, destruicao_historica
    )

    return {
        "ticker": ticker_clean,
        "simbolo_yahoo": simbolo,
        "nome_empresa": info.get("shortName") or ticker_clean,
        "setor": info.get("sector") or "N/A",
        "preco_atual": round(preco, 2),
        "score_geral": score,
        "recomendacao": veredito,
        "analise_racional": pontos_positivos + alertas_risco,
        "indicadores_tecnicos": {
            "rsi_wilder": rsi_val,
            "sma50": round(sma50, 2),
            "tendencia": tendencia,
            "destruicao_capital": destruicao_historica,
        },
        "multiplos": {
            "p_l": round(pl, 2) if pl else None,
            "p_vp": round(pvp, 2) if pvp else None,
            "ev_ebitda": round(ev_ebitda, 2) if ev_ebitda else None,
            "dividend_yield_pct": round(dy, 2),
            "roe_pct": round(roe, 2) if roe is not None else None,
            "margem_liquida_pct": round(margem_liq, 2),
        },
        "historico_10_anos": dados_10_anos,
    }


@router.get("/ticker-tape")
def get_ticker_tape():
    """Cotações dos termômetros de mercado para a barra de rolagem."""
    agora = time.time()
    with _lock_tape:
        payload_cache = _cache_tape["payload"]
        idade = agora - _cache_tape["carimbo"]
    if payload_cache is not None and idade < TAPE_CACHE_TTL:
        return payload_cache

    simbolos = [
        "^BVSP", "USDBRL=X", "PETR4.SA", "VALE3.SA", "ITUB4.SA",
        "WEGE3.SA", "BBDC4.SA", "BBAS3.SA", "ELET3.SA", "RENT3.SA",
    ]

    try:
        # 5 dias garantem o fechamento anterior mesmo com feriado no meio.
        bruto = yf.download(simbolos, period="5d", interval="1d", progress=False, auto_adjust=True)
    except Exception:  # noqa: BLE001
        return payload_cache or []

    if bruto is None or bruto.empty:
        return payload_cache or []

    try:
        fechamentos = bruto["Close"]
    except (KeyError, TypeError):
        return payload_cache or []

    if isinstance(fechamentos, pd.Series):
        fechamentos = fechamentos.to_frame(name=simbolos[0])

    resultados = []
    for simbolo in simbolos:
        if simbolo not in fechamentos.columns:
            continue
        validos = fechamentos[simbolo].dropna()
        if len(validos) < 2:
            continue
        try:
            atual = float(validos.iloc[-1])
            anterior = float(validos.iloc[-2])
        except (TypeError, ValueError):
            continue
        if anterior == 0:
            continue

        nome = simbolo.replace(".SA", "").replace("^BVSP", "IBOV").replace("USDBRL=X", "USD/BRL")
        resultados.append({
            "ativo": nome,
            "preco": round(atual, 2),
            "variacao": round(((atual - anterior) / anterior) * 100.0, 2),
        })

    if resultados:
        with _lock_tape:
            _cache_tape["payload"] = resultados
            _cache_tape["carimbo"] = time.time()
        return resultados

    return payload_cache or []
