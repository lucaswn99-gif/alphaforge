"""Taxa livre de risco e séries de indexador, lidas do Banco Central.

Série 432 do SGS é a meta Selic definida pelo Copom. Buscar em vez de cravar
importa porque o Sharpe é sensível à taxa livre de risco: uma constante no
código continua rodando depois de cada reunião do Copom, produzindo índice
errado sem sinal nenhum de que envelheceu.

Degradação, igual à da carteira do IBOV: BCB ao vivo -> cache em memória ->
constante embutida, e o consumidor sempre sabe qual dos três veio.

**As séries históricas (CDI, série 12; IPCA, série 433) são diferentes: não
têm fallback sintético.** Servem para marcar renda fixa na curva em
`modules/renda_fixa.py`, e inventar um dia de CDI ou um mês de IPCA produziria
uma rentabilidade que não aconteceu — o mesmo princípio de "não apurado nunca
vira zero", aplicado ao lado oposto: também não vira um número chutado. BCB
fora do ar e sem cache -> `None`, e quem chama mostra a posição pelo valor
aplicado, sinalizando que a correção ainda não foi apurada.
"""

import threading
import time

import requests

URL_SGS_SELIC_META = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json"
URL_SGS_CDI = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
               "?formato=json&dataInicial={ini}&dataFinal={fim}")
URL_SGS_IPCA = ("https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados"
                "?formato=json&dataInicial={ini}&dataFinal={fim}")
TIMEOUT = 8
CACHE_TTL = 6 * 3600

# Capturado do SGS em 08/09/2026 (vigência a partir de 16/09/2026). Só entra em
# cena se o BCB estiver fora do ar — e o campo `origem` denuncia quando entrou.
SELIC_FALLBACK = 14.00
SELIC_FALLBACK_DATA = "16/09/2026"

# Faixa de plausibilidade: a Selic nunca esteve fora disto na era do regime de
# metas, então valor fora daqui é resposta corrompida, não notícia.
SELIC_MINIMA = 0.5
SELIC_MAXIMA = 60.0

_cache = {"valor": None, "data": None, "carimbo": 0.0}
_lock = threading.Lock()


def _buscar_no_bcb():
    """(valor, data) do SGS, ou (None, None). Nunca levanta."""
    try:
        resposta = requests.get(URL_SGS_SELIC_META, timeout=TIMEOUT)
        if resposta.status_code != 200:
            return None, None
        corpo = resposta.json()
    except Exception:  # noqa: BLE001
        return None, None

    if not isinstance(corpo, list) or not corpo:
        return None, None

    registro = corpo[-1]
    if not isinstance(registro, dict):
        return None, None

    try:
        valor = float(str(registro.get("valor")).replace(",", "."))
    except (TypeError, ValueError):
        return None, None

    if not (SELIC_MINIMA <= valor <= SELIC_MAXIMA):
        return None, None

    return valor, registro.get("data")


def obter_selic_meta(forcar=False):
    """Devolve {'valor', 'data', 'origem'} — origem: bcb | cache | fallback."""
    agora = time.time()
    with _lock:
        em_cache = _cache["valor"]
        idade = agora - _cache["carimbo"]

    if em_cache is not None and not forcar and idade < CACHE_TTL:
        return {"valor": em_cache, "data": _cache["data"], "origem": "cache"}

    valor, data = _buscar_no_bcb()
    if valor is not None:
        with _lock:
            _cache.update({"valor": valor, "data": data, "carimbo": time.time()})
        return {"valor": valor, "data": data, "origem": "bcb"}

    if em_cache is not None:
        return {"valor": em_cache, "data": _cache["data"], "origem": "cache"}

    return {"valor": SELIC_FALLBACK, "data": SELIC_FALLBACK_DATA, "origem": "fallback"}


# --------------------------------------------------------------------------
# Séries históricas (CDI, IPCA) — para marcação na curva de renda fixa
# --------------------------------------------------------------------------

_cache_series = {}
_lock_series = threading.Lock()
CACHE_TTL_SERIE = 6 * 3600


def _buscar_serie_no_bcb(url_modelo, data_inicio, data_fim):
    """Lista [{'data': 'dd/mm/aaaa', 'valor': float}] ou None. Nunca levanta."""
    url = url_modelo.format(ini=data_inicio.strftime("%d/%m/%Y"),
                            fim=data_fim.strftime("%d/%m/%Y"))
    try:
        resposta = requests.get(url, timeout=TIMEOUT)
        if resposta.status_code != 200:
            return None
        corpo = resposta.json()
    except Exception:  # noqa: BLE001
        return None

    if not isinstance(corpo, list):
        return None

    serie = []
    for registro in corpo:
        if not isinstance(registro, dict):
            continue
        data = registro.get("data")
        if not data:
            continue
        try:
            valor = float(str(registro.get("valor")).replace(",", "."))
        except (TypeError, ValueError):
            continue
        serie.append({"data": data, "valor": valor})
    return serie


def _serie_com_cache(codigo, url_modelo, data_inicio, data_fim):
    chave = (codigo, data_inicio.isoformat(), data_fim.isoformat())
    agora = time.time()
    with _lock_series:
        em_cache = _cache_series.get(chave)

    if em_cache is not None and agora - em_cache[0] < CACHE_TTL_SERIE:
        return em_cache[1]

    serie = _buscar_serie_no_bcb(url_modelo, data_inicio, data_fim)
    if serie is not None:
        with _lock_series:
            _cache_series[chave] = (agora, serie)
        return serie

    if em_cache is not None:
        return em_cache[1]

    return None


def serie_cdi(data_inicio, data_fim):
    """[{'data','valor'}] da série 12 do SGS — CDI, taxa % ao dia, um ponto
    por dia útil (o próprio BCB não publica fim de semana/feriado, então a
    série já vem só nos dias que devem compor o acúmulo). `None` se o BCB
    estiver fora do ar e não houver cache — sem fallback sintético."""
    return _serie_com_cache("cdi", URL_SGS_CDI, data_inicio, data_fim)


def serie_ipca(data_inicio, data_fim):
    """Como `serie_cdi`, para a série 433 — IPCA, variação % no mês, um ponto
    por mês fechado."""
    return _serie_com_cache("ipca", URL_SGS_IPCA, data_inicio, data_fim)
