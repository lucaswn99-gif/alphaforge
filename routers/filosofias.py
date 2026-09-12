"""Rotas dos motores de filosofia e do painel de BDRs.

Duas varreduras caras moram aqui. A de Barsi consulta vinte e seis papéis; a
de Greenblatt passa de cem, e cada um pede preço, perfil e balanço. Sem cache,
dois cliques seguidos custariam duas varreduras completas e renderiam 429 do
Yahoo no meio da segunda — por isso o resultado vive quinze minutos em
memória, e `forcar=true` é o único jeito de furar a fila.

O Greenblatt sai por padrão num universo reduzido. O completo existe atrás de
`completo=true` porque ele leva minutos, e uma rota HTTP que leva minutos não
é rota: é tarefa agendada com disfarce.
"""

import os
import threading
import time

from fastapi import APIRouter, Depends, Query

from modules import bdr, filosofias, fontes, planos

router = APIRouter(prefix="/filosofias", tags=["filosofias"])

CACHE_TTL = 900
UNIVERSO_EUA_RAPIDO = 40

_motor = None
_painel = None
_trava_motor = threading.Lock()

_cache = {}
_trava_cache = threading.Lock()


def _fonte_sec():
    """Cliente da SEC, se houver identificação no ambiente.

    A SEC exige `User-Agent` com nome e e-mail — sem isso responde 403. Em vez
    de mandar uma requisição que já se sabe recusada, o motor cai para o Yahoo
    e diz de onde veio o número no campo `origem_contabil`.
    """
    identificacao = os.environ.get("SEC_USER_AGENT", "").strip()
    if not identificacao or "@" not in identificacao:
        return None
    try:
        return fontes.FonteSEC(identificacao)
    except Exception:  # noqa: BLE001
        return None


def motor():
    global _motor, _painel
    with _trava_motor:
        if _motor is None:
            partilhada = fontes.FonteYahoo()
            _motor = filosofias.PhilosophyEngine(fonte=partilhada,
                                                 fonte_sec=_fonte_sec())
            # Mesma fonte nos dois: o painel de BDR e o motor consultam os
            # mesmos tickers americanos, e um cache só evita a segunda viagem.
            _painel = bdr.GlobalEquitiesPanel(fonte=partilhada)
        return _motor


def painel():
    motor()
    return _painel


def _em_cache(chave, produzir, forcar=False):
    agora = time.time()
    with _trava_cache:
        guardado = _cache.get(chave)
    if guardado and not forcar and agora - guardado[0] < CACHE_TTL:
        return {**guardado[1], "cache": True,
                "idade_segundos": int(agora - guardado[0])}
    valor = produzir()
    with _trava_cache:
        _cache[chave] = (time.time(), valor)
    return {**valor, "cache": False, "idade_segundos": 0}


@router.get("/bogle")
def bogle(posicoes: str = Query(..., description="TICKER:QUANTIDADE,TICKER:QUANTIDADE"),
          alvo: str = Query(None, description="TICKER:PERCENTUAL,... somando 100"),
          banda: float = Query(filosofias.BANDA_REBALANCEAMENTO, ge=0.0, le=0.5,
                               description="Tolerância em fração (0.05 = 5 p.p.)"),
          ctx: planos.Contexto = Depends(planos.acesso())):
    """Distância da carteira até o alvo, e o ajuste que a fecha.

    O alvo é política de quem investe. Esta rota mede a distância; não escolhe
    a alocação nem diz qual deveria ser.
    """
    def separar(texto, rotulo):
        mapa = {}
        for pedaco in (texto or "").split(","):
            pedaco = pedaco.strip()
            if not pedaco:
                continue
            if ":" not in pedaco:
                return None, f"{rotulo}: '{pedaco}' não está no formato TICKER:VALOR."
            simbolo, bruto = pedaco.split(":", 1)
            valor = fontes.numero(bruto.replace("%", "").strip())
            if valor is None:
                return None, f"{rotulo}: valor inválido em '{pedaco}'."
            mapa[simbolo.strip().upper()] = valor
        return mapa, None

    carteira, erro = separar(posicoes, "posicoes")
    if erro:
        return {"erro": erro}
    if not carteira:
        return {"erro": "Informe ao menos uma posição, como VOO:10,WRLD11.SA:300."}

    pesos = None
    if alvo:
        pesos, erro = separar(alvo, "alvo")
        if erro:
            return {"erro": erro}

    # A cota é debitada só agora, com a entrada já validada: erro de digitação
    # devolve a mensagem sem consumir uma das consultas do dia.
    planos.cobrar(ctx, "bogle_rebalanceamento")
    return motor().nucleo_bogle(carteira, alvo=pesos, banda=banda)


@router.get("/barsi")
def barsi(forcar: bool = Query(False, description="Ignora o cache de 15 minutos"),
          ctx: planos.Contexto = Depends(planos.acesso())):
    """Ranking BESST por preço teto e margem de segurança."""
    resultado = _em_cache("barsi", lambda: motor().satelite_barsi(), forcar)
    return planos.cortar_barsi(resultado, ctx)


@router.get("/greenblatt")
def greenblatt(completo: bool = Query(False, description="Universo inteiro; leva minutos"),
               forcar: bool = Query(False, description="Ignora o cache de 15 minutos"),
               ctx: planos.Contexto = Depends(planos.acesso())):
    """Magic Formula com trava de Shareholder Yield.

    O universo completo só roda para assinante: são mais de cem ativos, cada
    um com consultas de preço e balanço. Deixar isso aberto seria entregar a
    conta do Yahoo e da SEC para quem não paga por ela.
    """
    universo_completo = bool(completo and ctx.premium)
    universo = (filosofias.UNIVERSO_EUA if universo_completo
                else filosofias.UNIVERSO_EUA[:UNIVERSO_EUA_RAPIDO])

    def produzir():
        saida = motor().satelite_greenblatt(universo=universo)
        saida["universo_completo"] = universo_completo
        return saida

    resultado = _em_cache(f"greenblatt:{universo_completo}", produzir, forcar)
    return planos.cortar_greenblatt(resultado, ctx)


@router.get("/bdr")
def painel_bdr(ativos: str = Query(None, description="AAPL,MSFT — vazio usa o mapa padrão"),
               forcar: bool = Query(False, description="Ignora o cache de 15 minutos"),
               ctx: planos.Contexto = Depends(planos.acesso())):
    """Ações americanas contra seus BDRs, com o spread de paridade.

    O spread só é marcado como confiável quando a razão veio da tabela
    conferida **e** o BDR tem liquidez. Distorção em papel parado é preço
    velho, não oportunidade — e é assim que ela aparece aqui.
    """
    lista = [t.strip().upper() for t in (ativos or "").split(",") if t.strip()] or None
    chave = "bdr:" + (",".join(sorted(lista)) if lista else "padrao")

    def produzir():
        motor()
        return {"linhas": painel().painel_json(lista), **painel().carimbo()}

    resultado = _em_cache(chave, produzir, forcar)
    return planos.cortar_bdr(resultado, ctx)


@router.get("/universos")
def universos():
    """O que cada motor varre, e de onde vêm os números.

    Universo que ninguém vê não é auditável: sem esta rota, "o ranking não
    trouxe o papel X" não tem resposta possível.
    """
    return {
        "besst": filosofias.UNIVERSO_BESST,
        "besst_total": sum(len(v) for v in filosofias.UNIVERSO_BESST.values()),
        "eua_total": len(filosofias.UNIVERSO_EUA),
        "eua_rapido": UNIVERSO_EUA_RAPIDO,
        "bdr": {chave: {"bdr": valor[0], "bdrs_por_acao": valor[1]}
                for chave, valor in bdr.BDRS_POR_ACAO.items()},
        "bdr_verificado_em": bdr.VERIFICADO_EM,
        "sec_configurada": _fonte_sec() is not None,
        "criterios": {
            "barsi": {"yield_alvo_pct": filosofias.YIELD_ALVO_BARSI * 100,
                      "payout_pct": [filosofias.PAYOUT_MINIMO, filosofias.PAYOUT_MAXIMO],
                      "divida_ebit_maxima": filosofias.DIVIDA_EBITDA_MAXIMA,
                      "margem_seguranca_minima_pct": filosofias.MARGEM_SEGURANCA_MINIMA * 100},
            "greenblatt": {"shareholder_yield_minimo_pct": filosofias.SHAREHOLDER_YIELD_MINIMO,
                           "top": filosofias.TOP_GREENBLATT},
            "momentum": {"janela_meses": filosofias.MESES_JANELA,
                         "meses_pulados": filosofias.MESES_PULADOS,
                         "queda_estrutural_pct": filosofias.QUEDA_ESTRUTURAL * 100,
                         "rsi_sobrevendido": filosofias.RSI_SOBREVENDIDO},
        },
    }
