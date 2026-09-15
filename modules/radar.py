"""Radar de cinco eixos: o retrato de um papel e o da carteira.

Cada eixo é 0-100 e responde uma pergunta diferente sobre o mesmo ativo:

    valor       está barato contra o que a empresa vale?     (Graham)
    qualidade   a empresa gera retorno sobre o capital?      (ROE, margem, ROIC)
    proventos   paga bem, e o pagamento se sustenta?         (Bazin)
    momentum    o preço está em tendência, com liquidez?
    seguranca   a empresa resiste a aperto?                  (liquidez, Z, dívida)

**Qualidade e Segurança não se sobrepõem, e isso é escolha.** Qualidade mede a
capacidade de GERAR retorno; Segurança, a capacidade de RESISTIR. Constância de
lucro conta só em Segurança — contá-la nos dois eixos faria uma empresa madura
parecer forte em duas dimensões por um único mérito.

**A normalização é por COBERTURA, não pelo total teórico.** É a mesma regra do
`quant._normalizar`, e a razão é a mesma: um papel com três dos quatro
critérios apurados não pode ser punido por um dado que a fonte não trouxe, nem
ganhar ponto que não mediu. Eixo sem nenhum critério apurado devolve None — e
None NÃO é zero, nem na tela nem na média da carteira.

**Cuidado com a unidade da margem de segurança.** `quant.margem_de_seguranca`
devolve PORCENTAGEM; `filosofias._avaliar_graham` devolve FRAÇÃO. Alimentar uma
faixa de pontos com a unidade errada divide a nota por cem sem produzir erro
nenhum — por isso a conversão acontece aqui, uma vez, e está testada.

**Ponderação da carteira é por valor, e a cobertura viaja junto.** Se só dois
de dez papéis têm nota de momentum, a nota de momentum da carteira descreve
aquela fatia e não o conjunto — o número sozinho mentiria por omissão.
"""

from modules import credit_engine, quant

EIXOS = ("valor", "qualidade", "proventos", "momentum", "seguranca")

ROTULOS = {
    "valor": "Valor",
    "qualidade": "Qualidade",
    "proventos": "Proventos",
    "momentum": "Momentum",
    "seguranca": "Segurança",
}

PERGUNTAS = {
    "valor": "Está barato contra o que a empresa vale?",
    "qualidade": "A empresa gera retorno sobre o capital que emprega?",
    "proventos": "Paga bem, e o pagamento se sustenta?",
    "momentum": "O preço está em tendência, com liquidez para entrar?",
    "seguranca": "A empresa resiste a um aperto?",
}

# --- Valor ------------------------------------------------------------------
# Margem sobre o Número de Graham, em %. Acima do número (margem negativa) não
# pontua; a partir daí o ganho é graduado e satura, porque desconto de 80% em
# geral é sintoma de problema, não de oportunidade.
PONTOS_MARGEM_GRAHAM = [(0, 0), (10, 14), (25, 24), (40, 32), (60, 36),
                        (float("inf"), 30)]
# Produto P/L × P/VP contra o teto de 22,5 do investidor defensivo.
PONTOS_PRODUTO = [(10, 34), (16, 28), (22.5, 20), (40, 8), (float("inf"), 0)]
PONTOS_PVP = [(0.7, 30), (1.0, 26), (1.5, 18), (2.5, 8), (float("inf"), 2)]

# --- Qualidade --------------------------------------------------------------
PONTOS_ROE = [(0, 0), (8, 10), (15, 22), (25, 32), (40, 36), (float("inf"), 28)]
PONTOS_MARGEM_LIQUIDA = [(0, 0), (5, 10), (12, 20), (22, 28), (float("inf"), 32)]
PONTOS_ROIC = [(0, 0), (8, 12), (15, 22), (25, 30), (float("inf"), 32)]

# --- Segurança --------------------------------------------------------------
PONTOS_LIQUIDEZ_CORRENTE = [(1.0, 0), (1.3, 10), (2.0, 22), (3.5, 26),
                            (float("inf"), 20)]
PONTOS_DL_EBIT_SEGURANCA = [(0, 28), (1.0, 26), (2.0, 20), (3.5, 10),
                            (float("inf"), 0)]
PONTOS_Z_SCORE = [(1.1, 0), (2.6, 16), (4.0, 24), (float("inf"), 28)]
# Constância de lucro: fração dos exercícios apurados que fecharam no azul.
# Um prejuízo em três anos custa caro de propósito — é o critério que Graham e
# Barsi tratam como eliminatório, e aqui ele pesa sem eliminar.
PONTOS_CONSTANCIA = [(0.34, 0), (0.67, 6), (0.99, 12), (float("inf"), 18)]

# --- Momentum ---------------------------------------------------------------
# Alimentado pelo `MotorMomentum` das filosofias (12M-1M, Sharpe e
# volatilidade), e NÃO pelo `quant.score_momentum`, que espera EMA, IFR diário
# e volume — outro formato, para outra pergunta. O de lá mede ponto de entrada
# de trade; o daqui mede se o papel vem subindo com risco pago.
#
# ATENÇÃO À UNIDADE: `momentum_12m_1m` e `volatilidade_anual` voltam como
# FRAÇÃO (0,15 = 15%), como o Sharpe do mesmo motor pressupõe. As faixas abaixo
# são em porcentagem, e a conversão está em `eixo_momentum`.
PONTOS_MOMENTUM_12M = [(-20, 0), (0, 10), (15, 24), (40, 32), (float("inf"), 24)]
PONTOS_SHARPE = [(0, 0), (0.5, 12), (1.0, 22), (2.5, 30), (float("inf"), 24)]
# Volatilidade menor pontua mais: aqui ela mede risco, não oportunidade.
PONTOS_VOLATILIDADE = [(20, 28), (30, 22), (45, 12), (float("inf"), 4)]


def _num(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None


def _somar(pares):
    """[(valor, faixa, teto)] -> score 0-100 normalizado pela cobertura."""
    obtidos = possiveis = 0.0
    medidos = 0
    for valor, faixa, teto in pares:
        ganho = quant._faixa(valor, faixa)
        if ganho is not None:
            obtidos += ganho
            possiveis += teto
            medidos += 1
    if not possiveis:
        return None, 0
    return quant._normalizar(obtidos, possiveis), medidos


def eixo_valor(graham):
    """Margem sobre o Número de Graham, produto P/L × P/VP e P/VP."""
    if not graham:
        return None, 0
    # CONVERSÃO DE UNIDADE: `_avaliar_graham` devolve a margem como fração
    # (0,25 = 25%), e as faixas aqui são em porcentagem. Sem esta linha toda
    # margem cairia na primeira faixa e o eixo ficaria zerado para todo mundo,
    # sem erro nenhum aparecer.
    margem = _num(graham.get("margem_seguranca"))
    margem_pct = None if margem is None else margem * 100.0
    return _somar([
        (margem_pct, PONTOS_MARGEM_GRAHAM, 36),
        (graham.get("produto_pl_pvp"), PONTOS_PRODUTO, 34),
        (graham.get("pvp"), PONTOS_PVP, 30),
    ])


def eixo_qualidade(balanco):
    """ROE, margem líquida e ROIC — capacidade de gerar retorno."""
    balanco = balanco or {}
    patrimonio = _num(balanco.get("patrimonio_liquido"))
    lucro = _num(balanco.get("lucro_liquido"))
    receita = _num(balanco.get("receita_liquida"))
    ebit = _num(balanco.get("ebit"))

    roe = (lucro / patrimonio * 100.0) if (lucro is not None and patrimonio
                                           and patrimonio > 0) else None
    margem = (lucro / receita * 100.0) if (lucro is not None and receita
                                           and receita > 0) else None
    capital = quant.capital_empregado(balanco.get("ativo_total"),
                                      balanco.get("passivo_circulante"))
    retorno = quant.roic(ebit, capital)

    return _somar([
        (roe, PONTOS_ROE, 36),
        (margem, PONTOS_MARGEM_LIQUIDA, 32),
        (retorno, PONTOS_ROIC, 32),
    ])


def eixo_seguranca(balanco, exercicios_com_lucro=None, exercicios_apurados=None):
    """Liquidez corrente, dívida sobre EBIT, Altman Z e constância de lucro."""
    balanco = balanco or {}
    circulante = _num(balanco.get("ativo_circulante"))
    passivo_circ = _num(balanco.get("passivo_circulante"))
    liquidez = (circulante / passivo_circ) if (circulante is not None
                                               and passivo_circ) else None

    divida = quant.divida_liquida(balanco.get("divida_curto_prazo"),
                                  balanco.get("divida_longo_prazo"),
                                  balanco.get("caixa"))
    alavancagem = quant.dl_sobre_ebit(divida, balanco.get("ebit"))

    passivo_total = None
    nao_circ = _num(balanco.get("passivo_nao_circulante"))
    if passivo_circ is not None or nao_circ is not None:
        passivo_total = (passivo_circ or 0.0) + (nao_circ or 0.0)

    # EBIT no lugar de EBITDA: a DFP coletada não traz depreciação separada.
    # A troca SUBESTIMA o X3 do Altman, então o Z sai conservador — erra para
    # o lado de parecer mais arriscado, nunca menos.
    laudo = credit_engine.calcular_altman_z_score_emergente(
        circulante, passivo_circ, balanco.get("ativo_total"),
        balanco.get("lucros_acumulados"), balanco.get("ebit"),
        balanco.get("patrimonio_liquido"), passivo_total)

    # Constância vira uma fração de 0 a 1 e entra pela MESMA porta dos outros
    # três. Somar por um caminho próprio exigiria refazer a normalização à mão,
    # e foi tentando isso que a primeira versão desta função nasceu com dois
    # caminhos e um deles errado.
    apurados = _num(exercicios_apurados)
    com_lucro = _num(exercicios_com_lucro)
    constancia = None
    if apurados and apurados > 0 and com_lucro is not None:
        constancia = max(0.0, min(1.0, com_lucro / apurados))

    return _somar([
        (liquidez, PONTOS_LIQUIDEZ_CORRENTE, 26),
        (alavancagem, PONTOS_DL_EBIT_SEGURANCA, 28),
        (laudo.get("z_score"), PONTOS_Z_SCORE, 28),
        (constancia, PONTOS_CONSTANCIA, 18),
    ])


def eixo_momentum(avaliacao):
    """Retorno 12M-1M, Sharpe e volatilidade, do `MotorMomentum`.

    Converte fração para porcentagem antes de pontuar: o motor devolve 0,15
    para 15%, e as faixas são em porcentagem. Sem a conversão, todo papel
    cairia na primeira faixa e o eixo ficaria zerado — sem erro nenhum.
    """
    if not avaliacao:
        return None, 0
    retorno = _num(avaliacao.get("momentum_12m_1m"))
    volatilidade = _num(avaliacao.get("volatilidade_anual"))
    return _somar([
        (None if retorno is None else retorno * 100.0, PONTOS_MOMENTUM_12M, 32),
        (avaliacao.get("sharpe"), PONTOS_SHARPE, 30),
        (None if volatilidade is None else volatilidade * 100.0,
         PONTOS_VOLATILIDADE, 28),
    ])


def radar_do_papel(ticker, graham=None, balanco=None, bazin=None,
                   momentum=None, exercicios_com_lucro=None,
                   exercicios_apurados=None):
    """Os cinco eixos de um papel. Eixo sem critério apurado volta None."""
    valor, m_valor = eixo_valor(graham)
    qualidade, m_qual = eixo_qualidade(balanco)
    seguranca, m_seg = eixo_seguranca(balanco, exercicios_com_lucro,
                                      exercicios_apurados)
    proventos = quant.score_bazin(bazin) if bazin else None
    tendencia, m_mom = eixo_momentum(momentum)

    eixos = {"valor": valor, "qualidade": qualidade, "proventos": proventos,
             "momentum": tendencia, "seguranca": seguranca}
    apurados = [e for e, v in eixos.items() if v is not None]

    return {
        "ticker": ticker,
        "eixos": eixos,
        "eixos_apurados": sorted(apurados),
        "eixos_nao_apurados": sorted(e for e in EIXOS if e not in apurados),
        "criterios_medidos": {"valor": m_valor, "qualidade": m_qual,
                              "seguranca": m_seg, "momentum": m_mom},
    }


def radar_da_carteira(radares, pesos):
    """Média por eixo, ponderada por valor, com a cobertura de cada eixo.

    Papel sem nota no eixo fica FORA da média daquele eixo, e o peso que ficou
    de fora vira `cobertura`. Tratar ausência como zero puxaria a média para
    baixo por falta de dado, que é o erro que este projeto não comete; tratar
    como se não existisse, sem declarar, esconderia que a nota descreve só uma
    parte da carteira.
    """
    radares = list(radares or [])
    pesos = pesos or {}
    peso_total = sum(_num(pesos.get(r["ticker"])) or 0.0 for r in radares)

    eixos = {}
    cobertura = {}
    for eixo in EIXOS:
        soma = peso = 0.0
        for radar in radares:
            nota = radar["eixos"].get(eixo)
            w = _num(pesos.get(radar["ticker"])) or 0.0
            if nota is None or w <= 0:
                continue
            soma += nota * w
            peso += w
        eixos[eixo] = round(soma / peso, 1) if peso else None
        cobertura[eixo] = round(peso / peso_total * 100.0, 1) if peso_total else 0.0

    medidos = [v for v in eixos.values() if v is not None]
    return {
        "eixos": eixos,
        "cobertura_pct": cobertura,
        "eixo_mais_forte": max((e for e in EIXOS if eixos[e] is not None),
                               key=lambda e: eixos[e], default=None),
        "eixo_mais_fraco": min((e for e in EIXOS if eixos[e] is not None),
                               key=lambda e: eixos[e], default=None),
        "media_geral": round(sum(medidos) / len(medidos), 1) if medidos else None,
        "papeis": len(radares),
        "rotulos": ROTULOS,
        "perguntas": PERGUNTAS,
    }
