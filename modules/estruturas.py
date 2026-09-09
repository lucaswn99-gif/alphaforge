"""Catálogo de estruturas com opções e motor de recomendação.

Cada estrutura sabe três coisas: como se monta a partir do spot e da
volatilidade, para que serve, e em que cenário ela é a escolha errada — esta
última sendo a que quase nenhuma ferramenta escreve.

COMO OS STRIKES SÃO ESCOLHIDOS
------------------------------
Por DELTA, não por percentual do spot. "10% fora do dinheiro" significa coisas
muito diferentes num papel de vol 20% e num de vol 60%; "25 delta" significa a
mesma coisa nos dois, porque o delta já embute volatilidade e prazo. É como
mesa monta, e faz o catálogo funcionar igual em PETR4 e em WEGE3.

O QUE DECIDE A RECOMENDAÇÃO
---------------------------
Três eixos, e o segundo é o que a maioria ignora:

  1. DIREÇÃO esperada do ativo — alta forte, alta moderada, lateral, baixa.
  2. PREÇO DA VOLATILIDADE: a implícita de mercado está acima ou abaixo da
     volatilidade que você espera realizar? Comprar opção com implícita cara é
     estar certo na direção e perder dinheiro assim mesmo. Este eixo separa
     quem compra prêmio de quem vende.
  3. OBJETIVO — direcional, renda, proteção.

Estrutura sem visão de volatilidade é meia análise. Um terminal que recomenda
"trava de alta" só porque você está otimista está ignorando metade do problema.
"""

from modules import opcoes

VISOES = ("alta_forte", "alta_moderada", "lateral", "baixa_moderada", "baixa_forte")
OBJETIVOS = ("direcional", "renda", "protecao")
PRECO_VOL = ("cara", "justa", "barata")

# Diferença relativa entre implícita e esperada a partir da qual o prêmio deixa
# de ser "justo". 15% é folga suficiente para não reagir a ruído de cotação.
LIMIAR_VOL = 0.15


def classificar_volatilidade(vol_implicita, vol_esperada):
    """A implícita está cara, justa ou barata contra a sua expectativa?

    É o eixo que decide comprar ou vender prêmio, e sem ele a recomendação
    fica pela metade: estar certo na direção e comprado em opção cara é uma
    das formas mais comuns de perder dinheiro com opção.
    """
    iv = opcoes._num(vol_implicita)
    ve = opcoes._num(vol_esperada)
    if iv is None or ve is None or ve <= 0:
        return None, None
    razao = iv / ve - 1.0
    if razao > LIMIAR_VOL:
        return "cara", razao
    if razao < -LIMIAR_VOL:
        return "barata", razao
    return "justa", razao


def _premio(tipo, spot, strike, taxa, vol, prazo, dividendo):
    p = opcoes.preco(tipo, spot, strike, taxa, vol, prazo, dividendo)
    return round(p, 2) if p is not None else None


def _perna(tipo, posicao, strike, premio, quantidade=1, rotulo=None):
    return {"tipo": tipo, "posicao": posicao, "strike": strike,
            "premio": premio, "quantidade": quantidade, "rotulo": rotulo}


# --------------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------------- #
# Cada entrada: como montar, para quem serve, e quando NÃO usar.
CATALOGO = {
    "trava_alta": {
        "nome": "Trava de alta (call spread)",
        "resumo": "Compra call no dinheiro e vende call acima. Risco e ganho definidos.",
        "quando": "Alta moderada, com alvo claro. O ganho para no strike vendido.",
        "quando_nao": ("Se você espera alta explosiva, o strike vendido corta justamente "
                       "a parte que valeria a pena. E se a implícita está cara, você "
                       "paga caro na ponta comprada."),
        "visoes": ("alta_moderada", "alta_forte"),
        "objetivos": ("direcional",),
        "vol_preferida": ("justa", "cara"),
        "exige_acao": False,
    },
    "trava_baixa": {
        "nome": "Trava de baixa (put spread)",
        "resumo": "Compra put no dinheiro e vende put abaixo. Risco e ganho definidos.",
        "quando": "Baixa moderada, ou proteção parcial de carteira com custo controlado.",
        "quando_nao": ("Queda violenta passa do strike vendido e o ganho trava. "
                       "Para proteger contra colapso, put seca protege mais."),
        "visoes": ("baixa_moderada", "baixa_forte"),
        "objetivos": ("direcional", "protecao"),
        "vol_preferida": ("justa", "cara"),
        "exige_acao": False,
    },
    "financiamento": {
        "nome": "Financiamento (venda coberta)",
        "resumo": "Com a ação em carteira, vende call acima do preço atual e embolsa o prêmio.",
        "quando": ("Lateralidade ou alta leve, e você aceita vender o papel no strike. "
                   "É a estrutura de renda mais usada na B3."),
        "quando_nao": ("Se o papel disparar você é exercido e fica de fora da alta. "
                       "E o prêmio NÃO protege contra queda relevante — ele amortece "
                       "alguns por cento, não mais."),
        "visoes": ("lateral", "alta_moderada"),
        "objetivos": ("renda",),
        "vol_preferida": ("cara", "justa"),
        "exige_acao": True,
    },
    "put_protetora": {
        "nome": "Put protetora",
        "resumo": "Com a ação em carteira, compra put abaixo do preço atual como seguro.",
        "quando": "Você quer manter o papel mas limitar a perda num evento específico.",
        "quando_nao": ("Seguro custa. Comprar put todo mês corrói o retorno — use com "
                       "prazo e motivo definidos, não como rotina."),
        "visoes": ("lateral", "baixa_moderada", "baixa_forte"),
        "objetivos": ("protecao",),
        "vol_preferida": ("barata", "justa"),
        "exige_acao": True,
    },
    "collar": {
        "nome": "Collar (fence)",
        "resumo": "Com a ação, compra put de proteção e vende call para pagá-la.",
        "quando": ("Proteção com custo próximo de zero, aceitando teto no ganho. "
                   "Clássico para posição concentrada que não se quer vender."),
        "quando_nao": ("Você troca o lado direito da distribuição pelo esquerdo. Se o "
                       "papel subir forte, o resultado é o mesmo de ter vendido."),
        "visoes": ("lateral", "baixa_moderada"),
        "objetivos": ("protecao", "renda"),
        "vol_preferida": ("justa", "cara"),
        "exige_acao": True,
    },
    "venda_put": {
        "nome": "Venda de put coberta em caixa",
        "resumo": "Vende put abaixo do preço atual, com caixa reservado para comprar se exercido.",
        "quando": ("Você quer o papel, mas mais barato. Recebe prêmio para esperar; "
                   "se cair até o strike, compra com desconto."),
        "quando_nao": ("Só monte se REALMENTE quiser o papel no strike. Vender put "
                       "sem querer a ação é vender seguro de algo que você não aceita "
                       "receber — e em queda forte a perda é grande."),
        "visoes": ("lateral", "alta_moderada"),
        "objetivos": ("renda",),
        "exige_acao": False,
    },
    "straddle": {
        "nome": "Straddle comprado",
        "resumo": "Compra call e put no mesmo strike, no dinheiro.",
        "quando": ("Você espera movimento forte e não sabe a direção — resultado, "
                   "decisão judicial, evento binário."),
        "quando_nao": ("É a estrutura que mais sofre com implícita cara: antes de um "
                       "evento a vol já subiu, e você paga o movimento antes dele "
                       "acontecer. Se o evento sair sem susto, perde nos dois lados."),
        "visoes": ("lateral",),
        "objetivos": ("direcional",),
        "vol_preferida": ("barata",),
        "exige_acao": False,
    },
    "strangle": {
        "nome": "Strangle comprado",
        "resumo": "Compra call acima e put abaixo do preço atual.",
        "quando": "Mesma aposta do straddle, mais barata — e exigindo movimento maior.",
        "quando_nao": "Precisa de movimento grande. Em mercado morno perde tudo.",
        "visoes": ("lateral",),
        "objetivos": ("direcional",),
        "vol_preferida": ("barata",),
        "exige_acao": False,
    },
    "borboleta": {
        "nome": "Borboleta (butterfly)",
        "resumo": "Compra uma call abaixo, vende duas no meio, compra uma acima.",
        "quando": "Você espera o papel PARADO perto de um preço específico no vencimento.",
        "quando_nao": ("Exige pontaria: fora da faixa estreita o resultado é o débito "
                       "pago. Não é estrutura de renda recorrente."),
        "visoes": ("lateral",),
        "objetivos": ("direcional", "renda"),
        "vol_preferida": ("cara",),
        "exige_acao": False,
    },
    "condor_ferro": {
        "nome": "Condor de ferro (iron condor)",
        "resumo": "Vende put spread abaixo e call spread acima. Recebe crédito.",
        "quando": ("Lateralidade com risco definido dos dois lados, e implícita cara. "
                   "É venda de volatilidade com perda limitada."),
        "quando_nao": ("Ganho pequeno e frequente contra perda grande e rara: a conta "
                       "só fecha com disciplina de tamanho. Não monte grande."),
        "visoes": ("lateral",),
        "objetivos": ("renda",),
        "vol_preferida": ("cara",),
        "exige_acao": False,
    },
}


def montar(chave, spot, taxa, vol, prazo, dividendo=0.0, quantidade=1):
    """Pernas da estrutura, com strikes por delta e prêmios teóricos.

    Os prêmios são TEÓRICOS, do modelo. Ao operar você substitui pelos preços
    de tela — e é justamente a diferença entre os dois que diz se o mercado
    está pagando bem pela estrutura.
    """
    if chave not in CATALOGO:
        return None
    K = lambda tipo, d: opcoes.strike_por_delta(tipo, d, spot, taxa, vol, prazo, dividendo)  # noqa: E731
    P = lambda tipo, k: _premio(tipo, spot, k, taxa, vol, prazo, dividendo)  # noqa: E731

    def call(posicao, delta, mult=1, rotulo=None):
        k = K("call", delta)
        return None if k is None else _perna("call", posicao, k, P("call", k),
                                             quantidade * mult, rotulo)

    def put(posicao, delta, mult=1, rotulo=None):
        k = K("put", delta)
        return None if k is None else _perna("put", posicao, k, P("put", k),
                                             quantidade * mult, rotulo)

    acao = _perna("acao", "compra", None, round(spot, 2), quantidade, "ação em carteira")

    receitas = {
        "trava_alta": lambda: [call("compra", 0.50, rotulo="call ATM"),
                               call("venda", 0.25, rotulo="call 25 delta")],
        "trava_baixa": lambda: [put("compra", 0.50, rotulo="put ATM"),
                                put("venda", 0.25, rotulo="put 25 delta")],
        "financiamento": lambda: [acao, call("venda", 0.30, rotulo="call 30 delta")],
        "put_protetora": lambda: [acao, put("compra", 0.25, rotulo="put 25 delta")],
        "collar": lambda: [acao, put("compra", 0.25, rotulo="put 25 delta"),
                           call("venda", 0.25, rotulo="call 25 delta")],
        "venda_put": lambda: [put("venda", 0.30, rotulo="put 30 delta")],
        "straddle": lambda: [call("compra", 0.50, rotulo="call ATM"),
                             put("compra", 0.50, rotulo="put ATM")],
        "strangle": lambda: [call("compra", 0.25, rotulo="call 25 delta"),
                             put("compra", 0.25, rotulo="put 25 delta")],
        "borboleta": lambda: [call("compra", 0.65, rotulo="call 65 delta"),
                              call("venda", 0.50, mult=2, rotulo="2x call ATM"),
                              call("compra", 0.35, rotulo="call 35 delta")],
        "condor_ferro": lambda: [put("compra", 0.10, rotulo="put 10 delta"),
                                 put("venda", 0.25, rotulo="put 25 delta"),
                                 call("venda", 0.25, rotulo="call 25 delta"),
                                 call("compra", 0.10, rotulo="call 10 delta")],
    }
    pernas = receitas[chave]()
    return None if any(p is None for p in pernas) else pernas


# --------------------------------------------------------------------------- #
# Motor de recomendação
# --------------------------------------------------------------------------- #
PESO_VISAO = 45
PESO_OBJETIVO = 30
PESO_VOL = 25


def _aderencia(ficha, visao, objetivo, preco_vol, tem_acao):
    """Quanto a estrutura serve ao contexto, de 0 a 100, com o porquê.

    Estrutura que EXIGE a ação em carteira e você não tem é descartada, não
    penalizada: recomendar financiamento a quem não tem o papel não é uma
    recomendação fraca, é uma recomendação impossível.
    """
    if ficha.get("exige_acao") and not tem_acao:
        return None, ["exige a ação em carteira, e você indicou não ter"]

    pontos, razoes = 0, []

    if visao in ficha.get("visoes", ()):
        pontos += PESO_VISAO
        razoes.append(f"desenhada para visão de {visao.replace('_', ' ')}")
    else:
        razoes.append(f"não é a estrutura natural para {visao.replace('_', ' ')}")

    if objetivo in ficha.get("objetivos", ()):
        pontos += PESO_OBJETIVO
        razoes.append(f"atende o objetivo de {objetivo}")

    preferidas = ficha.get("vol_preferida")
    if not preferidas:
        pontos += PESO_VOL // 2
    elif preco_vol in preferidas:
        pontos += PESO_VOL
        if preco_vol == "cara":
            razoes.append("a implícita está cara, e esta estrutura VENDE prêmio")
        elif preco_vol == "barata":
            razoes.append("a implícita está barata, e esta estrutura COMPRA prêmio")
        else:
            razoes.append("funciona bem com a implícita em preço justo")
    elif preco_vol == "cara" and "barata" in preferidas:
        razoes.append("⚠ esta estrutura compra prêmio, e a implícita está CARA — "
                      "você pode acertar a direção e ainda assim perder")
    elif preco_vol == "barata" and "cara" in preferidas:
        razoes.append("⚠ esta estrutura vende prêmio, e a implícita está BARATA — "
                      "você recebe pouco pelo risco assumido")

    return pontos, razoes


def recomendar(visao, objetivo, spot, taxa, vol_esperada, prazo,
               vol_implicita=None, dividendo=0.0, tem_acao=False, quantidade=1,
               retorno_esperado_ativo=None, limite=4):
    """Estruturas ordenadas por aderência ao contexto, já avaliadas.

    Devolve também as REJEITADAS com o motivo — saber por que uma estrutura
    não foi sugerida ensina mais que a lista das sugeridas.
    """
    visao = (visao or "lateral").lower().strip()
    objetivo = (objetivo or "direcional").lower().strip()
    if visao not in VISOES:
        return {"erro": f"Visão inválida. Use uma de: {', '.join(VISOES)}."}
    if objetivo not in OBJETIVOS:
        return {"erro": f"Objetivo inválido. Use um de: {', '.join(OBJETIVOS)}."}

    preco_vol, razao_vol = classificar_volatilidade(vol_implicita, vol_esperada)
    preco_vol = preco_vol or "justa"
    vol_mercado = opcoes._num(vol_implicita) or vol_esperada

    # Sem expectativa explícita de retorno, deriva-se da visão: é premissa, e
    # aparece carimbada no resultado para ninguém confundir com previsão.
    if retorno_esperado_ativo is None:
        retorno_esperado_ativo = {
            "alta_forte": 0.30, "alta_moderada": 0.15, "lateral": 0.0,
            "baixa_moderada": -0.15, "baixa_forte": -0.30,
        }[visao]

    sugeridas, rejeitadas = [], []
    for chave, ficha in CATALOGO.items():
        pontos, razoes = _aderencia(ficha, visao, objetivo, preco_vol, tem_acao)
        if pontos is None:
            rejeitadas.append({"chave": chave, "nome": ficha["nome"], "motivo": razoes[0]})
            continue
        # Os prêmios saem da volatilidade IMPLÍCITA — é o preço de mercado, o
        # que você de fato paga ou recebe. O resultado esperado sai da
        # volatilidade que você espera REALIZAR. A diferença entre as duas é a
        # aposta inteira: vender a 48% e realizar 35% é onde o dinheiro está.
        # Usar a esperada nos dois lados apagava exatamente esse efeito.
        pernas = montar(chave, spot, taxa, vol_mercado, prazo, dividendo, quantidade)
        if not pernas:
            rejeitadas.append({"chave": chave, "nome": ficha["nome"],
                               "motivo": "não foi possível montar com estes parâmetros"})
            continue
        avaliacao = opcoes.avaliar_estrutura(
            pernas, spot, taxa, prazo, vol_esperada, retorno_esperado_ativo,
            dividendo, vol_precificacao=vol_mercado)
        if "erro" in avaliacao:
            rejeitadas.append({"chave": chave, "nome": ficha["nome"],
                               "motivo": avaliacao["erro"]})
            continue
        sugeridas.append({
            "chave": chave, "nome": ficha["nome"], "resumo": ficha["resumo"],
            "quando": ficha["quando"], "quando_nao": ficha["quando_nao"],
            "aderencia": pontos, "razoes": razoes, "avaliacao": avaliacao,
        })

    sugeridas.sort(key=lambda item: (-item["aderencia"],
                                     -(item["avaliacao"]["probabilidade_lucro"] or 0)))

    return {
        "visao": visao, "objetivo": objetivo,
        "preco_volatilidade": preco_vol,
        "razao_implicita_esperada": round(razao_vol * 100, 1) if razao_vol is not None else None,
        "leitura_volatilidade": {
            "cara": ("A implícita está acima da volatilidade que você espera realizar. "
                     "Estruturas que VENDEM prêmio saem favorecidas; comprar opção "
                     "aqui é pagar por um movimento maior do que você mesmo projeta."),
            "barata": ("A implícita está abaixo da sua expectativa de volatilidade. "
                       "Estruturas que COMPRAM prêmio saem favorecidas."),
            "justa": ("Implícita e expectativa estão próximas: a decisão fica por conta "
                      "da direção e do objetivo, não do preço da volatilidade."),
        }[preco_vol],
        "retorno_esperado_ativo": retorno_esperado_ativo,
        "sugeridas": sugeridas[:limite],
        "rejeitadas": rejeitadas,
        "premissa": ("Prêmios são TEÓRICOS, do modelo, com a volatilidade que você "
                     "informou. Ao operar, substitua pelos preços de tela — a diferença "
                     "entre os dois é o que diz se o mercado está pagando bem."),
    }
