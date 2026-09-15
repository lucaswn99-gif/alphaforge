"""Projeção de capital e rentabilidade, com aportes e metas.

**O ponto de maior cuidado regulatório de toda esta leva** (linguagem do
próprio documento de escopo) — isto é, por definição, uma projeção de
resultado futuro, entregue com a marca do escritório de um assessor
certificado Ancord. Três regras de design não-negociáveis, para o número
nunca virar promessa:

1. **Toda premissa é explícita e editável.** Este módulo nunca embute uma
   taxa, aporte ou horizonte invisível — quem chama sempre informa os três, e
   a tela (não este módulo) é quem pré-preenche a taxa sugerida (o retorno do
   backtest de 12 meses) de forma visível e editável, nunca escondida atrás
   de um cálculo automático no servidor.
2. **Rótulo "hipotético/ilustrativo" com destaque, não em rodapé.** Este
   módulo não desenha tela, mas devolve `AVISO_HIPOTETICO` pronto para a tela
   nunca ter que reescrever (ou esquecer) o aviso.
3. **A meta sai como GAP, nunca como resposta binária.** "Vai dar" ou "não
   vai dar" é conselho; "faltam R$ X" ou "sobra R$ X" é informação. Por isso
   `calcular_gap` sempre devolve a diferença assinada, nunca um veredito.

**Renda "sustentável" é juros sobre o principal, nunca o principal em si.**
Ao comparar a meta de retirada/renda mensal contra o patrimônio projetado,
este módulo usa "taxa assumida × patrimônio" (perpetuidade — o principal
nunca é consumido), em vez de uma regra de retirada como "4% ao ano" que
ninguém digitou na tela. Usar uma segunda premissa não pedida seria o mesmo
erro que taxa embutida invisível: a única taxa em jogo é a que o usuário já
informou para o crescimento.
"""

from modules import perfil as modulo_perfil

TAXA_MINIMA_PCT = -50.0
TAXA_MAXIMA_PCT = 100.0
HORIZONTE_MAXIMO_ANOS = 80
APORTE_MAXIMO = 10_000_000.0

AVISO_HIPOTETICO = (
    "Projeção hipotética/ilustrativa, não uma promessa de resultado. Parte "
    "de premissas que você definiu (taxa, aporte, horizonte) — mude "
    "qualquer uma delas e o resultado muda junto. Rentabilidade passada não "
    "garante rentabilidade futura.")


class ErroProjecao(ValueError):
    """Entrada recusada, com motivo em português para a tela repetir."""


def validar(taxa_anual_pct, aporte_mensal, horizonte_anos):
    try:
        taxa_anual_pct = float(taxa_anual_pct)
    except (TypeError, ValueError):
        raise ErroProjecao("Taxa anual precisa ser um número.")
    if not (TAXA_MINIMA_PCT <= taxa_anual_pct <= TAXA_MAXIMA_PCT):
        raise ErroProjecao(
            f"Taxa fora da faixa aceita ({TAXA_MINIMA_PCT:.0f}% a "
            f"{TAXA_MAXIMA_PCT:.0f}% a.a.).")

    try:
        aporte_mensal = float(aporte_mensal)
    except (TypeError, ValueError):
        raise ErroProjecao("Aporte mensal precisa ser um número.")
    if not (0 <= aporte_mensal <= APORTE_MAXIMO):
        raise ErroProjecao("Aporte mensal fora da faixa aceita.")

    try:
        horizonte_anos = int(horizonte_anos)
    except (TypeError, ValueError):
        raise ErroProjecao("Horizonte precisa ser um número inteiro de anos.")
    if not (1 <= horizonte_anos <= HORIZONTE_MAXIMO_ANOS):
        raise ErroProjecao(
            f"Horizonte fora da faixa aceita (1 a {HORIZONTE_MAXIMO_ANOS} anos).")

    return taxa_anual_pct, aporte_mensal, horizonte_anos


def _taxa_mensal_equivalente(taxa_anual_pct):
    return (1 + taxa_anual_pct / 100.0) ** (1 / 12) - 1


def projetar(valor_inicial, taxa_anual_pct, aporte_mensal, horizonte_anos):
    """Série anual (ano 0 a `horizonte_anos`), aporte mensal constante ao fim
    de cada mês, juros compostos mês a mês na taxa equivalente à taxa anual
    informada.

    `renda_mensal_sustentavel` em cada ponto é taxa mensal × patrimônio
    daquele ponto — o que o patrimônio projetado sustentaria de retirada
    mensal SEM reduzir o principal, não uma recomendação de quanto retirar.
    """
    taxa_mensal = _taxa_mensal_equivalente(taxa_anual_pct)
    meses_totais = horizonte_anos * 12

    def ponto(ano, valor):
        return {"ano": ano, "valor": round(valor, 2),
                "renda_mensal_sustentavel": round(valor * taxa_mensal, 2)}

    serie = [ponto(0, valor_inicial)]
    valor = valor_inicial
    for mes in range(1, meses_totais + 1):
        valor = valor * (1 + taxa_mensal) + aporte_mensal
        if mes % 12 == 0:
            serie.append(ponto(mes // 12, valor))

    total_aportado = round(aporte_mensal * meses_totais, 2)
    valor_final = serie[-1]["valor"]

    return {
        "serie": serie,
        "valor_inicial": round(valor_inicial, 2),
        "valor_final": valor_final,
        "total_aportado": total_aportado,
        "rendimento_total": round(valor_final - valor_inicial - total_aportado, 2),
        "renda_mensal_sustentavel_final": serie[-1]["renda_mensal_sustentavel"],
        "taxa_mensal_equivalente_pct": round(taxa_mensal * 100.0, 4),
        "horizonte_anos": horizonte_anos,
    }


def calcular_gaps(objetivo, meta_retirada_mensal, meta_patrimonio,
                  meta_renda_mensal, valor_final, renda_mensal_sustentavel_final):
    """Só entra o gap de uma meta que o perfil REALMENTE tem cadastrada —
    nunca inventa meta que o assessor não definiu (ver `modules/perfil.py`).
    `gap` positivo é falta; negativo é sobra — a tela decide como rotular."""
    if objetivo not in modulo_perfil.OBJETIVOS:
        return []

    gaps = []
    if objetivo == modulo_perfil.RENDA_PASSIVA and meta_retirada_mensal:
        gaps.append({
            "tipo": "renda_mensal", "rotulo": "Renda passiva",
            "meta": meta_retirada_mensal,
            "projetado": renda_mensal_sustentavel_final,
            "gap": round(meta_retirada_mensal - renda_mensal_sustentavel_final, 2),
        })
    if objetivo == modulo_perfil.APOSENTADORIA:
        if meta_patrimonio:
            gaps.append({
                "tipo": "patrimonio", "rotulo": "Patrimônio na aposentadoria",
                "meta": meta_patrimonio,
                "projetado": valor_final,
                "gap": round(meta_patrimonio - valor_final, 2),
            })
        if meta_renda_mensal:
            gaps.append({
                "tipo": "renda_mensal", "rotulo": "Renda mensal na aposentadoria",
                "meta": meta_renda_mensal,
                "projetado": renda_mensal_sustentavel_final,
                "gap": round(meta_renda_mensal - renda_mensal_sustentavel_final, 2),
            })
    return gaps
