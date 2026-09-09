"""Precificação, gregas e retorno esperado de opções.

MODELO
------
Black-Scholes-Merton com dividendo contínuo. Europeu.

Opção de ação na B3 pode ser americana; para CALL sobre ação sem dividendo no
período, americana e europeia valem o mesmo (nunca compensa exercer antes). Com
dividendo relevante antes do vencimento, ou em PUT americana, existe prêmio de
exercício antecipado que este modelo NÃO captura — e o resultado sai
subestimado. O campo `modelo` viaja em toda resposta para a tela poder dizer
isso; estimar o prêmio americano exigiria árvore binomial, e entregar um número
sem dizer que ele é aproximado é pior que entregar a aproximação declarada.

RETORNO ESPERADO — A PARTE QUE QUASE TODA FERRAMENTA ERRA
---------------------------------------------------------
Sob a medida neutra a risco (a do Black-Scholes) o retorno esperado de QUALQUER
opção é exatamente a taxa livre de risco. Isso é construção do modelo, não
resultado: não há informação nenhuma ali. Uma tela que anuncia "retorno
esperado de 340%" calculado assim está anunciando ruído.

Retorno esperado de verdade exige uma distribuição do MUNDO REAL, que é
premissa de quem analisa: qual retorno você espera do ativo e com qual
volatilidade. Aqui essa premissa é entrada obrigatória e viaja carimbada em
toda resposta. O número deixa de parecer objetivo e passa a ser o que é —
a sua opinião, levada às últimas consequências com rigor.

A integração é determinística (quadratura sobre a normal padrão), não Monte
Carlo: mesmo dado, mesmo número, sempre. Um resultado que muda a cada clique
não serve para decidir nem para conferir depois.
"""

import math

# Faixa e resolução da quadratura. ±8 desvios cobrem a cauda até ~1e-15 de
# massa; 1601 pontos dão erro de integração abaixo de um centavo em prêmios
# de ordem de dezenas de reais.
Z_LIMITE = 8.0
Z_PONTOS = 1601

DIAS_UTEIS_ANO = 252.0
DIAS_CORRIDOS_ANO = 365.0

TIPOS = ("call", "put")


def _n(x):
    """Densidade da normal padrão."""
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _N(x):
    """Acumulada da normal padrão, via erf — sem depender de scipy."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _num(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def prazo_em_anos(dias, base="uteis"):
    """Dias até o vencimento em fração de ano.

    B3 cota volatilidade em dias úteis (252). Usar 365 com vol cotada em 252
    subestima o prêmio em ~20% — erro silencioso e grande.
    """
    d = _num(dias)
    if d is None or d < 0:
        return None
    return d / (DIAS_UTEIS_ANO if base == "uteis" else DIAS_CORRIDOS_ANO)


def _d1_d2(spot, strike, taxa, vol, prazo, dividendo=0.0):
    if not (spot and spot > 0 and strike and strike > 0 and vol and vol > 0
            and prazo and prazo > 0):
        return None, None
    raiz = vol * math.sqrt(prazo)
    d1 = (math.log(spot / strike) + (taxa - dividendo + 0.5 * vol * vol) * prazo) / raiz
    return d1, d1 - raiz


def preco(tipo, spot, strike, taxa, vol, prazo, dividendo=0.0):
    """Prêmio teórico. None quando falta parâmetro — nunca zero.

    No vencimento (prazo 0) devolve o valor intrínseco, que é o limite correto
    e evita divisão por zero.
    """
    tipo = (tipo or "").lower()
    spot, strike = _num(spot), _num(strike)
    taxa, vol, prazo = _num(taxa), _num(vol), _num(prazo)
    dividendo = _num(dividendo) or 0.0
    if tipo not in TIPOS or spot is None or strike is None:
        return None
    if prazo is not None and prazo <= 0:
        return max(spot - strike, 0.0) if tipo == "call" else max(strike - spot, 0.0)
    if taxa is None or vol is None or prazo is None or vol <= 0:
        return None

    d1, d2 = _d1_d2(spot, strike, taxa, vol, prazo, dividendo)
    if d1 is None:
        return None
    desconto_ativo = math.exp(-dividendo * prazo)
    desconto_caixa = math.exp(-taxa * prazo)
    if tipo == "call":
        return spot * desconto_ativo * _N(d1) - strike * desconto_caixa * _N(d2)
    return strike * desconto_caixa * _N(-d2) - spot * desconto_ativo * _N(-d1)


def gregas(tipo, spot, strike, taxa, vol, prazo, dividendo=0.0):
    """Delta, gama, vega, teta e rô, nas unidades que a mesa usa.

    Vega por 1 PONTO de volatilidade (não por 1,00 de vol) e teta por DIA
    (não por ano) — porque é assim que se lê na tela e se dimensiona posição.
    """
    tipo = (tipo or "").lower()
    spot, strike = _num(spot), _num(strike)
    taxa, vol, prazo = _num(taxa), _num(vol), _num(prazo)
    dividendo = _num(dividendo) or 0.0
    vazio = {"delta": None, "gama": None, "vega": None, "theta": None, "rho": None}
    if tipo not in TIPOS:
        return vazio

    d1, d2 = _d1_d2(spot, strike, taxa, vol, prazo, dividendo)
    if d1 is None:
        return vazio

    raiz_prazo = math.sqrt(prazo)
    desconto_ativo = math.exp(-dividendo * prazo)
    desconto_caixa = math.exp(-taxa * prazo)
    densidade = _n(d1)

    gama = desconto_ativo * densidade / (spot * vol * raiz_prazo)
    vega = spot * desconto_ativo * densidade * raiz_prazo / 100.0

    if tipo == "call":
        delta = desconto_ativo * _N(d1)
        theta_ano = (-spot * densidade * vol * desconto_ativo / (2 * raiz_prazo)
                     - taxa * strike * desconto_caixa * _N(d2)
                     + dividendo * spot * desconto_ativo * _N(d1))
        rho = strike * prazo * desconto_caixa * _N(d2) / 100.0
    else:
        delta = desconto_ativo * (_N(d1) - 1.0)
        theta_ano = (-spot * densidade * vol * desconto_ativo / (2 * raiz_prazo)
                     + taxa * strike * desconto_caixa * _N(-d2)
                     - dividendo * spot * desconto_ativo * _N(-d1))
        rho = -strike * prazo * desconto_caixa * _N(-d2) / 100.0

    return {"delta": delta, "gama": gama, "vega": vega,
            "theta": theta_ano / DIAS_CORRIDOS_ANO, "rho": rho}


def volatilidade_implicita(tipo, premio, spot, strike, taxa, prazo,
                           dividendo=0.0, tolerancia=1e-8, maximo=200):
    """Vol implícita por Newton com queda para bisseção.

    Newton sozinho diverge quando o vega é minúsculo — opção muito dentro ou
    muito fora do dinheiro, ou perto do vencimento. Sem a bisseção de reserva a
    função devolveria lixo justamente nos casos em que a leitura importa.

    Devolve None quando o prêmio está fora dos limites de não-arbitragem: preço
    abaixo do intrínseco não tem vol implícita, tem erro de digitação.
    """
    tipo = (tipo or "").lower()
    premio, spot, strike = _num(premio), _num(spot), _num(strike)
    taxa, prazo = _num(taxa), _num(prazo)
    dividendo = _num(dividendo) or 0.0
    if tipo not in TIPOS or None in (premio, spot, strike, taxa, prazo):
        return None
    if premio <= 0 or prazo <= 0 or spot <= 0 or strike <= 0:
        return None

    desconto_ativo = math.exp(-dividendo * prazo)
    desconto_caixa = math.exp(-taxa * prazo)
    if tipo == "call":
        piso, teto_preco = max(spot * desconto_ativo - strike * desconto_caixa, 0.0), spot * desconto_ativo
    else:
        piso, teto_preco = max(strike * desconto_caixa - spot * desconto_ativo, 0.0), strike * desconto_caixa
    if premio < piso - 1e-9 or premio > teto_preco + 1e-9:
        return None

    vol = 0.30
    for _ in range(60):
        teorico = preco(tipo, spot, strike, taxa, vol, prazo, dividendo)
        if teorico is None:
            break
        diferenca = teorico - premio
        if abs(diferenca) < tolerancia:
            return vol
        vega = gregas(tipo, spot, strike, taxa, vol, prazo, dividendo)["vega"] * 100.0
        if not vega or vega < 1e-10:
            break
        passo = diferenca / vega
        vol_nova = vol - passo
        if vol_nova <= 1e-6 or vol_nova > 10.0:
            break
        vol = vol_nova

    baixo, alto = 1e-6, 10.0
    for _ in range(maximo):
        meio = (baixo + alto) / 2.0
        teorico = preco(tipo, spot, strike, taxa, meio, prazo, dividendo)
        if teorico is None:
            return None
        if abs(teorico - premio) < tolerancia:
            return meio
        if teorico > premio:
            alto = meio
        else:
            baixo = meio
    return (baixo + alto) / 2.0


# --------------------------------------------------------------------------- #
# Estruturas: payoff, métricas e retorno esperado no mundo real
# --------------------------------------------------------------------------- #
INSTRUMENTOS = ("call", "put", "acao")
POSICOES = ("compra", "venda")

# Grade de preços do ativo para varrer payoff, extremos e breakevens.
GRADE_PONTOS = 1201
GRADE_LARGURA = 3.0     # de ~0 até 3x o spot cobre qualquer estrutura listada


def _perna(bruta):
    """Normaliza uma perna. Devolve None se a perna não fizer sentido."""
    if not isinstance(bruta, dict):
        return None
    tipo = str(bruta.get("tipo") or "").lower().strip()
    posicao = str(bruta.get("posicao") or "compra").lower().strip()
    if tipo not in INSTRUMENTOS or posicao not in POSICOES:
        return None
    premio = _num(bruta.get("premio"))
    quantidade = _num(bruta.get("quantidade"))
    quantidade = 1.0 if quantidade is None else abs(quantidade)
    if premio is None or quantidade <= 0:
        return None
    strike = _num(bruta.get("strike"))
    if tipo in ("call", "put") and (strike is None or strike <= 0):
        return None
    return {"tipo": tipo, "posicao": posicao, "strike": strike, "premio": premio,
            "quantidade": quantidade,
            "vol": _num(bruta.get("vol")), "rotulo": bruta.get("rotulo")}


def payoff_perna(perna, preco_final):
    """Resultado da perna no vencimento, já líquido do prêmio pago ou recebido."""
    sinal = 1.0 if perna["posicao"] == "compra" else -1.0
    if perna["tipo"] == "call":
        intrinseco = max(preco_final - perna["strike"], 0.0)
    elif perna["tipo"] == "put":
        intrinseco = max(perna["strike"] - preco_final, 0.0)
    else:
        intrinseco = preco_final
    return sinal * (intrinseco - perna["premio"]) * perna["quantidade"]


def payoff(pernas, preco_final):
    return sum(payoff_perna(p, preco_final) for p in pernas)


def custo_liquido(pernas):
    """Positivo = débito (você paga para montar). Negativo = crédito."""
    return sum((1.0 if p["posicao"] == "compra" else -1.0) * p["premio"] * p["quantidade"]
               for p in pernas)


def _grade(spot, pernas):
    strikes = [p["strike"] for p in pernas if p["strike"]]
    teto = max([spot * GRADE_LARGURA] + [k * 1.6 for k in strikes])
    passo = teto / (GRADE_PONTOS - 1)
    return [i * passo for i in range(GRADE_PONTOS)]


def pontos_de_equilibrio(pernas, spot):
    """Breakevens: onde o payoff cruza o zero.

    Varre a grade procurando troca de sinal e refina por bisseção. Achar por
    fórmula exigiria um caso para cada estrutura; a varredura funciona para
    qualquer combinação de pernas, inclusive as que o usuário inventar.
    """
    grade = _grade(spot, pernas)
    achados = []
    anterior_x, anterior_y = grade[0], payoff(pernas, grade[0])
    for x in grade[1:]:
        y = payoff(pernas, x)
        if anterior_y == 0.0:
            achados.append(anterior_x)
        elif (anterior_y < 0) != (y < 0):
            baixo, alto = anterior_x, x
            for _ in range(60):
                meio = (baixo + alto) / 2.0
                if (payoff(pernas, baixo) < 0) != (payoff(pernas, meio) < 0):
                    alto = meio
                else:
                    baixo = meio
            achados.append(round((baixo + alto) / 2.0, 4))
        anterior_x, anterior_y = x, y
    return sorted(set(achados))


def extremos(pernas, spot):
    """Lucro e perda máximos. Reconhece quando são ilimitados.

    Ilimitado não é um número grande: uma venda de call a seco tem perda sem
    teto, e devolver "R$ 8.400 de perda máxima" porque a grade parou ali seria
    a pior mentira que esta tela poderia contar.
    """
    grade = _grade(spot, pernas)
    valores = [payoff(pernas, x) for x in grade]
    maximo, minimo = max(valores), min(valores)

    # Inclinação nas pontas: se o payoff ainda sobe (ou desce) no fim da grade,
    # ele segue subindo (ou descendo) para sempre.
    def _inclinacao(a, b):
        return (payoff(pernas, b) - payoff(pernas, a)) / (b - a)

    inclinacao_alta = _inclinacao(grade[-2], grade[-1])
    inclinacao_baixa = _inclinacao(grade[0], grade[1])

    lucro_ilimitado = inclinacao_alta > 1e-9
    # Para baixo o ativo não passa de zero: a perda é limitada por construção,
    # exceto em venda de call, cuja perda cresce com a alta.
    perda_ilimitada = inclinacao_alta < -1e-9

    return {
        "lucro_maximo": None if lucro_ilimitado else round(maximo, 4),
        "perda_maxima": None if perda_ilimitada else round(minimo, 4),
        "lucro_ilimitado": lucro_ilimitado,
        "perda_ilimitada": perda_ilimitada,
        "inclinacao_baixa": inclinacao_baixa,
    }


def _quadratura():
    """Nós e pesos da normal padrão, por trapézio. Determinístico."""
    passo = 2.0 * Z_LIMITE / (Z_PONTOS - 1)
    nos = [-Z_LIMITE + i * passo for i in range(Z_PONTOS)]
    pesos = []
    for i, z in enumerate(nos):
        fator = 0.5 if i in (0, Z_PONTOS - 1) else 1.0
        pesos.append(fator * _n(z) * passo)
    total = sum(pesos)
    return nos, [w / total for w in pesos]     # renormaliza: a cauda cortada some


_NOS, _PESOS = _quadratura()


def distribuicao_final(spot, retorno_esperado, vol, prazo, dividendo=0.0):
    """Preços finais e probabilidades sob a distribuição do MUNDO REAL.

    S_T = S0 * exp((mu - q - vol²/2) T + vol √T Z), Z ~ N(0,1).

    `retorno_esperado` é a SUA expectativa de retorno anual do ativo, em
    decimal. Não é a taxa livre de risco: usar r aqui devolveria o resultado
    neutro a risco, em que toda opção rende exatamente r e a conta não informa
    nada.
    """
    spot, vol, prazo = _num(spot), _num(vol), _num(prazo)
    mu = _num(retorno_esperado)
    dividendo = _num(dividendo) or 0.0
    if None in (spot, vol, prazo, mu) or spot <= 0 or vol <= 0 or prazo <= 0:
        return None
    deriva = (mu - dividendo - 0.5 * vol * vol) * prazo
    difusao = vol * math.sqrt(prazo)
    return [(spot * math.exp(deriva + difusao * z), w) for z, w in zip(_NOS, _PESOS)]


def avaliar_estrutura(pernas_brutas, spot, taxa, prazo, vol_real,
                      retorno_esperado_ativo, dividendo=0.0, vol_precificacao=None):
    """Métricas completas de uma estrutura. Nunca levanta.

    `vol_real` é a volatilidade que VOCÊ espera realizar até o vencimento, e é
    o que define a distribuição do resultado. `vol_precificacao` (opcional)
    serve só para as gregas — normalmente a implícita de mercado. Separar as
    duas é o ponto: comprar opção quando a implícita está acima da realizada
    esperada é justamente a aposta que a estrutura faz.
    """
    pernas = [p for p in (_perna(b) for b in (pernas_brutas or [])) if p]
    if not pernas:
        return {"erro": "Nenhuma perna válida. Cada perna precisa de tipo, posição, "
                        "prêmio e — para call e put — strike."}

    spot, taxa, prazo = _num(spot), _num(taxa), _num(prazo)
    vol_real = _num(vol_real)
    mu = _num(retorno_esperado_ativo)
    dividendo = _num(dividendo) or 0.0
    if spot is None or spot <= 0:
        return {"erro": "Informe o preço do ativo."}

    custo = custo_liquido(pernas)
    limites = extremos(pernas, spot)
    equilibrios = pontos_de_equilibrio(pernas, spot)

    # --- gregas agregadas ---------------------------------------------------
    vol_gregas = _num(vol_precificacao) or vol_real
    agregadas = {"delta": 0.0, "gama": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0}
    gregas_disponiveis = False
    for p in pernas:
        sinal = (1.0 if p["posicao"] == "compra" else -1.0) * p["quantidade"]
        if p["tipo"] == "acao":
            agregadas["delta"] += sinal
            gregas_disponiveis = True
            continue
        vol_perna = p["vol"] or vol_gregas
        g = gregas(p["tipo"], spot, p["strike"], taxa, vol_perna, prazo, dividendo)
        if g["delta"] is None:
            continue
        gregas_disponiveis = True
        for chave in agregadas:
            agregadas[chave] += sinal * g[chave]
    if not gregas_disponiveis:
        agregadas = {k: None for k in agregadas}

    # --- resultado esperado no mundo real -----------------------------------
    esperado = probabilidade_lucro = None
    percentis = None
    distribuicao = distribuicao_final(spot, mu, vol_real, prazo, dividendo)
    if distribuicao:
        esperado = sum(payoff(pernas, s) * w for s, w in distribuicao)
        probabilidade_lucro = sum(w for s, w in distribuicao if payoff(pernas, s) > 0)
        ordenado = sorted(distribuicao, key=lambda item: payoff(pernas, item[0]))
        percentis, acumulado = {}, 0.0
        alvos = [(0.05, "p05"), (0.25, "p25"), (0.50, "mediana"), (0.75, "p75"), (0.95, "p95")]
        indice = 0
        for s, w in ordenado:
            acumulado += w
            while indice < len(alvos) and acumulado >= alvos[indice][0]:
                percentis[alvos[indice][1]] = round(payoff(pernas, s), 4)
                indice += 1

    # Capital em risco: a perda máxima quando ela existe; senão o débito pago.
    # Estrutura de crédito com perda ilimitada não tem denominador honesto, e
    # devolver retorno percentual ali seria inventar uma base.
    if limites["perda_maxima"] is not None and limites["perda_maxima"] < 0:
        capital = abs(limites["perda_maxima"])
    elif custo > 0:
        capital = custo
    else:
        capital = None

    retorno_pct = (esperado / capital * 100.0) if (esperado is not None and capital) else None

    return {
        "pernas": [{k: v for k, v in p.items() if k != "vol"} for p in pernas],
        "custo_liquido": round(custo, 4),
        "tipo_montagem": "débito" if custo > 0 else ("crédito" if custo < 0 else "neutro"),
        "capital_em_risco": round(capital, 4) if capital else None,
        "pontos_equilibrio": equilibrios,
        **limites,
        "gregas": {k: (round(v, 6) if v is not None else None) for k, v in agregadas.items()},
        "resultado_esperado": round(esperado, 4) if esperado is not None else None,
        "retorno_esperado_pct": round(retorno_pct, 2) if retorno_pct is not None else None,
        "probabilidade_lucro": round(probabilidade_lucro * 100, 2) if probabilidade_lucro is not None else None,
        "percentis_resultado": percentis,
        "premissas": {
            "spot": spot, "prazo_anos": prazo, "taxa_livre_risco": taxa,
            "volatilidade_esperada": vol_real,
            "retorno_esperado_ativo": mu,
            "dividendo": dividendo,
            "modelo": "Black-Scholes-Merton europeu; não captura prêmio de "
                      "exercício antecipado de opção americana com dividendo.",
            "aviso_distribuicao": (
                "Resultado esperado e probabilidade de lucro dependem INTEIRAMENTE "
                "da sua premissa de retorno e volatilidade do ativo. Não são "
                "propriedade da opção — são a sua opinião levada ao vencimento."),
        },
    }


def strike_por_delta(tipo, delta_alvo, spot, taxa, vol, prazo, dividendo=0.0):
    """Strike cujo delta é aproximadamente `delta_alvo` (em módulo).

    Montar estrutura por delta, e não por percentual do spot, é como a mesa
    monta — e por um motivo prático: o delta já embute a volatilidade e o
    prazo. "10% fora do dinheiro" é muito longe num papel de vol 20% e quase
    dentro do dinheiro num de vol 60%; "25 delta" significa a mesma coisa
    nos dois.
    """
    tipo = (tipo or "").lower()
    alvo = abs(_num(delta_alvo) or 0.0)
    spot, taxa, vol, prazo = _num(spot), _num(taxa), _num(vol), _num(prazo)
    if tipo not in TIPOS or not (0 < alvo < 1) or None in (spot, taxa, vol, prazo):
        return None
    if vol <= 0 or prazo <= 0:
        return None

    baixo, alto = spot * 0.05, spot * 5.0
    for _ in range(120):
        meio = (baixo + alto) / 2.0
        delta = gregas(tipo, spot, meio, taxa, vol, prazo, dividendo)["delta"]
        if delta is None:
            return None
        # Delta de call cai com o strike; o de put (negativo) cresce em módulo.
        atual = abs(delta)
        if (tipo == "call" and atual > alvo) or (tipo == "put" and atual < alvo):
            baixo = meio
        else:
            alto = meio
    return round((baixo + alto) / 2.0, 2)
