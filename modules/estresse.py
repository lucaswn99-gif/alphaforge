"""Estresse macro da carteira: sensibilidade a juro e queda em evento de cauda.

Duas perguntas diferentes, e os dois motores aqui são função pura — recebem
balanço e série de preço já buscados, devolvem o resultado. A aritmética que
diz a alguém quanto ele pode perder precisa ser testável sem rede.

    sensibilidade_selic   e se o juro subir, o EBIT ainda paga os juros?
    estresse_historico    e se repetir 2008, 2020, quanto a carteira cai?

**O limite do simulador de Selic, declarado antes de qualquer número.** A DFP
não separa a dívida por indexador: não há como saber quanto do endividamento
de uma companhia é CDI, pré-fixado, IPCA+ ou moeda estrangeira. Este motor
trata TODA a dívida líquida como pós-fixada, o que faz do resultado o LIMITE
SUPERIOR da sensibilidade — o pior caso, não uma previsão. Uma empresa com
metade da dívida pré-fixada sofrerá menos que o número mostrado; nenhuma
sofrerá mais. É a única forma de errar para o lado seguro sem inventar uma
composição de dívida que o balanço não publica.

**Caixa líquido inverte o sinal.** Companhia com mais caixa que dívida GANHA
com juro alto. Tratar isso como risco zero seria perder metade da informação,
então ela aparece com o sinal invertido e o motivo.

**Evento de cauda mede a carteira, não a média dos papéis.** A queda máxima do
conjunto não é a média das quedas individuais: os papéis não fazem fundo no
mesmo dia, e a média sempre exagera. O motor reconstrói a série de valor da
carteira e mede o drawdown DELA.

**Papel sem histórico na janela não vira zero.** Uma empresa que listou em 2021
não tem 2008, e incluí-la como se tivesse ficado parada diluiria a queda. Ela
fica de fora do cálculo e aparece na contagem de cobertura — quem lê precisa
saber que porcentagem da carteira o número realmente descreve.
"""

import math

# EBIT sobre despesa de juros. Abaixo de 1, o resultado operacional não paga o
# serviço da dívida — a empresa precisa de caixa, venda de ativo ou dívida nova
# só para ficar onde está.
COBERTURA_CRITICA = 1.0
# Acima disto a folga é grande o bastante para o juro deixar de ser a pergunta.
COBERTURA_CONFORTAVEL = 3.0

# Cobertura acima disso é indistinguível de "dívida irrelevante" e o número
# grande só polui a tela.
COBERTURA_MAXIMA_EXIBIDA = 99.0

# Janelas de eventos de cauda da bolsa brasileira. Datas de fechamento, com
# folga nas pontas para o pico e o fundo caberem dentro da janela.
EVENTOS = (
    {"chave": "crise_2008", "nome": "Crise financeira de 2008",
     "inicio": "2008-05-01", "fim": "2008-12-31",
     "resumo": "Quebra do Lehman e fuga global de risco."},
    {"chave": "recessao_2015", "nome": "Recessão brasileira de 2015-16",
     "inicio": "2015-01-01", "fim": "2016-01-31",
     "resumo": "Perda do grau de investimento e recessão de dois anos."},
    {"chave": "joesley_2017", "nome": "Joesley Day",
     "inicio": "2017-05-15", "fim": "2017-06-30",
     "resumo": "Choque político de um dia, com circuit breaker."},
    {"chave": "pandemia_2020", "nome": "Pandemia de 2020",
     "inicio": "2020-01-20", "fim": "2020-04-30",
     "resumo": "Queda mais rápida da história do índice."},
    {"chave": "aperto_2021", "nome": "Aperto monetário de 2021-22",
     "inicio": "2021-06-01", "fim": "2022-07-31",
     "resumo": "Selic de 2% a 13,75% em pouco mais de um ano."},
)


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None


def divida_liquida(balanco):
    """Dívida onerosa menos caixa, ou None quando não dá para apurar.

    Ausência de dívida publicada é diferente de dívida zero: a primeira é
    desconhecimento, a segunda é informação. Só devolve número quando ao menos
    uma das contas de dívida existe.
    """
    if not balanco:
        return None
    curto = _numero(balanco.get("divida_curto_prazo"))
    longo = _numero(balanco.get("divida_longo_prazo"))
    if curto is None and longo is None:
        return None
    caixa = _numero(balanco.get("caixa")) or 0.0
    return (curto or 0.0) + (longo or 0.0) - caixa


def _cobertura(ebit, despesa):
    """EBIT sobre despesa de juros. None quando a conta não fecha."""
    if ebit is None or despesa is None:
        return None
    if despesa <= 0:
        return None           # sem despesa não há cobertura a medir
    return ebit / despesa


def sensibilidade_selic(posicoes, balancos, taxa_atual, taxa_nova):
    """Efeito de uma Selic diferente sobre a capacidade de pagar juros.

    `taxa_atual` e `taxa_nova` em porcentagem ao ano (13.75, não 0.1375).
    `balancos` é {ticker: balanço da CVM}; `posicoes` traz ticker, classe e
    valor da posição para o peso.

    Devolve linha por posição e o retrato do conjunto. FII e ETF ficam de fora:
    o endividamento de um fundo imobiliário não está no balanço de companhia,
    e ETF não tem dívida nenhuma.
    """
    atual = (_numero(taxa_atual) or 0.0) / 100.0
    nova = (_numero(taxa_nova) or 0.0) / 100.0

    linhas = []
    peso_total = 0.0
    peso_critico = 0.0
    peso_apurado = 0.0
    peso_beneficiado = 0.0

    for posicao in posicoes or []:
        if (posicao.get("classe") or "") != "acao":
            continue
        ticker = posicao.get("ticker")
        valor = float(posicao.get("valor_atual") or posicao.get("custo_total") or 0.0)
        peso_total += valor

        balanco = (balancos or {}).get(ticker) or {}
        divida = divida_liquida(balanco)
        ebit = _numero(balanco.get("ebit"))

        linha = {
            "ticker": ticker, "valor": round(valor, 2),
            "divida_liquida": None if divida is None else round(divida, 2),
            "ebit": None if ebit is None else round(ebit, 2),
            "exercicio": balanco.get("ano"),
            "caixa_liquido": bool(divida is not None and divida <= 0),
            "despesa_atual": None, "despesa_nova": None,
            "cobertura_atual": None, "cobertura_nova": None,
            "variacao_despesa": None, "estado": "nao_apurado", "motivo": None,
        }

        if divida is None or ebit is None:
            linha["motivo"] = ("Sem dívida ou EBIT no balanço da CVM para esta "
                               "companhia.")
            linhas.append(linha)
            continue

        peso_apurado += valor

        if divida <= 0:
            # Caixa líquido: juro alto vira receita financeira, não despesa.
            linha.update(estado="beneficiado",
                         motivo=("Caixa líquido maior que a dívida: juro mais "
                                 "alto aumenta a receita financeira."))
            peso_beneficiado += valor
            linhas.append(linha)
            continue

        despesa_atual = divida * atual
        despesa_nova = divida * nova
        cobertura_atual = _cobertura(ebit, despesa_atual)
        cobertura_nova = _cobertura(ebit, despesa_nova)

        linha.update(
            despesa_atual=round(despesa_atual, 2),
            despesa_nova=round(despesa_nova, 2),
            cobertura_atual=_arredondar_cobertura(cobertura_atual),
            cobertura_nova=_arredondar_cobertura(cobertura_nova),
            variacao_despesa=round(despesa_nova - despesa_atual, 2))

        if cobertura_nova is None:
            linha.update(estado="nao_apurado",
                         motivo="Taxa zero não produz despesa a medir.")
        elif cobertura_nova < COBERTURA_CRITICA:
            linha.update(
                estado="critico",
                motivo=(f"Com Selic a {taxa_nova:.2f}%, o EBIT cobriria "
                        f"{cobertura_nova:.2f}x a despesa de juros — abaixo "
                        f"de 1x o resultado operacional não paga o serviço "
                        f"da dívida."))
            peso_critico += valor
        elif cobertura_nova < COBERTURA_CONFORTAVEL:
            linha.update(
                estado="apertado",
                motivo=(f"Cobertura cairia para {cobertura_nova:.2f}x — ainda "
                        f"paga, com pouca folga."))
        else:
            linha.update(estado="folgado",
                         motivo=f"Cobertura de {cobertura_nova:.2f}x: o juro "
                                f"não é a pergunta desta companhia.")
        linhas.append(linha)

    # Ordena pelo que pede olhar primeiro, e depois pelo tamanho da posição.
    ordem = {"critico": 0, "apertado": 1, "nao_apurado": 2, "folgado": 3,
             "beneficiado": 4}
    linhas.sort(key=lambda l: (ordem.get(l["estado"], 9), -l["valor"]))

    def parte(valor):
        return round(valor / peso_total * 100.0, 2) if peso_total else 0.0

    return {
        "taxa_atual": round(_numero(taxa_atual) or 0.0, 2),
        "taxa_nova": round(_numero(taxa_nova) or 0.0, 2),
        "posicoes": linhas,
        "valor_em_acoes": round(peso_total, 2),
        "pct_critico": parte(peso_critico),
        "pct_apurado": parte(peso_apurado),
        "pct_beneficiado": parte(peso_beneficiado),
        "criticas": sum(1 for l in linhas if l["estado"] == "critico"),
        "avaliadas": len(linhas),
        "limite": ("Toda a dívida líquida é tratada como pós-fixada. A DFP não "
                   "separa a dívida por indexador, então este é o LIMITE "
                   "SUPERIOR da sensibilidade — o pior caso, não uma previsão."),
    }


def _arredondar_cobertura(valor):
    if valor is None:
        return None
    return round(min(valor, COBERTURA_MAXIMA_EXIBIDA), 2)


def drawdown_maximo(serie):
    """Maior queda de topo a fundo de uma série, em porcentagem negativa.

    Recebe uma lista de valores em ordem cronológica. Devolve None para série
    curta demais para ter topo e fundo.
    """
    valores = [v for v in (serie or []) if v is not None and not math.isnan(v)]
    if len(valores) < 2:
        return None
    topo = valores[0]
    pior = 0.0
    for valor in valores:
        if valor > topo:
            topo = valor
        if topo > 0:
            queda = (valor - topo) / topo
            if queda < pior:
                pior = queda
    return round(pior * 100.0, 2)


def estresse_historico(series, pesos):
    """Queda máxima da CARTEIRA na janela, e não a média das quedas.

    `series` é {ticker: [fechamentos na janela, em ordem]}; `pesos` é
    {ticker: peso}. Os papéis não fazem fundo no mesmo dia, e a média das
    quedas individuais sempre exagera a queda do conjunto — por isso o motor
    reconstrói a série de valor da carteira e mede o drawdown dela.

    Só entram os papéis com histórico na janela. Os demais aparecem em
    `sem_historico`, com o peso que ficou de fora: quem lê precisa saber que
    porcentagem da carteira o número descreve.
    """
    series = series or {}
    pesos = pesos or {}

    com_dado = {t: s for t, s in series.items()
                if s and len(s) >= 2 and pesos.get(t) and s[0]}
    sem_dado = sorted(t for t in pesos if t not in com_dado)

    peso_total = sum(pesos.get(t, 0.0) for t in pesos)
    peso_coberto = sum(pesos.get(t, 0.0) for t in com_dado)

    if not com_dado or peso_coberto <= 0:
        return {"drawdown_pct": None, "pior_papel": None,
                "cobertura_pct": 0.0, "com_historico": 0,
                "sem_historico": sem_dado,
                "motivo": ("Nenhuma posição da carteira tem histórico de preço "
                           "nesta janela.")}

    # Todas as séries no mesmo comprimento: o menor entre elas. Preencher a
    # mais curta repetindo valor criaria um platô que não existiu.
    tamanho = min(len(s) for s in com_dado.values())
    carteira = []
    for i in range(tamanho):
        total = 0.0
        for ticker, serie in com_dado.items():
            # Normaliza pelo primeiro fechamento: o que importa é a variação,
            # e somar preços sem normalizar daria peso ao papel mais caro.
            total += (pesos[ticker] / peso_coberto) * (serie[i] / serie[0])
        carteira.append(total)

    quedas = {t: drawdown_maximo(s[:tamanho]) for t, s in com_dado.items()}
    quedas = {t: q for t, q in quedas.items() if q is not None}
    pior = min(quedas, key=quedas.get) if quedas else None

    return {
        "drawdown_pct": drawdown_maximo(carteira),
        "pior_papel": ({"ticker": pior, "drawdown_pct": quedas[pior]}
                       if pior else None),
        "cobertura_pct": round(peso_coberto / peso_total * 100.0, 2) if peso_total else 0.0,
        "com_historico": len(com_dado),
        "sem_historico": sem_dado,
        "pregoes": tamanho,
        "motivo": None,
    }
