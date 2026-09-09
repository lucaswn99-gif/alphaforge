"""Otimização de carteira com restrição, encolhimento e comparação com o CDI.

POR QUE A VERSÃO ANTERIOR PRODUZIA 50% EM UM ATIVO
---------------------------------------------------
Três defeitos somados, e nenhum deles se resolve mexendo em parâmetro:

1. BUSCA ALEATÓRIA NÃO É OTIMIZAÇÃO. Sortear 20 mil vetores de peso em cinco
   dimensões cobre uma fração desprezível do simplex. O "melhor" encontrado é
   o sorteio que deu sorte, não o ótimo — e ele muda a cada execução. Aqui a
   busca é gradiente projetado: determinística, converge, e respeita limites.

2. SEM TETO POR ATIVO, Markowitz sempre produz solução de canto. É matemática,
   não acaso: o otimizador irrestrito empilha no ativo com melhor razão
   retorno/risco estimada. Nenhum alocador profissional entrega 50% num ativo,
   e o modelo não deveria propor.

3. MÉDIA HISTÓRICA É PÉSSIMO ESTIMADOR DE RETORNO ESPERADO. É por isso que a
   carteira ia para o HASH11: ele foi o que mais subiu na janela. Otimizar
   sobre médias cruas é otimizar sobre ruído — o erro de estimativa entra
   amplificado no resultado. Aqui os retornos são encolhidos na direção da
   média do conjunto, e a covariância na direção de uma diagonal.

E A PERGUNTA QUE FALTAVA
------------------------
Com a Selic a 14%, a comparação relevante não é entre carteiras — é contra o
CDI. Uma carteira de Sharpe 0,06 é pior que renda fixa pura, e chamar isso de
"alocação ótima" sem dizer que o CDI domina é o pior tipo de omissão num
terminal de assessoria. Toda resposta agora carrega essa comparação.
"""

import numpy as np

TETO_PADRAO = 0.25          # nenhum ativo passa de 25% por padrão
TETO_SETOR_PADRAO = 0.40    # nenhum setor ou classe passa de 40%
PISO_PADRAO = 0.0
ITERACOES = 600
PASSO = 0.08
BISSECOES = 30      # 30 passos já esgotam a precisão de float em [0, 1]
RODADAS_GRUPO = 12  # a redistribuição converge em poucas rodadas

# Encolhimento. Valores conservadores e deliberados: 50% nos retornos porque a
# média histórica é quase pura variância amostral; 20% na covariância, que é
# estimada com muito mais precisão que a média.
ENCOLHIMENTO_RETORNO = 0.50
ENCOLHIMENTO_COVARIANCIA = 0.20

# Abaixo disso a carteira não paga o risco: o CDI entrega o mesmo sem
# volatilidade. Não é regra de mercado, é o limiar em que a tela passa a dizer
# em voz alta que a renda fixa domina.
SHARPE_MINIMO_RELEVANTE = 0.20

OBJETIVOS = ("sharpe", "minima_variancia", "paridade_risco")


def encolher_retornos(mu, intensidade=ENCOLHIMENTO_RETORNO):
    """James-Stein simplificado: puxa cada retorno para a média do conjunto.

    O ativo que mais subiu quase nunca repete; o que menos subiu quase nunca
    repete o fundo. Encolher para a média é assumir isso explicitamente, em
    vez de fingir que a janela histórica é a expectativa.
    """
    mu = np.asarray(mu, dtype=float)
    return (1 - intensidade) * mu + intensidade * mu.mean()


def encolher_covariancia(sigma, intensidade=ENCOLHIMENTO_COVARIANCIA):
    """Ledoit-Wolf simplificado: puxa para uma diagonal de variância média.

    Matriz de covariância amostral com poucas observações por ativo é mal
    condicionada, e o otimizador explora justamente os pares cuja correlação
    foi estimada com mais erro.
    """
    sigma = np.asarray(sigma, dtype=float)
    n = sigma.shape[0]
    alvo = np.eye(n) * np.trace(sigma) / n
    return (1 - intensidade) * sigma + intensidade * alvo


def projetar_com_grupos(v, teto, piso, grupos, teto_grupo):
    """Projeção respeitando teto por ativo E por setor/classe.

    Sem o teto de grupo, "no máximo 25% por ativo" permite 100% em bancos com
    quatro papéis — que é concentração igual, disfarçada de diversificação. O
    teto de grupo é o que responde à pergunta real: quanto disso é a mesma
    aposta?

    Projeções alternadas: projeta no simplex com caixa, corta os grupos
    estourados para o teto, devolve o excedente aos ativos não saturados, e
    repete. Converge em poucas rodadas; ao fim as duas restrições valem.
    """
    if not grupos or teto_grupo is None or teto_grupo >= 1.0:
        return projetar(v, teto, piso)

    indices = {}
    for i, g in enumerate(grupos):
        indices.setdefault(g, []).append(i)

    # Teto de grupo impossível (poucos grupos para o limite) vira o mínimo
    # viável, em vez de travar a otimização.
    minimo_viavel = 1.0 / len(indices)
    teto_grupo = max(teto_grupo, minimo_viavel)

    w = projetar(v, teto, piso)
    for _ in range(RODADAS_GRUPO):
        excedente = 0.0
        saturados = set()
        for grupo, ids in indices.items():
            soma = w[ids].sum()
            if soma > teto_grupo + 1e-9:
                fator = teto_grupo / soma
                excedente += soma - teto_grupo
                w[ids] *= fator
                saturados.add(grupo)
        if excedente <= 1e-9:
            break
        # Devolve o excedente a quem ainda tem folga, proporcionalmente à
        # folga — distribuir por igual empurraria ativo pequeno para o teto.
        folga = np.array([
            0.0 if grupos[i] in saturados else max(teto - w[i], 0.0)
            for i in range(len(w))])
        total_folga = folga.sum()
        if total_folga <= 1e-12:
            break
        w = w + excedente * folga / total_folga
    return w / w.sum() if w.sum() > 0 else w


def projetar(v, teto, piso=PISO_PADRAO):
    """Projeção no conjunto {w : soma = 1, piso <= w <= teto}.

    Bisseção no multiplicador: w = clip(v - tau, piso, teto), procurando o tau
    que faz a soma dar 1. É o passo que transforma gradiente livre em
    gradiente COM restrição — sem ele o teto seria só um corte no fim, que
    distorce a solução em vez de otimizar dentro dela.
    """
    v = np.asarray(v, dtype=float)
    n = len(v)
    if teto * n < 1.0 - 1e-9:
        teto = 1.0 / n          # teto impossível: cai na carteira igualitária
    baixo, alto = (v - teto).min() - 1.0, (v - piso).max() + 1.0
    for _ in range(BISSECOES):
        tau = (baixo + alto) / 2.0
        soma = np.clip(v - tau, piso, teto).sum()
        if soma > 1.0:
            baixo = tau
        else:
            alto = tau
    w = np.clip(v - (baixo + alto) / 2.0, piso, teto)
    total = w.sum()
    return w / total if total > 0 else np.full(n, 1.0 / n)


def minimos_forcados(grupos, teto_grupo):
    """Quanto cada grupo é OBRIGADO a receber por causa do teto dos outros.

    Com três grupos e teto de 40%, os outros dois somam no máximo 80% — logo o
    terceiro recebe 20% no mínimo, mesmo que seja o pior ativo da lista. Foi o
    que aconteceu numa carteira real: o teto setorial empurrou 20% para um ETF
    de cripto, que ficou com 47% do risco total.

    Restrição que força alocação em vez de limitá-la precisa ser dita, não
    descoberta. Devolve {grupo: mínimo forçado em %}, só para os que passam de
    zero.
    """
    if not grupos or not teto_grupo:
        return {}
    nomes = list(dict.fromkeys(grupos))
    forcados = {}
    for nome in nomes:
        teto_dos_outros = teto_grupo * (len(nomes) - 1)
        minimo = max(0.0, 1.0 - teto_dos_outros)
        if minimo > 1e-9:
            forcados[nome] = round(minimo * 100, 2)
    return forcados


def _metricas(w, mu, sigma, taxa_livre):
    retorno = float(w @ mu)
    variancia = float(w @ sigma @ w)
    volatilidade = float(np.sqrt(max(variancia, 1e-18)))
    sharpe = (retorno - taxa_livre) / volatilidade if volatilidade > 0 else 0.0
    return retorno, volatilidade, sharpe


def _subir_gradiente(w0, gradiente, teto, piso, iteracoes=ITERACOES, passo=PASSO,
                     grupos=None, teto_grupo=None):
    """Gradiente projetado com passo decrescente. Determinístico."""
    proj = (lambda x: projetar_com_grupos(x, teto, piso, grupos, teto_grupo))
    w = proj(w0)
    for i in range(iteracoes):
        g = gradiente(w)
        norma = np.linalg.norm(g)
        if norma < 1e-12:
            break
        w = proj(w + (passo / (1 + i / 120.0)) * g / norma)
    return w


def maximizar_sharpe(mu, sigma, taxa_livre, teto=TETO_PADRAO, piso=PISO_PADRAO,
                     grupos=None, teto_grupo=None):
    n = len(mu)

    def gradiente(w):
        excedente = w @ mu - taxa_livre
        variancia = max(w @ sigma @ w, 1e-18)
        vol = np.sqrt(variancia)
        return mu / vol - excedente * (sigma @ w) / (vol ** 3)

    # Multi-início: a superfície é bem comportada, mas partir de um ponto só
    # deixa o resultado dependente do chute. Três partidas custam milissegundos.
    candidatos = [np.full(n, 1.0 / n),
                  np.maximum(mu, 0) + 1e-9,
                  1.0 / np.sqrt(np.maximum(np.diag(sigma), 1e-12))]
    melhor, melhor_sharpe = None, -np.inf
    for inicio in candidatos:
        w = _subir_gradiente(inicio, gradiente, teto, piso,
                             grupos=grupos, teto_grupo=teto_grupo)
        _, _, sharpe = _metricas(w, mu, sigma, taxa_livre)
        if sharpe > melhor_sharpe:
            melhor, melhor_sharpe = w, sharpe
    return melhor


def minimizar_variancia(sigma, teto=TETO_PADRAO, piso=PISO_PADRAO,
                        grupos=None, teto_grupo=None):
    """Não usa retorno esperado nenhum — e é essa a virtude.

    Como a média histórica é o estimador ruim, a carteira de mínima variância
    costuma se sair melhor fora da amostra que a de máximo Sharpe, justamente
    por não depender do parâmetro mal estimado.
    """
    n = sigma.shape[0]
    return _subir_gradiente(np.full(n, 1.0 / n), lambda w: -2.0 * (sigma @ w),
                            teto, piso, grupos=grupos, teto_grupo=teto_grupo)


def paridade_de_risco(sigma, teto=TETO_PADRAO, piso=PISO_PADRAO,
                      grupos=None, teto_grupo=None):
    """Cada ativo contribui com a mesma parcela do risco total.

    Diferente de peso igual: um ativo com o dobro da volatilidade entra com
    metade do peso. Também não usa retorno esperado — e é por isso que
    costuma se comportar melhor fora da amostra.

    Atualização multiplicativa (w_i <- w_i * alvo / contribuicao_i) em vez de
    gradiente sobre o erro quadrático: converge em dezenas de passos contra
    milhares, e é a formulação padrão do problema.
    """
    n = sigma.shape[0]
    w = 1.0 / np.sqrt(np.maximum(np.diag(sigma), 1e-12))
    w = w / w.sum()
    proj = lambda x: projetar_com_grupos(x, teto, piso, grupos, teto_grupo)  # noqa: E731

    for _ in range(120):
        marginal = sigma @ w
        contribuicao = np.maximum(w * marginal, 1e-18)
        alvo = float(w @ marginal) / n
        # Expoente 0.5 amortece o passo: 1.0 oscila em matrizes mal condicionadas.
        w_novo = proj(w * np.power(alvo / contribuicao, 0.5))
        if np.max(np.abs(w_novo - w)) < 1e-9:
            w = w_novo
            break
        w = w_novo
    return w


def fronteira(mu, sigma, teto=TETO_PADRAO, piso=PISO_PADRAO, pontos=14,
              grupos=None, teto_grupo=None):
    """Curva risco-retorno variando a aversão ao risco.

    Maximiza mu'w - lambda * w'Sigma w para lambda numa grade log. Cada lambda
    é um ponto da fronteira eficiente COM as mesmas restrições da carteira
    recomendada — desenhar a fronteira irrestrita ao lado de uma solução
    restrita compararia coisas diferentes.
    """
    n = len(mu)
    saida = []
    for lam in np.logspace(-1.2, 2.2, pontos):
        w = _subir_gradiente(np.full(n, 1.0 / n),
                             lambda w, lam=lam: mu - 2.0 * lam * (sigma @ w),
                             teto, piso, iteracoes=300, passo=0.08,
                             grupos=grupos, teto_grupo=teto_grupo)
        retorno = float(w @ mu)
        vol = float(np.sqrt(max(w @ sigma @ w, 1e-18)))
        saida.append({"retorno": round(retorno * 100, 3), "volatilidade": round(vol * 100, 3)})
    # Ordena por risco e remove pontos dominados (mesmo risco, retorno menor).
    saida.sort(key=lambda p: p["volatilidade"])
    limpa, teto_retorno = [], -np.inf
    for ponto in saida:
        if ponto["retorno"] > teto_retorno + 1e-9:
            limpa.append(ponto)
            teto_retorno = ponto["retorno"]
    return limpa


def otimizar(mu_bruto, sigma_bruto, taxa_livre, objetivo="sharpe",
             teto=TETO_PADRAO, piso=PISO_PADRAO, encolher=True,
             grupos=None, teto_grupo=TETO_SETOR_PADRAO):
    """Devolve pesos e métricas, com os retornos brutos e encolhidos à vista."""
    mu_bruto = np.asarray(mu_bruto, dtype=float)
    sigma_bruto = np.asarray(sigma_bruto, dtype=float)

    mu = encolher_retornos(mu_bruto) if encolher else mu_bruto
    sigma = encolher_covariancia(sigma_bruto) if encolher else sigma_bruto

    argumentos = {"grupos": grupos, "teto_grupo": teto_grupo}
    if objetivo == "minima_variancia":
        w = minimizar_variancia(sigma, teto, piso, **argumentos)
    elif objetivo == "paridade_risco":
        w = paridade_de_risco(sigma, teto, piso, **argumentos)
    else:
        objetivo = "sharpe"
        w = maximizar_sharpe(mu, sigma, taxa_livre, teto, piso, **argumentos)

    # As métricas de exibição saem da covariância ENCOLHIDA, a mesma que
    # decidiu os pesos — mostrar risco de uma matriz e otimizar noutra faria a
    # tela discordar do próprio cálculo.
    retorno, volatilidade, sharpe = _metricas(w, mu, sigma, taxa_livre)
    retorno_bruto = float(w @ mu_bruto)

    # Contribuição de risco: onde o risco realmente está, que quase nunca é
    # onde o peso está.
    marginal = sigma @ w
    variancia = max(float(w @ marginal), 1e-18)
    contribuicao = (w * marginal) / variancia

    return {
        "objetivo": objetivo,
        "pesos": w,
        "retorno_esperado": retorno,
        "retorno_historico_bruto": retorno_bruto,
        "volatilidade": volatilidade,
        "sharpe": sharpe,
        "contribuicao_risco": contribuicao,
        "teto_por_ativo": teto,
        "teto_por_grupo": teto_grupo,
        "minimos_forcados_por_grupo": minimos_forcados(grupos, teto_grupo),
        "grupos_distintos": len(dict.fromkeys(grupos)) if grupos else 0,
        "peso_por_grupo": (
            {g: round(float(sum(w[i] for i, x in enumerate(grupos) if x == g)) * 100, 2)
             for g in dict.fromkeys(grupos)} if grupos else None),
        "encolhimento_aplicado": bool(encolher),
        "supera_cdi": sharpe >= SHARPE_MINIMO_RELEVANTE,
    }
