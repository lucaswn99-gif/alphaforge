"""Segmento de cada FII: tijolo, papel ou fundo de fundos.

Por que isto é uma LISTA À MÃO e não uma dedução: não existe fonte pública
que classifique FII por segmento de forma confiável e estável. O Informe
Mensal da CVM não traz segmento; o ticker não carrega essa informação; e
inferir por comportamento de preço (papel oscila menos, P/VP colado em 1,00)
acerta na média e erra no caso concreto — que é justamente o que aparece na
tela do cliente.

Então: o que está aqui foi classificado à mão, e o que não está aparece como
"não classificado" em vez de receber um palpite. Mesmo princípio do resto do
projeto — "não apurado nunca vira zero".

Para corrigir ou acrescentar sem mexer no código, crie `segmentos_fii_manual.json`
na raiz do projeto:

    {"HGLG11": "tijolo", "KNIP11": "papel", "RBRF11": "fof"}

O arquivo manual sempre vence esta lista.
"""

import json
import os

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO_MANUAL = os.path.join(os.path.dirname(PASTA), "segmentos_fii_manual.json")

TIJOLO = "tijolo"
PAPEL = "papel"
FOF = "fof"
NAO_CLASSIFICADO = "nao_classificado"

SEGMENTOS_VALIDOS = (TIJOLO, PAPEL, FOF)

# Rótulos para a tela. Ficam aqui, junto da classificação, para a API e o
# frontend não divergirem no nome do mesmo segmento.
ROTULOS = {
    TIJOLO: "Tijolo",
    PAPEL: "Papel",
    FOF: "Fundo de fundos",
    NAO_CLASSIFICADO: "Não classificado",
}

# --------------------------------------------------------------------------
# Tijolo: o fundo é dono de imóvel — galpão, shopping, laje, agência, hospital.
# A renda vem de aluguel, e o valor patrimonial é reavaliação de imóvel.
# --------------------------------------------------------------------------
_TIJOLO = [
    # Logística e galpões
    "HGLG11", "BTLG11", "VILG11", "XPLG11", "LVBI11", "BRCO11", "GGRC11",
    "SDIL11", "VTLT11", "ALZR11",
    # Shoppings
    "XPML11", "VISC11", "MALL11", "HGBS11", "HSML11", "PQDP11", "ABCP11",
    # Lajes corporativas
    "PVBI11", "HGRE11", "BRCR11", "RCRB11", "TEPP11", "JSRE11", "EDGA11",
    "HGPO11", "VINO11", "ONEF11",
    # Renda urbana, varejo e agências bancárias
    "HGRU11", "TRXF11", "RBVA11", "BBPO11", "SAAG11",
    # Híbrido (lajes + logística), tratado como tijolo porque a receita é aluguel
    "KNRI11",
    # Hospitalar, educacional e hotelaria
    "NSLU11", "RBED11", "HTMX11",
]

# --------------------------------------------------------------------------
# Papel: o fundo é dono de CRI/recebível, não de imóvel. A renda vem de juros
# e correção (IPCA ou CDI), e o valor patrimonial é marcação dos títulos —
# por isso o P/VP de papel vive colado em 1,00, e um desconto grande aqui
# significa coisa bem diferente de um desconto em tijolo.
# --------------------------------------------------------------------------
_PAPEL = [
    "KNIP11", "KNCR11", "KNSC11", "KNHY11", "IRDM11", "CPTS11", "MXRF11",
    "HGCR11", "MCCI11", "RECR11", "CVBI11", "VRTA11", "RBRR11", "RBRY11",
    "VGIR11", "VGIP11", "HABT11", "DEVA11", "URPR11", "VCJR11", "PLCR11",
    "BTCI11", "AFHI11", "OUJP11", "BCRI11", "ARRI11", "SNCI11", "HCTR11",
    "KCRE11", "CACR11",
]

# --------------------------------------------------------------------------
# Fundo de fundos: compra cotas de outros FIIs. Não é tijolo nem papel — é
# uma camada acima, com taxa em cima de taxa, e merece leitura própria.
# --------------------------------------------------------------------------
_FOF = [
    "RBRF11", "BCFF11", "HFOF11", "MGFF11", "KFOF11", "XPSF11", "CXRI11",
    "BPFF11", "RFOF11",
]

_BASE = {}
for _lista, _segmento in ((_TIJOLO, TIJOLO), (_PAPEL, PAPEL), (_FOF, FOF)):
    for _ticker in _lista:
        _BASE[_ticker] = _segmento

_memoria = {"manual": None}


def _carregar_manual(caminho=None):
    if _memoria["manual"] is not None:
        return _memoria["manual"]
    mapa = {}
    try:
        with open(caminho or ARQUIVO_MANUAL, "r", encoding="utf-8") as arquivo:
            for ticker, segmento in (json.load(arquivo) or {}).items():
                segmento = str(segmento or "").strip().lower()
                if segmento in SEGMENTOS_VALIDOS:
                    mapa[str(ticker).strip().upper()] = segmento
    except (OSError, ValueError, AttributeError):
        pass
    _memoria["manual"] = mapa
    return mapa


def segmento_do_ticker(ticker, caminho_manual=None):
    """"tijolo", "papel", "fof" ou "nao_classificado". Nunca levanta."""
    ticker = str(ticker or "").strip().upper()
    if not ticker:
        return NAO_CLASSIFICADO
    manual = _carregar_manual(caminho_manual)
    return manual.get(ticker) or _BASE.get(ticker) or NAO_CLASSIFICADO


def tickers_do_segmento(segmento, caminho_manual=None):
    """Todos os tickers classificados num segmento, já com o manual aplicado."""
    segmento = str(segmento or "").strip().lower()
    mapa = dict(_BASE)
    mapa.update(_carregar_manual(caminho_manual))
    return sorted(t for t, s in mapa.items() if s == segmento)


def rotulo(segmento):
    return ROTULOS.get(segmento, ROTULOS[NAO_CLASSIFICADO])


def limpar_memoria():
    _memoria["manual"] = None
