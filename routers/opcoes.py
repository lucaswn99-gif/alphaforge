"""Opções: precificação, estruturas e recomendação.

Três endpoints e um princípio: toda resposta carrega as PREMISSAS que a
produziram. Retorno esperado de opção não é propriedade da opção — é a sua
expectativa de retorno e volatilidade do ativo levada ao vencimento. Sem o
carimbo, o número parece objetivo quando é opinião com casas decimais.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from modules import estruturas, opcoes, planos, taxas

router = APIRouter(prefix="/api/opcoes", tags=["opções"])


def _taxa_livre(informada=None):
    """Taxa livre de risco em decimal. Sem parâmetro, usa a meta Selic do BCB."""
    try:
        valor = float(informada)
        if 0 < valor < 100:
            return valor / 100.0, "parametro", None
    except (TypeError, ValueError):
        pass
    selic = taxas.obter_selic_meta()
    return float(selic["valor"]) / 100.0, selic["origem"], selic["data"]


@router.get("/catalogo")
def catalogo():
    """As estruturas disponíveis, com quando usar e — o que importa — quando não."""
    return {
        "estruturas": [{"chave": k, **v} for k, v in estruturas.CATALOGO.items()],
        "visoes": list(estruturas.VISOES),
        "objetivos": list(estruturas.OBJETIVOS),
    }


@router.get("/precificar")
def precificar(
    tipo: str = Query(..., description="call ou put"),
    spot: float = Query(..., gt=0),
    strike: float = Query(..., gt=0),
    dias: float = Query(..., ge=0, description="dias úteis até o vencimento"),
    vol: float = Query(None, description="volatilidade anual em %; vazio exige premio_mercado"),
    premio_mercado: float = Query(None, description="prêmio de tela, para extrair a implícita"),
    taxa_aa: float = Query(None, description="taxa livre de risco em %; vazio usa a Selic"),
    dividendo_aa: float = Query(0.0, description="dividend yield anual em %"),
    ctx: planos.Contexto = Depends(planos.acesso("opcoes_precificar")),
):
    """Prêmio, gregas e volatilidade implícita de uma opção.

    Informe `vol` para precificar, ou `premio_mercado` para extrair a
    implícita — informando os dois, você compara o teórico com a tela, que é
    a leitura que interessa.
    """
    taxa, origem_taxa, vigencia = _taxa_livre(taxa_aa)
    prazo = opcoes.prazo_em_anos(dias)
    dividendo = (float(dividendo_aa or 0.0)) / 100.0

    implicita = None
    if premio_mercado is not None:
        implicita = opcoes.volatilidade_implicita(
            tipo, premio_mercado, spot, strike, taxa, prazo, dividendo)

    vol_usada = (float(vol) / 100.0) if vol else implicita
    if vol_usada is None:
        return {"erro": "Informe a volatilidade, ou um prêmio de mercado do qual "
                        "extrair a implícita. Sem um dos dois não há o que calcular."}

    teorico = opcoes.preco(tipo, spot, strike, taxa, vol_usada, prazo, dividendo)
    g = opcoes.gregas(tipo, spot, strike, taxa, vol_usada, prazo, dividendo)

    return {
        "tipo": tipo, "spot": spot, "strike": strike, "dias_uteis": dias,
        "premio_teorico": round(teorico, 4) if teorico is not None else None,
        "premio_mercado": premio_mercado,
        "volatilidade_usada_pct": round(vol_usada * 100, 2),
        "volatilidade_implicita_pct": round(implicita * 100, 2) if implicita else None,
        "gregas": {k: (round(v, 6) if v is not None else None) for k, v in g.items()},
        "premissas": {
            "taxa_livre_risco_aa": round(taxa * 100, 2),
            "origem_taxa": origem_taxa, "vigencia_taxa": vigencia,
            "dividendo_aa": dividendo_aa,
            "base_prazo": "dias úteis (252), como a B3 cota volatilidade",
            "modelo": "Black-Scholes-Merton europeu",
        },
        "leitura_gregas": {
            "delta": "quanto o prêmio anda para cada R$ 1 do ativo",
            "gama": "quanto o delta muda para cada R$ 1 do ativo",
            "vega": "quanto o prêmio muda para cada 1 PONTO de volatilidade",
            "theta": "quanto o prêmio perde por DIA corrido, tudo mais constante",
            "rho": "quanto o prêmio muda para cada 1 ponto de juro",
        },
    }


class Perna(BaseModel):
    tipo: str                       # call, put ou acao
    posicao: str = "compra"         # compra ou venda
    strike: Optional[float] = None
    premio: float
    quantidade: float = 1
    rotulo: Optional[str] = None


class Estrutura(BaseModel):
    pernas: List[Perna]
    spot: float
    dias: float
    vol_esperada: float             # em %, a que você espera REALIZAR
    retorno_esperado_ativo: float   # em %, a sua expectativa anual para o ativo
    vol_implicita: Optional[float] = None    # em %, para as gregas
    taxa_aa: Optional[float] = None
    dividendo_aa: float = 0.0


@router.post("/avaliar")
def avaliar(dados: Estrutura,
            ctx: planos.Contexto = Depends(planos.acesso("opcoes_precificar"))):
    """Payoff, extremos, breakevens, gregas e resultado esperado de qualquer
    combinação de pernas — inclusive uma que você inventar."""
    taxa, origem_taxa, vigencia = _taxa_livre(dados.taxa_aa)
    resultado = opcoes.avaliar_estrutura(
        [p.model_dump() for p in dados.pernas],
        spot=dados.spot, taxa=taxa, prazo=opcoes.prazo_em_anos(dados.dias),
        vol_real=dados.vol_esperada / 100.0,
        retorno_esperado_ativo=dados.retorno_esperado_ativo / 100.0,
        dividendo=(dados.dividendo_aa or 0.0) / 100.0,
        vol_precificacao=(dados.vol_implicita / 100.0) if dados.vol_implicita else None,
    )
    if "erro" not in resultado:
        resultado["premissas"]["origem_taxa"] = origem_taxa
        resultado["premissas"]["vigencia_taxa"] = vigencia
        resultado["curva_payoff"] = _curva(
            [p.model_dump() for p in dados.pernas], dados.spot)
    return resultado


def _curva(pernas_brutas, spot, pontos=81):
    """Pontos do payoff para desenhar, de 60% a 140% do spot."""
    pernas = [p for p in (opcoes._perna(b) for b in pernas_brutas) if p]
    if not pernas:
        return []
    inicio, fim = spot * 0.6, spot * 1.4
    passo = (fim - inicio) / (pontos - 1)
    return [{"preco": round(inicio + i * passo, 2),
             "resultado": round(opcoes.payoff(pernas, inicio + i * passo), 4)}
            for i in range(pontos)]


class Contexto(BaseModel):
    visao: str = "lateral"          # alta_forte .. baixa_forte
    objetivo: str = "direcional"    # direcional, renda, protecao
    spot: float
    dias: float
    vol_esperada: float             # em %
    vol_implicita: Optional[float] = None   # em %
    retorno_esperado_ativo: Optional[float] = None   # em %
    taxa_aa: Optional[float] = None
    dividendo_aa: float = 0.0
    tem_acao: bool = False
    quantidade: float = 1


@router.post("/recomendar")
def recomendar(dados: Contexto,
               ctx: planos.Contexto = Depends(planos.acesso("opcoes_recomendar"))):
    """Estruturas ordenadas por aderência ao seu cenário, já avaliadas.

    Devolve também as REJEITADAS com o motivo: saber por que uma estrutura não
    foi sugerida ensina mais que a lista das sugeridas.
    """
    taxa, origem_taxa, vigencia = _taxa_livre(dados.taxa_aa)
    resposta = estruturas.recomendar(
        visao=dados.visao, objetivo=dados.objetivo, spot=dados.spot,
        taxa=taxa, vol_esperada=dados.vol_esperada / 100.0,
        prazo=opcoes.prazo_em_anos(dados.dias),
        vol_implicita=(dados.vol_implicita / 100.0) if dados.vol_implicita else None,
        dividendo=(dados.dividendo_aa or 0.0) / 100.0,
        tem_acao=bool(dados.tem_acao), quantidade=dados.quantidade,
        retorno_esperado_ativo=((dados.retorno_esperado_ativo / 100.0)
                               if dados.retorno_esperado_ativo is not None else None),
    )
    if "erro" not in resposta:
        for sugerida in resposta["sugeridas"]:
            sugerida["curva_payoff"] = _curva(sugerida["avaliacao"]["pernas"], dados.spot)
        resposta["taxa_livre_risco_aa"] = round(taxa * 100, 2)
        resposta["origem_taxa"] = origem_taxa
        resposta["vigencia_taxa"] = vigencia
    return resposta
