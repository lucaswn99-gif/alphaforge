"""Calculadora de crédito: entrada manual, score e o porquê de cada critério.

Existe para o emissor que NÃO publica DFP — CRI, CRA, debênture de companhia
fechada. Você tem o prospecto ou o balanço na mão e digita; o motor calcula.

O que ele devolve não é só um número. Cada critério volta com o valor apurado,
a referência usada, se passou, e uma frase dizendo o que aquilo significa. Um
score sem o porquê não serve para decidir nem para explicar a decisão depois —
e explicar depois é metade do trabalho de assessoria.

NADA É ESTIMADO. Campo que você não preencher volta como "não apurado", entra
como cobertura menor e aparece na tela. A versão antiga deste motor preenchia
ativo circulante com 40% do ativo total quando faltava; esse chute entrava no
Z-Score como se fosse medido.
"""

from modules.credit_engine import (PISO_COBERTURA_JUROS, PISO_Z_SCORE,
                                   TETO_ALAVANCAGEM,
                                   calcular_altman_z_score_emergente)

PISO_LIQUIDEZ_CORRENTE = 1.0
PISO_CAPITAL_PROPRIO = 20.0   # % do ativo financiado por capital próprio

# Faixas graduadas: crédito não é aprovado/reprovado binário até o veto. Entre
# um limite e outro há gradação, e é ela que separa emissor sólido de emissor
# que apenas passou raspando.
FAIXAS_ALAVANCAGEM = [(0.5, 30), (1.5, 26), (2.5, 20), (3.0, 12), (3.5, 6), (float("inf"), 0)]
FAIXAS_COBERTURA = [(1.5, 0), (2.5, 12), (4.0, 20), (6.0, 24), (float("inf"), 26)]
FAIXAS_Z = [(1.10, 0), (1.80, 8), (2.60, 16), (3.50, 22), (float("inf"), 24)]
FAIXAS_LIQUIDEZ = [(0.8, 0), (1.0, 4), (1.3, 9), (1.8, 12), (float("inf"), 10)]
FAIXAS_CAPITAL = [(10, 0), (20, 3), (35, 7), (50, 9), (float("inf"), 10)]

PESOS = {"alavancagem": 30, "cobertura": 26, "z_score": 24,
         "liquidez": 12, "capital_proprio": 10}

# Abaixo disso não há laudo: aprovar com dois critérios de cinco seria dizer
# que o que não foi medido está bem.
MINIMO_CRITERIOS = 3


def _num(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def _faixa(valor, faixas):
    for limite, pontos in faixas:
        if valor <= limite:
            return pontos
    return faixas[-1][1]


def _criterio(chave, rotulo, valor, referencia, aprovado, explicacao, pontos, peso):
    return {"chave": chave, "rotulo": rotulo, "valor": valor,
            "referencia": referencia, "situacao": ("nao_apurado" if valor is None
                                                   else "aprovado" if aprovado else "reprovado"),
            "explicacao": explicacao, "pontos": pontos, "peso": peso}


def avaliar(dados):
    """dados: dict com os campos do balanço. Devolve o laudo completo.

    Campos aceitos (todos opcionais; o que faltar vira "não apurado"):
        nome_emissor, ativo_total, ativo_circulante, passivo_circulante,
        passivo_total, patrimonio_liquido, lucros_retidos, ebitda,
        divida_bruta, caixa, despesa_financeira
    """
    g = lambda k: _num(dados.get(k))  # noqa: E731

    ativo_total = g("ativo_total")
    ativo_circulante = g("ativo_circulante")
    passivo_circulante = g("passivo_circulante")
    passivo_total = g("passivo_total")
    patrimonio = g("patrimonio_liquido")
    lucros_retidos = g("lucros_retidos")
    ebitda = g("ebitda")
    divida_bruta = g("divida_bruta")
    caixa = g("caixa")
    despesa = g("despesa_financeira")

    # Passivo total pode ser deduzido: ativo total menos patrimônio líquido.
    # É identidade contábil, não estimativa.
    if passivo_total is None and ativo_total is not None and patrimonio is not None:
        passivo_total = ativo_total - patrimonio

    # Dívida líquida exige as duas pontas. Dívida bruta chamada de líquida
    # superestima a alavancagem e reprova emissor que tem caixa.
    divida_liquida = None
    if divida_bruta is not None and caixa is not None:
        divida_liquida = divida_bruta - caixa

    criterios = []

    # --- 1. Alavancagem ----------------------------------------------------
    alavancagem = None
    if divida_liquida is not None and ebitda is not None and ebitda > 0:
        alavancagem = round(max(divida_liquida, 0.0) / ebitda, 2)
    if alavancagem is None:
        explicacao = ("Precisa de dívida bruta, caixa e EBITDA positivo. "
                      "Sem os três não há alavancagem — e sem ela não há laudo de crédito.")
        pontos = None
    elif alavancagem > TETO_ALAVANCAGEM:
        explicacao = (f"A dívida líquida equivale a {alavancagem} anos de geração "
                      f"operacional, acima do teto de {TETO_ALAVANCAGEM}x. Nesse patamar a "
                      "empresa depende de rolagem: qualquer fechamento de mercado vira "
                      "problema de caixa, não de resultado.")
        pontos = _faixa(alavancagem, FAIXAS_ALAVANCAGEM)
    else:
        explicacao = (f"A dívida líquida equivale a {alavancagem} anos de geração "
                      f"operacional, dentro do teto de {TETO_ALAVANCAGEM}x. "
                      "A empresa consegue amortizar com o próprio resultado.")
        pontos = _faixa(alavancagem, FAIXAS_ALAVANCAGEM)
    criterios.append(_criterio(
        "alavancagem", "Dívida líquida / EBITDA", alavancagem,
        f"até {TETO_ALAVANCAGEM}x",
        alavancagem is not None and alavancagem <= TETO_ALAVANCAGEM,
        explicacao, pontos, PESOS["alavancagem"]))

    # --- 2. Cobertura de juros --------------------------------------------
    cobertura = None
    if ebitda is not None and despesa is not None and abs(despesa) > 0:
        cobertura = round(ebitda / abs(despesa), 2)
    if cobertura is None:
        explicacao = ("Precisa de EBITDA e despesa financeira anual. É o critério que "
                      "diz se o resultado paga os juros — sem ele o resto é secundário.")
        pontos = None
    elif cobertura < PISO_COBERTURA_JUROS:
        explicacao = (f"A geração operacional cobre os juros apenas {cobertura} vez(es), "
                      f"abaixo do piso de {PISO_COBERTURA_JUROS}x. O emissor está pagando "
                      "juros com caixa, venda de ativo ou nova dívida — não com operação.")
        pontos = _faixa(cobertura, FAIXAS_COBERTURA)
    else:
        explicacao = (f"A geração operacional cobre os juros {cobertura} vezes. "
                      "Há folga entre o resultado e o serviço da dívida.")
        pontos = _faixa(cobertura, FAIXAS_COBERTURA)
    criterios.append(_criterio(
        "cobertura", "Cobertura de juros (EBITDA / despesa financeira)", cobertura,
        f"mínimo {PISO_COBERTURA_JUROS}x",
        cobertura is not None and cobertura >= PISO_COBERTURA_JUROS,
        explicacao, pontos, PESOS["cobertura"]))

    # --- 3. Altman Z'' -----------------------------------------------------
    z = calcular_altman_z_score_emergente(
        ativo_circulante=ativo_circulante, passivo_circulante=passivo_circulante,
        ativo_total=ativo_total, lucros_retidos=lucros_retidos, ebitda=ebitda,
        patrimonio_liquido=patrimonio, passivo_total=passivo_total)
    z_score = z["z_score"]
    if z_score is None:
        explicacao = ("Faltam: " + ", ".join(z.get("campos_faltantes") or ["dados"])
                      + ". O Z'' combina capital de giro, lucros retidos, rentabilidade "
                      "do ativo e estrutura de capital numa medida só de risco de insolvência.")
        pontos = None
    elif z_score < PISO_Z_SCORE:
        explicacao = (f"Z'' de {z_score}, em zona de estresse (abaixo de {PISO_Z_SCORE}). "
                      "O modelo de Altman para emergentes associa esse patamar a "
                      "probabilidade elevada de insolvência em até dois anos.")
        pontos = _faixa(z_score, FAIXAS_Z)
    else:
        explicacao = (f"Z'' de {z_score} — {z['classificacao'].lower()}. "
                      "Combina capital de giro, lucros retidos, rentabilidade do ativo "
                      "e estrutura de capital.")
        pontos = _faixa(z_score, FAIXAS_Z)
    criterios.append(_criterio(
        "z_score", "Altman Z'' (mercados emergentes)", z_score,
        f"mínimo {PISO_Z_SCORE}",
        z_score is not None and z_score >= PISO_Z_SCORE,
        explicacao, pontos, PESOS["z_score"]))

    # --- 4. Liquidez corrente ---------------------------------------------
    liquidez = None
    if ativo_circulante is not None and passivo_circulante and passivo_circulante > 0:
        liquidez = round(ativo_circulante / passivo_circulante, 2)
    if liquidez is None:
        explicacao = "Precisa de ativo e passivo circulante."
        pontos = None
    elif liquidez < PISO_LIQUIDEZ_CORRENTE:
        explicacao = (f"Liquidez corrente de {liquidez}: o que vence em doze meses "
                      "supera o que entra no mesmo prazo. Capital de giro negativo "
                      "não condena sozinho, mas exige acesso a crédito de curto prazo.")
        pontos = _faixa(liquidez, FAIXAS_LIQUIDEZ)
    else:
        explicacao = (f"Liquidez corrente de {liquidez}: o ativo de curto prazo cobre "
                      "as obrigações dos próximos doze meses.")
        pontos = _faixa(liquidez, FAIXAS_LIQUIDEZ)
    criterios.append(_criterio(
        "liquidez", "Liquidez corrente", liquidez,
        f"mínimo {PISO_LIQUIDEZ_CORRENTE}",
        liquidez is not None and liquidez >= PISO_LIQUIDEZ_CORRENTE,
        explicacao, pontos, PESOS["liquidez"]))

    # --- 5. Capital próprio ------------------------------------------------
    capital_proprio = None
    if patrimonio is not None and ativo_total and ativo_total > 0:
        capital_proprio = round(patrimonio / ativo_total * 100.0, 1)
    if capital_proprio is None:
        explicacao = "Precisa de patrimônio líquido e ativo total."
        pontos = None
    elif capital_proprio < PISO_CAPITAL_PROPRIO:
        explicacao = (f"Apenas {capital_proprio}% do ativo é financiado por capital "
                      f"próprio, abaixo de {PISO_CAPITAL_PROPRIO}%. Quanto menor essa "
                      "fatia, menor o colchão que absorve prejuízo antes de atingir o credor.")
        pontos = _faixa(capital_proprio, FAIXAS_CAPITAL)
    else:
        explicacao = (f"{capital_proprio}% do ativo é financiado por capital próprio — "
                      "é esse colchão que absorve prejuízo antes de chegar ao credor.")
        pontos = _faixa(capital_proprio, FAIXAS_CAPITAL)
    criterios.append(_criterio(
        "capital_proprio", "Capital próprio / Ativo total", capital_proprio,
        f"mínimo {PISO_CAPITAL_PROPRIO}%",
        capital_proprio is not None and capital_proprio >= PISO_CAPITAL_PROPRIO,
        explicacao, pontos, PESOS["capital_proprio"]))

    # --- score, vetos e veredito -------------------------------------------
    apurados = [c for c in criterios if c["pontos"] is not None]
    obtidos = sum(c["pontos"] for c in apurados)
    possiveis = sum(c["peso"] for c in apurados)
    score = round(obtidos / possiveis * 100.0) if possiveis else None

    vetos = []
    if ebitda is not None and ebitda <= 0:
        vetos.append("EBITDA não positivo: não há geração operacional para servir dívida.")
    if alavancagem is not None and alavancagem > TETO_ALAVANCAGEM:
        vetos.append(f"Alavancagem de {alavancagem}x acima do teto de {TETO_ALAVANCAGEM}x.")
    if cobertura is not None and cobertura < PISO_COBERTURA_JUROS:
        vetos.append(f"Cobertura de juros de {cobertura}x abaixo do piso de {PISO_COBERTURA_JUROS}x.")
    if z_score is not None and z_score < PISO_Z_SCORE:
        vetos.append(f"Altman Z'' de {z_score} em zona de estresse.")

    nao_apurados = [c["rotulo"] for c in criterios if c["pontos"] is None]

    if vetos:
        veredito, resumo = "REPROVADO", "Reprovado por veto: " + " ".join(vetos)
    elif len(apurados) < MINIMO_CRITERIOS:
        veredito = "INCONCLUSIVO"
        resumo = (f"Apenas {len(apurados)} de {len(criterios)} critérios foram apurados. "
                  "Ausência de veto não é aprovação: sem os dados que faltam, o laudo "
                  "não afirma nada sobre o emissor.")
        score = min(score, 55) if score is not None else None
    elif score is not None and score >= 75:
        veredito, resumo = "APROVADO", (
            f"Aprovado com {score} pontos, sem veto em nenhum critério. "
            "Estrutura de capital e geração operacional confortáveis para o serviço da dívida.")
    elif score is not None and score >= 55:
        veredito, resumo = "APROVADO COM RESSALVA", (
            f"Passou em todos os critérios, mas com {score} pontos — margem estreita. "
            "Vale exigir prêmio maior ou garantia adicional.")
    else:
        veredito, resumo = "ATENÇÃO", (
            f"Nenhum veto disparou, mas o conjunto soma apenas {score} pontos. "
            "O emissor passa raspando em vários critérios ao mesmo tempo.")

    if nao_apurados and veredito != "INCONCLUSIVO":
        resumo += (" Não apurado: " + ", ".join(nao_apurados)
                   + " — o score foi normalizado pelo que foi medido.")

    return {
        "emissor": (dados.get("nome_emissor") or "Emissor não identificado"),
        "score": score,
        "veredito": veredito,
        "resumo": resumo,
        "criterios": criterios,
        "vetos": vetos,
        "nao_apurados": nao_apurados,
        "criterios_apurados": len(apurados),
        "criterios_totais": len(criterios),
        "calculados": {
            "divida_liquida": divida_liquida,
            "passivo_total": passivo_total,
            "alavancagem": alavancagem,
            "cobertura_juros": cobertura,
            "altman_z": z_score,
            "classificacao_z": z.get("classificacao"),
            "liquidez_corrente": liquidez,
            "capital_proprio_pct": capital_proprio,
            "lucros_retidos_assumidos_zero": z.get("lucros_retidos_assumidos_zero", False),
        },
    }
