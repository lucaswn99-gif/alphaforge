import concurrent.futures

import pandas as pd
from fastapi import APIRouter

from modules.market_data import (
    baixar_precos,
    extrair_fundamentos,
    limpar_cache,
    obter_info,
    rsi_wilder,
)

router = APIRouter(prefix="/renda-variavel", tags=["Renda Variável & Ações"])

# Tickers que mudaram de código na B3 -> código atual usado pelo Yahoo.
# Sentido: chave = código antigo digitado pelo usuário, valor = código vigente.
TICKER_ALIASES = {
    "RRRP3": "BRAV3",   # 3R Petroleum + Enauta -> Brava Energia (2024)
}

UNIVERSO_ACOES = [
    "VALE3", "PETR4", "ITUB4", "PETR3", "PRIO3", "BBDC4", "BBAS3", "B3SA3", "BPAC11",
    "ITUB3", "ELET3", "ELET6", "ABEV3", "WEGE3", "RENT3", "JBSS3", "SUZB3", "SANB11", "EQTL3",
    "ITSA4", "ITSA3", "BBSE3", "RDOR3", "EMBR3", "VIVT3", "RADL3", "RAIL3", "KLBN11", "GGBR4",
    "GGBR3", "CSNA3", "CPLE6", "CPLE3", "CMIG4", "CMIG3", "ENGI11", "TAEE11", "CPFE3", "SBSP3",
    "VBBR3", "UGPA3", "CSAN3", "AURE3", "ENEV3", "EGIE3", "SAPR4", "SAPR3", "CSMG3", "TOTS3",
    "MGLU3", "LREN3", "ASAI3", "CRFB3", "GMAT3", "AZZA3", "VIVA3", "CEAB3", "ALPA4", "CYRE3",
    "MRVE3", "DIRR3", "CURY3", "EVEN3", "EZTC3", "MULT3", "ALOS3", "IGTI11", "MOVI3", "SIMH3",
    "VAMO3", "CCRO3", "ECOR3", "JSLG3", "GGPS3", "CMIN3", "USIM5", "SLCE3", "SMTO3", "MRFG3",
    "BRFS3", "BEEF3", "RECV3", "BRAV3", "IRBR3", "PSSA3", "CXSE3", "ABCB4", "BPAN4", "BRSR6",
    "POMO4", "TUPY3", "GOAU4", "ALUP11", "STBP3", "JHSF3",
]


def _fmt(valor, casas=2):
    """Arredonda mantendo None como None — indisponível não vira zero."""
    return round(valor, casas) if valor is not None else None


def calcular_score_quantamental(pl, pvp, roe, dy, margem_liq, tendencia_grafica,
                                rsi_val, destruicao_historica=False):
    """
    Motor de decisão único (scanner e auditoria individual usam este).

    Mudança central: `None` significa DADO INDISPONÍVEL e o critério é
    simplesmente pulado. Antes, dado ausente chegava aqui como 0.0 e disparava
    `roe <= 0` -> "em prejuízo" -> veredito "VENDA / ALTO RISCO". Empresa
    lucrativa era reprovada por falha de API, não por fundamento.

    Retorna também `cobertura_dados`: % dos 5 fundamentos efetivamente obtidos.
    """
    score = 50
    alertas_risco = []
    pontos_positivos = []
    ausentes = []

    fundamentos = {"P/L": pl, "P/VP": pvp, "ROE": roe, "DY": dy, "Margem Líq.": margem_liq}
    ausentes = [nome for nome, v in fundamentos.items() if v is None]
    cobertura = round(100 * (len(fundamentos) - len(ausentes)) / len(fundamentos))

    # Prejuízo só é afirmado com dado em mãos.
    em_prejuizo = (margem_liq is not None and margem_liq < 0) or (roe is not None and roe < 0)
    if margem_liq is not None and margem_liq < 0:
        alertas_risco.append(f"Margem líquida negativa ({margem_liq:.2f}%).")
    if roe is not None and roe < 0:
        alertas_risco.append(f"ROE negativo ({roe:.1f}%): destruição de patrimônio.")
    if destruicao_historica:
        alertas_risco.append("Destruição contínua de capital recente (value trap).")

    # --- Múltiplos ---
    if pl is not None:
        if 0 < pl < 12 and not em_prejuizo:
            score += 15
            pontos_positivos.append(f"P/L atrativo ({pl:.1f}x)")
        elif pl > 25:
            score -= 10
            alertas_risco.append(f"P/L elevado ({pl:.1f}x)")
        elif pl < 0:
            score -= 10
            alertas_risco.append("P/L negativo: prejuízo nos últimos 12 meses.")

    if pvp is not None and 0 < pvp < 1.8 and not em_prejuizo:
        score += 10
        pontos_positivos.append(f"P/VP descontado ({pvp:.2f}x)")

    if roe is not None:
        if roe >= 15:
            score += 15
            pontos_positivos.append(f"Alta rentabilidade (ROE {roe:.1f}%)")
        elif 0 <= roe < 5:
            score -= 5
            alertas_risco.append(f"ROE baixo ({roe:.1f}%): pouca eficiência do capital.")

    if dy is not None and dy >= 6 and not em_prejuizo:
        score += 10
        pontos_positivos.append(f"Bons dividendos ({dy:.1f}%)")

    # --- Técnico ---
    if tendencia_grafica == "ALTA":
        score += 10
        pontos_positivos.append("Tendência gráfica de ALTA (preço > SMA50)")
    elif tendencia_grafica == "BAIXA":
        score -= 5
        alertas_risco.append("Tendência gráfica de BAIXA (preço < SMA50)")

    if rsi_val is not None:
        if rsi_val < 35:
            score += 10
            pontos_positivos.append(f"Sobrevenda (RSI {rsi_val}) — possível ponto de entrada")
        elif rsi_val > 70:
            score -= 10
            alertas_risco.append(f"Sobrecompra (RSI {rsi_val}) — papel esticado")

    # Cobertura baixa puxa o score para o neutro em vez de fabricar convicção
    # em cima de dado que não existe.
    if cobertura < 60:
        score = 50 + (score - 50) * (cobertura / 100)
        alertas_risco.append(
            f"Cobertura de dados de apenas {cobertura}% (faltando: {', '.join(ausentes)})."
        )

    score = int(max(5, min(98, score)))
    if em_prejuizo or destruicao_historica:
        score = min(score, 35)

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
    elif cobertura < 40:
        veredito = "DADOS INSUFICIENTES"

    if ausentes:
        alertas_risco.append(f"Sem dado de: {', '.join(ausentes)}.")

    return score, veredito, pontos_positivos, alertas_risco, cobertura


def _serie_close(df_precos: pd.DataFrame, ticker_yf: str) -> pd.Series | None:
    """Extrai a série de fechamento de um ticker do DataFrame multi-nível."""
    if df_precos is None or df_precos.empty:
        return None
    try:
        if isinstance(df_precos.columns, pd.MultiIndex):
            # get_level_values reflete o que realmente veio; .levels mantém
            # categorias de tickers que falharam no download.
            if ticker_yf not in df_precos.columns.get_level_values(0):
                return None
            serie = df_precos[ticker_yf]["Close"]
        else:
            serie = df_precos["Close"]
        return serie.dropna()
    except (KeyError, IndexError):
        return None


def _tecnicos(close: pd.Series) -> dict | None:
    """Preço, SMA50, tendência e RSI(14) de Wilder a partir da série de preços."""
    if close is None or len(close) < 20:
        return None

    preco = float(close.iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else None
    rsi_serie = rsi_wilder(close)
    ultimo_rsi = rsi_serie.iloc[-1] if len(rsi_serie) else None
    rsi_val = round(float(ultimo_rsi), 1) if pd.notna(ultimo_rsi) else None

    return {
        "preco": preco,
        "sma50": sma50,
        "rsi": rsi_val,
        "tendencia": ("ALTA" if preco > sma50 else "BAIXA") if sma50 is not None else "INDEFINIDA",
    }


@router.get("/scanner-quantamental")
def executar_scanner(min_cobertura: int = 0):
    """
    Varre o universo de ações. Resiliente a rate limit do Yahoo.

    min_cobertura: filtra ativos com menos de X% dos fundamentos disponíveis
    (0 = devolve tudo, com a cobertura marcada em cada linha).
    """
    # Resolve aliases ANTES de deduplicar. Sem isso, um alias que aponta para um
    # papel já presente no universo (ex.: AXIA3 -> ELET3) gerava a mesma empresa
    # duas vezes na tabela e uma requisição duplicada ao Yahoo.
    resolvidos: dict[str, str] = {}
    for t in UNIVERSO_ACOES:
        alvo = TICKER_ALIASES.get(t, t)
        resolvidos.setdefault(alvo, f"{alvo}.SA")

    lista_yf = list(resolvidos.values())

    df_precos = baixar_precos(lista_yf, period="6mo", interval="1d")

    fundamentos: dict[str, dict] = {}
    # 8 workers, não 25: acima disso o Yahoo devolve resposta truncada e o
    # ticker sumia do resultado. O semáforo em market_data reforça o teto.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futuros = {executor.submit(extrair_fundamentos, tyf): tyf for tyf in lista_yf}
        for futuro in concurrent.futures.as_completed(futuros):
            tyf = futuros[futuro]
            try:
                res = futuro.result()
            except Exception:
                res = None
            if res:
                fundamentos[tyf] = res

    resultados = []
    sem_dados = []

    for ticker_orig, ticker_yf in resolvidos.items():
        close = _serie_close(df_precos, ticker_yf)
        tec = _tecnicos(close)
        fund = fundamentos.get(ticker_yf)

        if tec is None and fund is None:
            sem_dados.append(ticker_orig)
            continue

        # Ativo sem fundamentos ainda entra na lista, marcado — antes ele era
        # descartado em silêncio e o scanner "perdia" 20 a 40 papéis por rodada.
        fund = fund or {"nome": ticker_orig, "pl": None, "pvp": None, "roe": None,
                        "dy": None, "margem_liq": None, "roe_origem": "indisponível"}
        tec = tec or {"preco": fund.get("preco"), "rsi": None, "tendencia": "INDEFINIDA"}

        score, veredito, _, alertas, cobertura = calcular_score_quantamental(
            pl=fund["pl"], pvp=fund["pvp"], roe=fund["roe"], dy=fund["dy"],
            margem_liq=fund["margem_liq"], tendencia_grafica=tec["tendencia"],
            rsi_val=tec["rsi"],
        )

        resultados.append({
            "ticker": ticker_orig,
            "nome": fund["nome"],
            "preco": _fmt(tec["preco"]),
            "pl": _fmt(fund["pl"]),
            "pvp": _fmt(fund["pvp"]),
            "roe": _fmt(fund["roe"]),
            "roe_origem": fund.get("roe_origem"),
            "dy": _fmt(fund["dy"]),
            "margem_liquida": _fmt(fund["margem_liq"]),
            "tendencia_grafica": tec["tendencia"],
            "rsi": tec["rsi"],
            "score_geral": score,
            "veredito": veredito,
            "cobertura_dados_pct": cobertura,
        })

    if min_cobertura > 0:
        resultados = [r for r in resultados if r["cobertura_dados_pct"] >= min_cobertura]

    resultados.sort(key=lambda x: (x["score_geral"], x["cobertura_dados_pct"]), reverse=True)

    com_roe = sum(1 for r in resultados if r["roe"] is not None)
    return {
        "total": len(resultados),
        "universo": len(lista_yf),
        "diagnostico": {
            "com_roe": com_roe,
            "sem_roe": len(resultados) - com_roe,
            "sem_dado_algum": sem_dados,
        },
        "oportunidades": resultados,
    }


@router.get("/acao/{ticker}")
def auditar_acao(ticker: str):
    ticker_clean = ticker.upper().strip().replace(".SA", "")
    ticker_consulta = TICKER_ALIASES.get(ticker_clean, ticker_clean)
    ticker_yf = f"{ticker_consulta}.SA"

    # profundo=True: se info não trouxer ROE, cai no balanço (Lucro TTM ÷ PL).
    fund = extrair_fundamentos(ticker_yf, profundo=True)
    if fund is None:
        return {"erro": f"Yahoo não retornou dados para {ticker_clean}. "
                        f"Verifique o código ou tente novamente em instantes (rate limit)."}

    # 10 anos de diário é ~2.500 linhas por request. Mensal dá o mesmo retorno
    # anual com ~120 linhas; o diário fica só nos 12 meses que alimentam SMA/RSI.
    df_longo = baixar_precos([ticker_yf], period="10y", interval="1mo")
    df_curto = baixar_precos([ticker_yf], period="1y", interval="1d")

    close_longo = _serie_close(df_longo, ticker_yf)
    close_curto = _serie_close(df_curto, ticker_yf)

    if close_curto is None or close_curto.empty:
        return {"erro": "Sem dados históricos disponíveis."}

    tec = _tecnicos(close_curto) or {}
    preco = fund.get("preco") or tec.get("preco")

    dados_10_anos = []
    retornos_anuais = []
    if close_longo is not None and not close_longo.empty:
        anual = close_longo.groupby(close_longo.index.year).agg(["first", "last", "max", "min"])
        ano_corrente = close_longo.index[-1].year
        for ano, linha in anual.iterrows():
            variacao = ((linha["last"] - linha["first"]) / linha["first"]) * 100
            # Ano corrente é parcial; conta no histórico mas não no teste de
            # destruição de capital, que compara anos fechados.
            if ano != ano_corrente:
                retornos_anuais.append(float(variacao))
            dados_10_anos.append({
                "ano": int(ano),
                "fechamento": round(float(linha["last"]), 2),
                "maxima": round(float(linha["max"]), 2),
                "minima": round(float(linha["min"]), 2),
                "retorno_ano_pct": round(float(variacao), 2),
                "parcial": ano == ano_corrente,
            })

    destruicao = (len(retornos_anuais) >= 3
                  and sum(retornos_anuais[-3:]) / 3 < -30)

    score, veredito, positivos, alertas, cobertura = calcular_score_quantamental(
        fund["pl"], fund["pvp"], fund["roe"], fund["dy"], fund["margem_liq"],
        tec.get("tendencia", "INDEFINIDA"), tec.get("rsi"), destruicao,
    )

    return {
        "ticker": ticker_clean,
        "nome_empresa": fund["nome"],
        "setor": fund.get("setor") or "N/A",
        "preco_atual": _fmt(preco),
        "recomendacao": veredito,
        "score_geral": score,
        "cobertura_dados_pct": cobertura,
        "analise_racional": positivos + alertas,
        "multiplos": {
            "p_l": _fmt(fund["pl"]),
            "p_vp": _fmt(fund["pvp"]),
            "ev_ebitda": _fmt(fund["ev_ebitda"]),
            "dividend_yield_pct": _fmt(fund["dy"]),
            "roe_pct": _fmt(fund["roe"]),
            "roe_fonte": fund["roe_origem"],
            "margem_liquida_pct": _fmt(fund["margem_liq"]),
        },
        "tecnico": {
            "sma50": _fmt(tec.get("sma50")),
            "rsi_14": tec.get("rsi"),
            "tendencia": tec.get("tendencia"),
        },
        "historico_10_anos": dados_10_anos,
    }


@router.get("/ticker-tape")
def get_ticker_tape():
    """Cotações para a barra de rolagem."""
    tickers = ["^BVSP", "USDBRL=X", "PETR4.SA", "VALE3.SA", "ITUB4.SA", "WEGE3.SA",
               "BBDC4.SA", "BBAS3.SA", "ELET3.SA", "RENT3.SA"]

    df = baixar_precos(tickers, period="5d", interval="1d")
    if df is None or df.empty:
        return []

    resultados = []
    for t in tickers:
        serie = _serie_close(df, t)
        if serie is None or len(serie) < 2:
            continue
        atual, anterior = float(serie.iloc[-1]), float(serie.iloc[-2])
        if anterior == 0:
            continue
        nome = t.replace(".SA", "").replace("^BVSP", "IBOV").replace("USDBRL=X", "USD/BRL")
        resultados.append({
            "ativo": nome,
            "preco": round(atual, 2),
            "variacao": round(((atual - anterior) / anterior) * 100, 2),
        })
    return resultados


@router.get("/diagnostico/{ticker}")
def diagnosticar_ticker(ticker: str):
    """
    Mostra os campos crus do Yahoo e como cada degrau da cascata de ROE se
    comporta. Use quando um papel específico voltar com fundamento faltando.
    """
    ticker_clean = ticker.upper().strip().replace(".SA", "")
    ticker_yf = f"{TICKER_ALIASES.get(ticker_clean, ticker_clean)}.SA"
    info = obter_info(ticker_yf)

    if not info:
        return {"ticker": ticker_clean, "erro": "Yahoo não retornou info (rate limit ou código inválido)."}

    campos = ["returnOnEquity", "trailingPE", "priceToBook", "bookValue",
              "sharesOutstanding", "netIncomeToCommon", "totalRevenue", "profitMargins",
              "dividendYield", "trailingAnnualDividendRate", "trailingAnnualDividendYield",
              "currentPrice", "regularMarketPrice", "enterpriseToEbitda", "sector"]

    return {
        "ticker": ticker_clean,
        "campos_yahoo": {c: info.get(c, "<ausente>") for c in campos},
        "total_campos_retornados": len(info),
        "fundamentos_calculados": extrair_fundamentos(ticker_yf, profundo=True),
    }


@router.post("/cache/limpar")
def limpar_cache_mercado():
    """Força a próxima chamada a buscar dados novos no Yahoo."""
    return {"entradas_removidas": limpar_cache()}
