"""ETFs listados na B3, por categoria.

Lista curada à mão, pelo mesmo motivo de `segmentos_fii.py`: o endpoint de
fundos listados do B3 (`fundsProxy/GetListedFundsSIG`, usado por
`cadastro_fii.py`) não responde de forma confiável, e não há outra fonte
pública estável que devolva "os ETFs mais negociados" com categoria.

A lista é deliberadamente ampla — ETF que não existe mais, mudou de código ou
não tem histórico no Yahoo simplesmente NÃO aparece na tela: `_montar_etfs`
em `routers/wealth.py` descarta quem não tem pregões suficientes para a média
de 20. Por isso incluir um código a mais custa zero na tela, e deixar um de
fora custa um ativo que você não vê. O radar informa quantos foram
consultados e quantos responderam, para a diferença ficar visível em vez de
silenciosa.

Para acrescentar ou remover sem mexer no código, crie `etfs_manual.json` na
raiz do projeto:

    {"BOVA11": "indice_amplo", "NOVO11": "internacional", "XINA11": null}

Valor `null` remove o ETF da lista. O arquivo manual sempre vence.
"""

import json
import os

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_MANUAL = os.path.join(os.path.dirname(PASTA), "etfs_manual.json")

ROTULOS = {
    "indice_amplo": "Índice amplo",
    "small_caps": "Small caps",
    "dividendos": "Dividendos",
    "setorial": "Setorial e temático",
    "internacional": "Internacional",
    "cripto": "Cripto",
    "renda_fixa": "Renda fixa",
    "ouro": "Ouro",
}

# Ibovespa e IBrX — o grosso do volume de ETF da bolsa passa por aqui.
_INDICE_AMPLO = ["BOVA11", "BOVV11", "BOVB11", "XBOV11", "PIBB11", "BRAX11"]
_SMALL_CAPS = ["SMAL11", "SMAC11"]
_DIVIDENDOS = ["DIVO11", "BBSD11"]
# Recortes setoriais e de índices temáticos da B3.
_SETORIAL = ["FIND11", "MATB11", "ECOO11", "ISUS11", "GOVE11"]
# Exposição em bolsa estrangeira sem sair do CPF/CNPJ brasileiro.
_INTERNACIONAL = ["IVVB11", "SPXI11", "NASD11", "WRLD11", "ACWI11", "EURP11",
                  "XINA11", "ASIA11"]
_CRIPTO = ["HASH11", "BITH11", "QBTC11", "QETH11", "ETHE11"]
# Índices de renda fixa (IMA-B, IRF-M) — úteis como referência de carteira.
_RENDA_FIXA = ["IMAB11", "IB5M11", "B5P211", "IRFM11", "FIXA11"]
_OURO = ["GOLD11"]

_BASE = {}
for _lista, _categoria in (
    (_INDICE_AMPLO, "indice_amplo"),
    (_SMALL_CAPS, "small_caps"),
    (_DIVIDENDOS, "dividendos"),
    (_SETORIAL, "setorial"),
    (_INTERNACIONAL, "internacional"),
    (_CRIPTO, "cripto"),
    (_RENDA_FIXA, "renda_fixa"),
    (_OURO, "ouro"),
):
    for _ticker in _lista:
        _BASE[_ticker] = _categoria

_memoria = {"manual": None}


def _carregar_manual(caminho=None):
    """{ticker: categoria|None}. None marca remoção. Nunca levanta."""
    if _memoria["manual"] is not None:
        return _memoria["manual"]
    mapa = {}
    try:
        with open(caminho or ARQUIVO_MANUAL, "r", encoding="utf-8") as arquivo:
            for ticker, categoria in (json.load(arquivo) or {}).items():
                ticker = str(ticker).strip().upper()
                if not ticker:
                    continue
                if categoria is None:
                    mapa[ticker] = None
                    continue
                categoria = str(categoria).strip().lower()
                if categoria in ROTULOS:
                    mapa[ticker] = categoria
    except (OSError, ValueError, AttributeError):
        pass
    _memoria["manual"] = mapa
    return mapa


def mapa_etfs(caminho_manual=None):
    """{ticker: categoria}, já com o manual aplicado (e as remoções feitas)."""
    mapa = dict(_BASE)
    for ticker, categoria in _carregar_manual(caminho_manual).items():
        if categoria is None:
            mapa.pop(ticker, None)
        else:
            mapa[ticker] = categoria
    return mapa


def tickers(caminho_manual=None):
    """Todos os códigos, ordenados por categoria e depois por código —
    é nessa ordem que a tabela da tela sai."""
    mapa = mapa_etfs(caminho_manual)
    ordem = list(ROTULOS)
    return sorted(mapa, key=lambda t: (ordem.index(mapa[t]), t))


def categoria_do_ticker(ticker, caminho_manual=None):
    return mapa_etfs(caminho_manual).get(str(ticker or "").strip().upper())


def rotulo(categoria):
    return ROTULOS.get(categoria, "—")


def limpar_memoria():
    _memoria["manual"] = None
