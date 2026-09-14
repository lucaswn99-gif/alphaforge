"""Composição do índice IFIX, lida direto do B3.

Mesmo desenho de `composicao_ibov.py` (mesma API do B3, mesmas três camadas de
degradação) — só troca o parâmetro `index`. Ver aquele módulo para o raciocínio
completo; aqui só o que muda.

    B3 ao vivo  ->  cache em disco (12 h)  ->  lista embutida (fallback)
"""

import base64
import json
import os
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_BASE = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{payload}"
PARAMETROS = {"language": "pt-br", "pageNumber": 1, "pageSize": 300, "index": "IFIX", "segment": "2"}
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

TIMEOUT = 12
CACHE_TTL = 12 * 3600
CACHE_PATH = os.path.join(
    os.environ.get("ALPHAFORGE_CACHE_DIR") or os.path.dirname(os.path.abspath(__file__)),
    "cache_ifix.json",
)

# Fallback: os FIIs já curados manualmente em `routers/wealth.py` (tijolo +
# papel), a lista que o radar usava antes desta mudança. Não é o IFIX inteiro,
# mas é uma carteira real e testada — infinitamente melhor que o radar voltar
# vazio se o B3 estiver fora do ar.
IFIX_FALLBACK = [
    "HGLG11", "BTLG11", "XPML11", "VISC11", "ALZR11",
    "KNRI11", "VILG11", "MALL11", "PVBI11", "BRCR11",
    "KNIP11", "KNCR11", "IRDM11", "CPTS11", "MXRF11",
    "HGCR11", "MCCI11", "RECR11", "CVBI11", "VRTA11",
]

# O IFIX tem bem mais papéis que essas 20 curadas, mas nenhuma resposta do B3
# pode substituir a lista por menos que isso — sinal de página truncada, igual
# ao IBOV.
MINIMO_PLAUSIVEL = 20


def _montar_url():
    payload = base64.b64encode(json.dumps(PARAMETROS).encode("utf-8")).decode("ascii")
    return URL_BASE.format(payload=payload)


def _ler_cache():
    try:
        with open(CACHE_PATH, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        codigos = [str(c).upper() for c in dados.get("codigos", [])]
        carimbo = float(dados.get("carimbo", 0.0))
        if len(codigos) >= MINIMO_PLAUSIVEL:
            return codigos, carimbo
    except (OSError, ValueError, TypeError):
        pass
    return None, 0.0


def _gravar_cache(codigos):
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as arquivo:
            json.dump({"codigos": codigos, "carimbo": time.time()}, arquivo, ensure_ascii=False)
    except OSError:
        pass  # cache é otimização, nunca requisito


def _buscar_no_b3():
    """Devolve a lista de códigos do B3 ou None. Nunca levanta."""
    url = _montar_url()
    for verificar_tls in (True, False):
        try:
            resposta = requests.get(url, headers=CABECALHOS, timeout=TIMEOUT, verify=verificar_tls)
            if resposta.status_code != 200:
                continue
            corpo = resposta.json()
        except Exception:  # noqa: BLE001 - qualquer falha cai para cache/fallback
            continue

        resultados = corpo.get("results") if isinstance(corpo, dict) else None
        if not isinstance(resultados, list):
            continue

        codigos = []
        for item in resultados:
            codigo = (item or {}).get("cod")
            if codigo:
                codigo = str(codigo).strip().upper()
                if codigo not in codigos:
                    codigos.append(codigo)

        if len(codigos) >= MINIMO_PLAUSIVEL:
            return codigos
    return None


def obter_composicao(forcar=False):
    """Devolve (codigos, origem, idade_segundos).

    origem: "b3" (rede), "cache" (disco, < 12 h) ou "fallback" (lista embutida).
    """
    codigos_cache, carimbo = _ler_cache()
    idade = time.time() - carimbo if carimbo else None

    if codigos_cache and not forcar and idade is not None and idade < CACHE_TTL:
        return codigos_cache, "cache", int(idade)

    codigos = _buscar_no_b3()
    if codigos:
        _gravar_cache(codigos)
        return codigos, "b3", 0

    # Rede falhou: cache velho ainda é melhor que lista congelada no código.
    if codigos_cache:
        return codigos_cache, "cache", int(idade) if idade is not None else None

    return list(IFIX_FALLBACK), "fallback", None
