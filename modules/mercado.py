"""Commodities, índices e câmbio — o pano de fundo do que move a B3.

Por que isto existe num terminal de renda variável brasileira: metade do IBOV
em peso é commodity. Minério manda em VALE e CSNA, petróleo manda em PETR,
PRIO e RECV, boi e grão mandam em BEEF, MBRF e SLCE, e o dólar manda em todo
mundo. Olhar múltiplo de mineradora sem olhar o minério é análise pela metade.

Fonte: endpoint de séries do Yahoo (`yf.download`), o mesmo do preço das ações
— e, diferente do `quoteSummary`, ele responde de datacenter, então funciona no
Render. Cotação de futuro tem atraso da própria bolsa de origem; a tela diz
isso.

Só existe leitura aqui. Nada é gravado.
"""

import threading
import time

import pandas as pd
import yfinance as yf

CACHE_TTL = 300          # 5 min: são referências de contexto, não execução
PERIODO = "5d"           # 5 dias cobrem feriado no meio sem perder o anterior

# (símbolo, rótulo, grupo, unidade). O rótulo é o que aparece na tela.
INSTRUMENTOS = [
    # --- commodities que movem o índice ---
    ("BZ=F", "Brent", "Energia", "US$/bbl"),
    ("CL=F", "WTI", "Energia", "US$/bbl"),
    ("TIO=F", "Minério 62%", "Metais", "US$/t"),
    ("GC=F", "Ouro", "Metais", "US$/oz"),
    ("SI=F", "Prata", "Metais", "US$/oz"),
    ("HG=F", "Cobre", "Metais", "US$/lb"),
    ("ZS=F", "Soja", "Agro", "¢/bu"),
    ("ZC=F", "Milho", "Agro", "¢/bu"),
    ("KC=F", "Café", "Agro", "¢/lb"),
    ("SB=F", "Açúcar", "Agro", "¢/lb"),
    ("LE=F", "Boi gordo (CME)", "Agro", "¢/lb"),
    # --- índices e câmbio ---
    ("^BVSP", "Ibovespa", "Índices", "pts"),
    ("^GSPC", "S&P 500", "Índices", "pts"),
    ("^IXIC", "Nasdaq", "Índices", "pts"),
    ("^VIX", "VIX", "Índices", "pts"),
    ("USDBRL=X", "Dólar", "Câmbio", "R$"),
    ("EURBRL=X", "Euro", "Câmbio", "R$"),
    ("DX-Y.NYB", "DXY", "Câmbio", "pts"),
    ("^TNX", "Treasury 10a", "Juros", "%"),
]

GRUPOS = ("Energia", "Metais", "Agro", "Índices", "Câmbio", "Juros")

_lock = threading.Lock()
_cache = {"payload": None, "carimbo": 0.0}


def _fechamentos(bruto, simbolos):
    """A coluna `Close` do download vetorizado, em qualquer dos formatos que o
    yfinance devolve (um símbolo vira Series, vários viram DataFrame)."""
    if bruto is None or getattr(bruto, "empty", True):
        return None
    try:
        fechamentos = bruto["Close"]
    except (KeyError, TypeError):
        return None
    if isinstance(fechamentos, pd.Series):
        fechamentos = fechamentos.to_frame(name=simbolos[0])
    return fechamentos


def _variacao(serie):
    """(último, variação %) ou None. Exige dois fechamentos — um só não é
    variação, e mostrar 0,00% nesse caso seria afirmar algo falso."""
    validos = serie.dropna()
    if len(validos) < 2:
        return None
    try:
        atual = float(validos.iloc[-1])
        anterior = float(validos.iloc[-2])
    except (TypeError, ValueError):
        return None
    if anterior == 0 or atual != atual:
        return None
    return atual, (atual - anterior) / anterior * 100.0


def coletar(forcar=False):
    """[{simbolo, nome, grupo, unidade, preco, variacao}]. Nunca levanta.

    Instrumento que não vier fica de fora da lista, e o painel mostra quantos
    dos esperados chegaram — some em silêncio é o que fazia o scanner encolher
    sem ninguém perceber.
    """
    agora = time.time()
    with _lock:
        payload = _cache["payload"]
        idade = agora - _cache["carimbo"]
    if payload is not None and not forcar and idade < CACHE_TTL:
        return payload

    simbolos = [i[0] for i in INSTRUMENTOS]
    try:
        bruto = yf.download(simbolos, period=PERIODO, interval="1d",
                            progress=False, auto_adjust=True)
    except Exception:  # noqa: BLE001
        return payload or []

    fechamentos = _fechamentos(bruto, simbolos)
    if fechamentos is None:
        return payload or []

    resultados = []
    for simbolo, nome, grupo, unidade in INSTRUMENTOS:
        if simbolo not in fechamentos.columns:
            continue
        medida = _variacao(fechamentos[simbolo])
        if medida is None:
            continue
        preco, variacao = medida
        resultados.append({
            "simbolo": simbolo, "nome": nome, "grupo": grupo, "unidade": unidade,
            "preco": round(preco, 2), "variacao": round(variacao, 2),
        })

    with _lock:
        _cache["payload"] = resultados
        _cache["carimbo"] = agora
    return resultados


def por_grupo(itens=None):
    """Mesma lista, agrupada na ordem em que a tela mostra."""
    itens = itens if itens is not None else coletar()
    agrupado = {grupo: [] for grupo in GRUPOS}
    for item in itens:
        agrupado.setdefault(item["grupo"], []).append(item)
    return {grupo: linhas for grupo, linhas in agrupado.items() if linhas}


def limpar_cache():
    with _lock:
        _cache["payload"] = None
        _cache["carimbo"] = 0.0
