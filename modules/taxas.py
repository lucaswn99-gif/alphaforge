"""Taxa livre de risco, lida do Banco Central.

Série 432 do SGS é a meta Selic definida pelo Copom. Buscar em vez de cravar
importa porque o Sharpe é sensível à taxa livre de risco: uma constante no
código continua rodando depois de cada reunião do Copom, produzindo índice
errado sem sinal nenhum de que envelheceu.

Degradação, igual à da carteira do IBOV: BCB ao vivo -> cache em memória ->
constante embutida, e o consumidor sempre sabe qual dos três veio.
"""

import threading
import time

import requests

URL_SGS_SELIC_META = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json"
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
