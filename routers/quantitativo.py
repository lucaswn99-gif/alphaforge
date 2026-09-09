"""Motor multicritério: momentum, Bazin e Greenblatt sobre o mesmo universo.

Três estratégias distintas, não três variações da mesma. Um papel de dividendo
não pontua em momentum e um papel de momentum não pontua em Bazin — por isso o
`score_quant` composto é o MELHOR segmento, e não a média: média puniria o
especialista, que é justamente o que cada estratégia procura.

O scanner fundamentalista (`/renda-variavel/scanner-quantamental`) continua
intocado. Este é um motor ao lado, com o mesmo universo e a mesma carga de
preços — nenhuma requisição adicional ao Yahoo.

    ticker -> série de preço (Yahoo)   -> momentum
           -> proventos da série       -> Bazin (com LPA e dívida da CVM)
           -> balanço da CVM           -> Greenblatt
"""

import threading
import time

import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Query

from modules import cadastro_b3, fundamentos_cvm, identidade, quant
from routers.equity import (calcular_rsi_wilder, carimbo_de_coleta, fatiar_precos,
                            flag, montar_universo, resolver_universo)

router = APIRouter(prefix="/api/quant", tags=["quantitativo"])

CACHE_TTL = 900          # 15 min, alinhado ao atraso da própria fonte de preço
HISTORICO = "2y"         # cobre EMA50, percentil de Bollinger e DY de 12 meses
SEGMENTOS = ("momentum", "bazin", "greenblatt")

# Recuperação judicial não existe como campo na DFP nem no cadastro do B3. Esta
# lista é MANUAL e a tela diz isso — inventar o dado seria pior que não tê-lo.
# Edite aqui quando precisar excluir um papel do ranking de Greenblatt.
EM_RECUPERACAO_JUDICIAL = ()

_lock = threading.Lock()
_cache = {"payload": None, "carimbo": 0.0}


def _coluna(sub, nome):
    """Coluna do sub-dataframe, ou None. Nem todo papel volta com Dividends."""
    if sub is None or nome not in getattr(sub, "columns", []):
        return None
    serie = sub[nome].dropna()
    return serie if len(serie) else None


def _balanco(codigo):
    cnpj = cadastro_b3.cnpj_do_ticker(codigo)
    if not cnpj:
        return None, None
    return cnpj, fundamentos_cvm.balanco_por_cnpj(cnpj)


def _num(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n else None


def avaliar_papel(codigo, sub):
    """Um papel nos três segmentos. Nunca levanta; campo ausente vira None."""
    close = _coluna(sub, "Close")
    if close is None or len(close) < quant.EMA_LONGA:
        return None

    preco = float(close.iloc[-1])
    volume = _coluna(sub, "Volume")
    ifr = calcular_rsi_wilder(close)

    marca = identidade.identidade(codigo)
    cnpj, balanco = _balanco(codigo)
    balanco = balanco or {}

    # --- momentum: só série de preço e volume ------------------------------
    momentum = quant.avaliar_momentum(close, volume, ifr)

    # --- Bazin: proventos da série + lucro e dívida da CVM -----------------
    dividendos = _coluna(sub, "Dividends")
    dpa = quant.dividendos_12m(dividendos, indice_precos=close.index)
    dy = (dpa / preco * 100.0) if (dpa is not None and preco > 0) else None
    divida_liq = quant.divida_liquida(balanco.get("divida_curto_prazo"),
                                      balanco.get("divida_longo_prazo"),
                                      balanco.get("caixa"))
    bazin = quant.avaliar_bazin(preco, dpa, dy, balanco.get("lpa_on"),
                                divida_liq, balanco.get("ebit"))

    # --- Greenblatt: só balanço --------------------------------------------
    acoes = quant.acoes_implicitas(balanco.get("lucro_liquido"), balanco.get("lpa_on"))
    ev = quant.enterprise_value(preco, acoes, divida_liq)
    ev_ebit = quant.ev_sobre_ebit(ev, balanco.get("ebit"))
    capital = quant.capital_empregado(balanco.get("ativo_total"),
                                      balanco.get("passivo_circulante"))
    retorno = quant.roic(balanco.get("ebit"), capital)

    # Financeira e recuperação judicial ficam fora do ranking — mas continuam
    # na tabela com momentum e Bazin. Excluir do ranking não é excluir do radar.
    elegivel = (marca["setor"] not in quant.SETORES_EXCLUIDOS
                and codigo not in EM_RECUPERACAO_JUDICIAL)
    motivo_exclusao = None
    if marca["setor"] in quant.SETORES_EXCLUIDOS:
        motivo_exclusao = "instituição financeira: EV/EBIT e ROIC não a descrevem"
    elif codigo in EM_RECUPERACAO_JUDICIAL:
        motivo_exclusao = "em recuperação judicial (lista manual)"

    return {
        "ticker": codigo,
        "identidade": marca,
        "cnpj": cnpj,
        "exercicio_cvm": balanco.get("ano"),
        "preco": round(preco, 2),
        "momentum": momentum,
        "bazin": bazin,
        "greenblatt": {
            "ev_ebit": ev_ebit, "roic": retorno, "enterprise_value": ev,
            "acoes_implicitas": acoes, "capital_empregado": capital,
            "elegivel": elegivel, "motivo_exclusao": motivo_exclusao,
        },
        "scores": {"momentum": quant.score_momentum(momentum),
                   "bazin": quant.score_bazin(bazin),
                   "greenblatt": None},   # preenchido depois do ranking
    }


def montar(forcar=False):
    """Universo inteiro nos três segmentos. Uma carga de preço, um resultado."""
    agora = time.time()
    with _lock:
        payload, idade = _cache["payload"], agora - _cache["carimbo"]
    if payload is not None and not forcar and idade < CACHE_TTL:
        return {**payload, "cache": True, "idade_segundos": int(idade)}

    mapa = resolver_universo(forcar=forcar)
    _, meta = montar_universo(forcar=forcar)
    simbolos = list(mapa)
    try:
        bruto = yf.download(simbolos, period=HISTORICO, interval="1d", progress=False,
                            group_by="ticker", auto_adjust=True, actions=True)
    except Exception as exc:  # noqa: BLE001
        return {"erro": f"Falha ao baixar preços: {type(exc).__name__}",
                **carimbo_de_coleta()}

    if bruto is None or getattr(bruto, "empty", True):
        return {"erro": "A fonte de preços não devolveu série alguma.",
                **carimbo_de_coleta()}

    papeis, falhas = [], []
    for simbolo, codigo in mapa.items():
        try:
            avaliado = avaliar_papel(codigo, fatiar_precos(bruto, simbolo))
        except Exception as exc:  # noqa: BLE001
            falhas.append({"ticker": codigo, "motivo": f"cálculo: {type(exc).__name__}"})
            continue
        if avaliado is None:
            falhas.append({"ticker": codigo, "motivo": "histórico insuficiente"})
            continue
        papeis.append(avaliado)

    # O ranking de Greenblatt é RELATIVO: só existe depois que todo o universo
    # elegível foi apurado. Por isso ele fecha aqui, e não dentro do laço.
    candidatos = [{"ticker": p["ticker"], **p["greenblatt"]}
                  for p in papeis if p["greenblatt"]["elegivel"]]
    ranking = quant.ranking_greenblatt(candidatos)
    total = len(ranking)
    por_ticker = {r["ticker"]: r for r in ranking}

    for p in papeis:
        posicao = por_ticker.get(p["ticker"])
        if posicao:
            p["greenblatt"].update(
                rank=posicao["rank_greenblatt"],
                posicao_ev_ebit=posicao["posicao_ev_ebit"],
                posicao_roic=posicao["posicao_roic"],
                universo_ranking=total,
            )
            p["scores"]["greenblatt"] = quant.score_greenblatt(
                posicao["rank_greenblatt"], total)
        composto, segmento = quant.compor_score_quant(p["scores"])
        p["score_quant"] = composto
        p["segmento_dominante"] = segmento

    papeis.sort(key=lambda p: (p["score_quant"] is None, -(p["score_quant"] or 0)))

    resultado = {
        **carimbo_de_coleta(), **meta,
        "papeis": papeis,
        "total": len(papeis),
        "falhas": falhas,
        "aprovados": {s: sum(1 for p in papeis if p.get(s, {}).get("aprovado"))
                      for s in ("momentum", "bazin")},
        "universo_greenblatt": total,
        "em_recuperacao_judicial": list(EM_RECUPERACAO_JUDICIAL),
        "ressalvas": [
            "Alavancagem calculada sobre EBIT, não EBITDA: a DFP não padroniza "
            "depreciação. O número sai maior que a DL/EBITDA real — o filtro erra "
            "para reprovar, nunca para aprovar.",
            "ROIC sobre capital empregado (ativo total menos passivo circulante). "
            "O ROIC original de Greenblatt exclui ágio e caixa excedente, contas "
            "que a DFP não separa.",
            "Recuperação judicial é lista manual: não existe esse campo na DFP "
            "nem no cadastro do B3.",
        ],
    }

    with _lock:
        _cache["payload"] = resultado
        _cache["carimbo"] = agora
    return {**resultado, "cache": False, "idade_segundos": 0}


@router.get("/scanner")
def get_scanner(segmento: str = Query("todos"),
                apenas_aprovados: bool = Query(False),
                minimo_score: float = Query(0.0),
                forcar: bool = Query(False)):
    """Motor multicritério. `segmento` = momentum | bazin | greenblatt | todos."""
    dados = montar(forcar=flag(forcar))
    if "erro" in dados:
        return dados

    papeis = dados["papeis"]
    alvo = (segmento or "todos").lower().strip()

    if alvo in SEGMENTOS:
        if alvo == "greenblatt":
            papeis = [p for p in papeis if p["greenblatt"].get("rank")]
            papeis.sort(key=lambda p: p["greenblatt"]["rank"])
        else:
            if flag(apenas_aprovados):
                papeis = [p for p in papeis if p[alvo]["aprovado"]]
            papeis.sort(key=lambda p: -(p["scores"][alvo] or 0))
    elif flag(apenas_aprovados):
        papeis = [p for p in papeis
                  if p["momentum"]["aprovado"] or p["bazin"]["aprovado"]]

    corte = float(minimo_score or 0.0)
    if corte > 0:
        papeis = [p for p in papeis if (p.get("score_quant") or 0) >= corte]

    return {**dados, "papeis": papeis, "exibidos": len(papeis), "segmento": alvo}


@router.get("/papel/{ticker}")
def get_papel(ticker: str):
    """Um papel nos três segmentos, com todos os componentes abertos."""
    codigo = (ticker or "").upper().strip()
    dados = montar()
    if "erro" in dados:
        return dados
    for p in dados["papeis"]:
        if p["ticker"] == codigo:
            return {**carimbo_de_coleta(), **p, "ressalvas": dados["ressalvas"]}
    return {"erro": f"{codigo} não está no universo do radar."}


def limpar_cache():
    with _lock:
        _cache["payload"] = None
        _cache["carimbo"] = 0.0
