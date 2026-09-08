"""Composição da carteira teórica do IBOV, lida direto do B3.

Antes o universo do scanner era uma lista fixa no código, que envelhecia a
cada rebalanceamento quadrimestral do índice. Aqui a carteira vem do próprio
B3, com três camadas de degradação para o scanner nunca ficar vazio:

    B3 ao vivo  ->  cache em disco (12 h)  ->  lista embutida (fallback)

O endpoint é o mesmo que a página de índices do B3 consome: os parâmetros vão
codificados em base64 no path.
"""

import base64
import json
import os
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_BASE = "https://sistemaswebb3-listados.b3.com.br/indexProxy/indexCall/GetPortfolioDay/{payload}"
PARAMETROS = {"language": "pt-br", "pageNumber": 1, "pageSize": 200, "index": "IBOV", "segment": "1"}
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}

TIMEOUT = 12
CACHE_TTL = 12 * 3600
# No Render o disco é efêmero e o cache simplesmente não sobrevive ao deploy —
# o que só custa uma ida ao B3. ALPHAFORGE_CACHE_DIR permite apontar para um
# volume, se um dia houver.
CACHE_PATH = os.path.join(
    os.environ.get("ALPHAFORGE_CACHE_DIR") or os.path.dirname(os.path.abspath(__file__)),
    "cache_ibov.json",
)

# Carteira teórica do IBOV capturada do B3 em 2026-09-08 (76 papéis). Serve só
# como último recurso: se este bloco for usado, a resposta do scanner marca
# origem="fallback" para o dado velho não passar por atual.
IBOV_FALLBACK = [
    "ALOS3", "ABEV3", "ASAI3", "AURE3", "AXIA3", "AZZA3", "B3SA3", "BBSE3", "BBDC3", "BBDC4",
    "BRAP4", "BBAS3", "BRAV3", "BPAC11", "CXSE3", "CEAB3", "CMIG4", "COGN3", "CSMG3", "CPLE3",
    "CSAN3", "CPFE3", "CMIN3", "CURY3", "CYRE3", "DIRR3", "EMBJ3", "ENGI11", "ENEV3", "EGIE3",
    "EQTL3", "FLRY3", "GGBR4", "GOAU4", "HAPV3", "HYPE3", "IGTI11", "ISAE4", "ITSA4", "ITUB4",
    "KLBN11", "RENT3", "LREN3", "MGLU3", "POMO4", "MBRF3", "BEEF3", "MOTV3", "MRVE3", "MULT3",
    "NATU3", "PETR3", "PETR4", "PSSA3", "PRIO3", "RADL3", "RDOR3", "RAIL3", "SBSP3", "SANB11",
    "CSNA3", "SMFT3", "SUZB3", "TAEE11", "VIVT3", "TEND3", "TIMS3", "TOTS3", "UGPA3", "USIM5",
    "VALE3", "VAMO3", "VBBR3", "VIVA3", "WEGE3", "YDUQ3",
]

# Quantidade mínima plausível para uma carteira do IBOV. Abaixo disso a
# resposta é considerada truncada e não substitui o cache.
MINIMO_PLAUSIVEL = 50


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

    return list(IBOV_FALLBACK), "fallback", None
