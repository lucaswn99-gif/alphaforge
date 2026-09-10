"""Calculadora de crédito de emissor.

A aba de crédito é uma calculadora e nada mais: você digita o balanço do
emissor — prospecto de CRI, CRA, debênture, ou a demonstração de uma companhia
fechada — e recebe o score com o motivo de cada critério ter passado ou não.

O que saiu daqui, e por quê:

  - Extração de PDF por LLM. Ela nunca calculou índice nenhum; só lia números
    do documento. Com o campo digitado, não há o que extrair — e some junto a
    dependência de chave de API, que hoje não existe.
  - Consulta por ticker. Emissor listado já tem laudo no scanner, a partir da
    DFP. Duplicar o caminho aqui criava duas telas para a mesma pergunta,
    capazes de discordar entre si.

Índice de crédito é conta, não interpretação. Os limites usados ficam expostos
em /renda-fixa/parametros: score cujos cortes ninguém vê não é auditável.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from modules import credito_score, planos

router = APIRouter(prefix="/renda-fixa", tags=["Crédito"])


class DadosEmissor(BaseModel):
    """Campos do balanço, digitados a partir do prospecto ou das demonstrações.

    Todos opcionais: o que faltar vira "não apurado" e reduz a cobertura do
    score, em vez de ser estimado. Os valores vão na mesma unidade — a tela
    aplica o multiplicador (unidade, mil, milhão) antes de enviar.
    """
    nome_emissor: Optional[str] = None
    ativo_total: Optional[float] = None
    ativo_circulante: Optional[float] = None
    passivo_circulante: Optional[float] = None
    passivo_total: Optional[float] = None
    patrimonio_liquido: Optional[float] = None
    lucros_retidos: Optional[float] = None
    ebitda: Optional[float] = None
    divida_bruta: Optional[float] = None
    caixa: Optional[float] = None
    despesa_financeira: Optional[float] = None


@router.post("/calcular")
def calcular(dados: DadosEmissor,
             ctx: planos.Contexto = Depends(planos.acesso("credito_calcular"))):
    """Score de crédito a partir dos dados digitados, com o porquê de cada
    critério — aprovado, reprovado ou não apurado, e o que aquilo significa."""
    return credito_score.avaliar(dados.model_dump())


@router.get("/parametros")
def parametros():
    """Os limites que o motor usa. Ficam expostos porque um score cujos cortes
    ninguém vê não é auditável — e crédito precisa ser."""
    return {
        "teto_alavancagem": credito_score.TETO_ALAVANCAGEM,
        "piso_cobertura_juros": credito_score.PISO_COBERTURA_JUROS,
        "piso_z_score": credito_score.PISO_Z_SCORE,
        "piso_liquidez_corrente": credito_score.PISO_LIQUIDEZ_CORRENTE,
        "piso_capital_proprio_pct": credito_score.PISO_CAPITAL_PROPRIO,
        "minimo_criterios": credito_score.MINIMO_CRITERIOS,
        "pesos": credito_score.PESOS,
    }
