"""Indicadores do motor multicritério: momentum, Bazin e Greenblatt.

Funções puras sobre séries e números. Nenhuma toca a rede, nenhuma levanta:
campo que não fecha volta None, nunca zero. Zero na tela é lido como medição, e
foi assim que empresa lucrativa já apareceu com ROE 0 neste projeto.

DUAS APROXIMAÇÕES QUE VOCÊ PRECISA CONHECER, PORQUE MUDAM O NÚMERO
------------------------------------------------------------------
1. EBITDA não existe na DFP. A CVM não padroniza depreciação numa conta
   própria — reconstruí-la exigiria a DVA ou as notas explicativas. Usamos
   EBIT. Como EBIT < EBITDA, a alavancagem sai MAIOR que a real: o filtro erra
   para o lado de reprovar, nunca de aprovar. Por isso o campo se chama
   `dl_ebit`, e não `dl_ebitda`.

2. O ROIC de Greenblatt original é EBIT sobre capital de giro líquido mais
   ativo fixo líquido, excluindo ágio e caixa excedente — contas que a DFP não
   separa. Usamos capital empregado (ativo total menos passivo circulante), que
   é a definição padrão mais próxima com os dados disponíveis. Ranking fica
   comparável entre papéis; o nível absoluto não é o do livro.
"""

import numpy as np
import pandas as pd

# --- momentum ---------------------------------------------------------------
EMA_CURTA = 21
EMA_LONGA = 50
IFR_MINIMO = 50.0            # abaixo disso não há força; é queda, não tendência
IFR_MAXIMO = 68.0            # acima disso já esticou: entrada tardia
ADTV_MINIMO = 5_000_000.0    # liquidez: abaixo disso a saída custa o ganho
JANELA_ADTV = 20
JANELA_BOLLINGER = 20
DESVIOS_BOLLINGER = 2.0
JANELA_PERCENTIL = 126       # ~6 meses de pregão
PERCENTIL_COMPRESSAO = 25.0  # largura no quartil inferior da própria história
VOLUME_RELATIVO_MINIMO = 1.3

# --- Bazin ------------------------------------------------------------------
YIELD_BAZIN = 0.06           # o teto do Bazin é o preço que dá 6% de yield
DY_MINIMO_BAZIN = 6.0
PAYOUT_MINIMO = 30.0
PAYOUT_MAXIMO = 80.0
TETO_DL_EBIT = 2.5

# --- Greenblatt -------------------------------------------------------------
# Banco e seguradora ficam fora: EV/EBIT e ROIC não descrevem instituição
# financeira, cujo passivo é o negócio e não o financiamento dele.
SETORES_EXCLUIDOS = ("Financeiro",)


def _serie(valores):
    if valores is None:
        return None
    s = pd.Series(valores).astype(float).dropna()
    return s if len(s) else None


def _num(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def ema(close, periodo):
    """Média exponencial. Exige `periodo` pregões — média de 5 dias chamada de
    EMA21 é outra coisa, e mentiria sobre a tendência."""
    s = _serie(close)
    if s is None or len(s) < periodo:
        return None
    return float(s.ewm(span=periodo, adjust=False).mean().iloc[-1])


def adtv(close, volume, janela=JANELA_ADTV):
    """Volume financeiro médio (R$). Volume em quantidade não diz liquidez:
    um milhão de cotas a R$ 0,80 não é o mesmo mercado que a R$ 80."""
    c, v = _serie(close), _serie(volume)
    if c is None or v is None:
        return None
    financeiro = (c * v).dropna()
    if len(financeiro) < janela:
        return None
    return float(financeiro.rolling(janela).mean().iloc[-1])


def largura_bollinger(close, janela=JANELA_BOLLINGER, desvios=DESVIOS_BOLLINGER):
    """Largura das bandas como fração da média — comparável entre papéis de
    preços muito diferentes, o que a largura em reais não seria."""
    s = _serie(close)
    if s is None or len(s) < janela:
        return None
    media = s.rolling(janela).mean()
    desvio = s.rolling(janela).std(ddof=0)
    largura = (2 * desvios * desvio) / media
    largura = largura.replace([np.inf, -np.inf], np.nan).dropna()
    return largura if len(largura) else None


def volume_relativo(volume, curta=5, longa=JANELA_ADTV):
    """Volume dos últimos dias contra o normal do papel. É o que separa
    compressão que vai romper de compressão que é só marasmo."""
    v = _serie(volume)
    if v is None or len(v) < longa:
        return None
    media_longa = float(v.rolling(longa).mean().iloc[-1])
    if media_longa <= 0:
        return None
    return float(v.rolling(curta).mean().iloc[-1]) / media_longa


def compressao_volatilidade(close, volume):
    """{comprimido, largura, percentil, volume_relativo}.

    Compressão é medida contra a PRÓPRIA história do papel, não contra um
    limiar fixo: a largura normal de WEGE3 e de MGLU3 são grandezas diferentes,
    e um corte absoluto marcaria sempre os mesmos papéis.
    """
    resultado = {"comprimido": False, "largura": None, "percentil": None,
                 "volume_relativo": None}

    serie_largura = largura_bollinger(close)
    if serie_largura is None:
        return resultado

    recorte = serie_largura.tail(JANELA_PERCENTIL)
    if len(recorte) < JANELA_BOLLINGER * 2:
        return resultado

    atual = float(recorte.iloc[-1])
    percentil = float((recorte <= atual).mean() * 100.0)
    relativo = volume_relativo(volume)

    resultado.update(largura=atual, percentil=percentil, volume_relativo=relativo)
    resultado["comprimido"] = bool(
        percentil <= PERCENTIL_COMPRESSAO
        and relativo is not None
        and relativo >= VOLUME_RELATIVO_MINIMO
    )
    return resultado


def avaliar_momentum(close, volume, ifr):
    """Regra completa do segmento. Devolve os componentes, não só o veredito —
    saber QUAL critério reprovou vale mais que saber que reprovou."""
    c = _serie(close)
    preco = float(c.iloc[-1]) if c is not None and len(c) else None
    e21, e50 = ema(close, EMA_CURTA), ema(close, EMA_LONGA)
    liquidez = adtv(close, volume)
    ifr = _num(ifr)
    compressao = compressao_volatilidade(close, volume)

    criterios = {
        "preco_acima_ema21": None if (preco is None or e21 is None) else preco > e21,
        "ema21_acima_ema50": None if (e21 is None or e50 is None) else e21 > e50,
        "ifr_na_faixa": None if ifr is None else IFR_MINIMO <= ifr <= IFR_MAXIMO,
        "liquidez_suficiente": None if liquidez is None else liquidez >= ADTV_MINIMO,
    }
    # None é "não apurado", não "reprovado": um papel sem volume no arquivo do
    # dia não pode ser dado como ilíquido.
    faltantes = [k for k, v in criterios.items() if v is None]
    aprovado = not faltantes and all(criterios.values())

    return {
        "preco": preco, "ema21": e21, "ema50": e50, "ifr": ifr,
        "adtv": liquidez, "criterios": criterios, "criterios_nao_apurados": faltantes,
        "aprovado": aprovado, "compressao": compressao,
    }


# --------------------------------------------------------------------------- #
# Dividendos e valor — Bazin
# --------------------------------------------------------------------------- #
def dividendos_12m(serie_dividendos, indice_precos=None):
    """Provento por ação nos últimos 12 meses, ancorado em HOJE.

    A âncora importa: ancorar na data do último provento faria uma empresa que
    parou de pagar em 2023 exibir o yield de 2023 para sempre. Ancorando no
    presente, quem parou de pagar aparece com zero — que é a verdade.

    Série vazia devolve None (não sabemos); série sem pagamento na janela
    devolve 0.0 (sabemos que não pagou). São coisas diferentes.
    """
    s = _serie(serie_dividendos)
    if s is None:
        return None
    try:
        s = s[s > 0]
        if not len(s):
            return 0.0
        fim = (pd.Timestamp.now(tz="UTC").tz_localize(None)
               if indice_precos is None else pd.Timestamp(indice_precos[-1]))
        indice = pd.to_datetime(s.index)
        if getattr(indice, "tz", None) is not None:
            indice = indice.tz_localize(None)
        s = pd.Series(s.values, index=indice)
        return float(s[s.index >= fim - pd.Timedelta(days=365)].sum())
    except Exception:  # noqa: BLE001
        return None


def preco_teto_bazin(dpa_12m, taxa=YIELD_BAZIN):
    """Preço que faria o provento atual render `taxa`.

    Bazin usava 6%: acima desse preço, o dividendo não paga o risco de bolsa.
    Empresa que não pagou nada não tem teto — devolve None, e não zero: teto
    zero seria lido como "nunca comprar", quando o correto é "não se aplica".
    """
    dpa = _num(dpa_12m)
    if dpa is None or dpa <= 0 or taxa <= 0:
        return None
    return dpa / taxa


def margem_de_seguranca(preco_teto, preco_atual):
    """Quanto o papel ainda pode subir até bater o teto, em %. Negativo
    significa negociando acima do teto."""
    teto, preco = _num(preco_teto), _num(preco_atual)
    if teto is None or preco is None or preco <= 0:
        return None
    return (teto / preco - 1.0) * 100.0


def payout(dpa_12m, lpa):
    """Fração do lucro distribuída, em %.

    Calculado POR AÇÃO dos dois lados — provento por ação do histórico de
    preços, lucro por ação da DFP. Misturar total com por-ação exigiria o
    número de ações, que a DFP não publica direto e que teríamos de inferir.

    Prejuízo não gera payout: dividir por LPA negativo produz percentual
    negativo sem significado.
    """
    dpa, lucro_acao = _num(dpa_12m), _num(lpa)
    if dpa is None or lucro_acao is None or lucro_acao <= 0:
        return None
    return dpa / lucro_acao * 100.0


def divida_liquida(curto, longo, caixa):
    """Dívida onerosa menos caixa. Sem caixa não há dívida líquida — dívida
    bruta chamada de líquida superestima a alavancagem."""
    c, l, cx = _num(curto), _num(longo), _num(caixa)
    if c is None and l is None:
        return None
    if cx is None:
        return None
    return (c or 0.0) + (l or 0.0) - cx


def dl_sobre_ebit(divida_liq, ebit):
    """Alavancagem sobre EBIT (não EBITDA — ver o cabeçalho do módulo).

    Caixa líquido (dívida negativa) devolve 0.0: a empresa não deve nada, e
    devolver o número negativo faria a ordenação premiar duplamente.
    EBIT não positivo devolve None — não existe alavancagem sustentável sem
    geração operacional, e o filtro trata como não apurado, reprovando.
    """
    dl, resultado = _num(divida_liq), _num(ebit)
    if dl is None or resultado is None or resultado <= 0:
        return None
    return max(dl / resultado, 0.0)


def avaliar_bazin(preco, dpa_12m, dy_12m, lpa, divida_liq, ebit):
    """Segmento completo. Devolve o porquê de cada reprovação."""
    teto = preco_teto_bazin(dpa_12m)
    margem = margem_de_seguranca(teto, preco)
    pay = payout(dpa_12m, lpa)
    alavancagem = dl_sobre_ebit(divida_liq, ebit)
    dy = _num(dy_12m)

    criterios = {
        "dy_suficiente": None if dy is None else dy > DY_MINIMO_BAZIN,
        "payout_saudavel": None if pay is None else PAYOUT_MINIMO <= pay <= PAYOUT_MAXIMO,
        "alavancagem_ok": None if alavancagem is None else alavancagem < TETO_DL_EBIT,
        "abaixo_do_teto": None if margem is None else margem > 0,
    }
    faltantes = [k for k, v in criterios.items() if v is None]
    return {
        "preco_teto": teto, "margem_seguranca": margem, "payout": pay,
        "dl_ebit": alavancagem, "dy_12m": dy, "dpa_12m": _num(dpa_12m),
        "criterios": criterios, "criterios_nao_apurados": faltantes,
        "aprovado": not faltantes and all(criterios.values()),
    }


# --------------------------------------------------------------------------- #
# Greenblatt — Magic Formula
# --------------------------------------------------------------------------- #
def acoes_implicitas(lucro_liquido, lpa):
    """Número de ações a partir de lucro e LPA. A DFP não publica a quantidade
    de ações numa conta padronizada; lucro/LPA é a via disponível."""
    lucro, lucro_acao = _num(lucro_liquido), _num(lpa)
    if lucro is None or lucro_acao is None or lucro_acao == 0:
        return None
    acoes = lucro / lucro_acao
    return acoes if acoes > 0 else None


def enterprise_value(preco, acoes, divida_liq):
    """Valor de mercado mais dívida líquida — o que custaria comprar a empresa
    inteira e quitar a dívida dela."""
    p, n, dl = _num(preco), _num(acoes), _num(divida_liq)
    if p is None or n is None or dl is None or p <= 0 or n <= 0:
        return None
    ev = p * n + dl
    return ev if ev > 0 else None


def ev_sobre_ebit(ev, ebit):
    """Quanto se paga por unidade de resultado operacional. EBIT não positivo
    não gera múltiplo: o papel simplesmente não entra no ranking."""
    valor, resultado = _num(ev), _num(ebit)
    if valor is None or resultado is None or resultado <= 0:
        return None
    return valor / resultado


def capital_empregado(ativo_total, passivo_circulante):
    """Ativo total menos passivo circulante — o capital que a operação de fato
    consome. Ver a ressalva 2 no cabeçalho do módulo."""
    ativo, circulante = _num(ativo_total), _num(passivo_circulante)
    if ativo is None or circulante is None:
        return None
    capital = ativo - circulante
    return capital if capital > 0 else None


def roic(ebit, capital):
    """Retorno sobre o capital empregado, em %."""
    resultado, base = _num(ebit), _num(capital)
    if resultado is None or base is None or base <= 0:
        return None
    return resultado / base * 100.0


def ranking_greenblatt(candidatos):
    """Ranking combinado: menor EV/EBIT e maior ROIC.

    Cada papel recebe uma posição em cada lista e a soma decide. O ponto da
    fórmula é esse: não procura o mais barato nem o mais rentável, procura a
    melhor combinação dos dois — comprar um bom negócio a um preço razoável.

    `candidatos` são dicts com ticker, ev_ebit e roic. Quem não tiver os dois
    fica de fora: ranquear com um lado ausente inventaria uma posição.
    """
    elegiveis = [c for c in candidatos
                 if c.get("ev_ebit") is not None and c.get("roic") is not None]
    if not elegiveis:
        return []

    por_ev = sorted(elegiveis, key=lambda c: c["ev_ebit"])
    por_roic = sorted(elegiveis, key=lambda c: -c["roic"])
    posicao_ev = {c["ticker"]: i + 1 for i, c in enumerate(por_ev)}
    posicao_roic = {c["ticker"]: i + 1 for i, c in enumerate(por_roic)}

    saida = []
    for c in elegiveis:
        pev, proic = posicao_ev[c["ticker"]], posicao_roic[c["ticker"]]
        saida.append({**c, "posicao_ev_ebit": pev, "posicao_roic": proic,
                      "posicao_combinada": pev + proic})
    saida.sort(key=lambda c: (c["posicao_combinada"], c["ev_ebit"]))
    for i, c in enumerate(saida):
        c["rank_greenblatt"] = i + 1
    return saida


# --------------------------------------------------------------------------- #
# Score normalizado
# --------------------------------------------------------------------------- #
def _faixa(valor, pontos):
    """Pontuação por faixa graduada. `pontos` é [(limite, pontos), ...] em
    ordem crescente de limite; o primeiro limite que couber vence."""
    v = _num(valor)
    if v is None:
        return None
    for limite, ponto in pontos:
        if v <= limite:
            return ponto
    return pontos[-1][1]


PONTOS_MARGEM = [(0, 0), (10, 12), (20, 20), (35, 28), (50, 34), (float("inf"), 38)]
PONTOS_DY = [(6, 0), (8, 10), (10, 16), (12, 20), (16, 18), (float("inf"), 12)]
PONTOS_PAYOUT = [(20, 2), (30, 8), (55, 18), (80, 14), (100, 5), (float("inf"), 0)]
PONTOS_ALAVANCAGEM = [(0.5, 24), (1.5, 20), (2.5, 14), (3.5, 6), (float("inf"), 0)]

PONTOS_IFR_MOMENTUM = [(50, 0), (56, 22), (62, 26), (68, 20), (float("inf"), 6)]
PONTOS_DISTANCIA_EMA = [(0, 0), (3, 22), (8, 18), (15, 12), (float("inf"), 5)]
PONTOS_LIQUIDEZ = [(5e6, 0), (2e7, 14), (1e8, 20), (float("inf"), 24)]


def _normalizar(pontos_obtidos, pontos_possiveis):
    """Score 0-100 normalizado pela COBERTURA, não pelo total teórico.

    Um papel com três dos quatro critérios apurados não pode ser penalizado por
    um dado que a fonte não trouxe — mas também não pode ganhar pontos que não
    mediu. Normalizar pelo que foi apurado resolve os dois lados; a cobertura
    viaja junto para a tela poder mostrá-la.
    """
    if not pontos_possiveis:
        return None
    return round(min(pontos_obtidos / pontos_possiveis, 1.0) * 100.0, 1)


def score_momentum(avaliacao):
    """0-100 para o segmento de tendência. Reprovado no filtro não zera o
    score — ele mede a QUALIDADE do setup; o campo `aprovado` diz se passou."""
    obtidos = possiveis = 0.0

    ifr_pontos = _faixa(avaliacao.get("ifr"), PONTOS_IFR_MOMENTUM)
    if ifr_pontos is not None:
        obtidos += ifr_pontos; possiveis += 26

    preco, e21 = _num(avaliacao.get("preco")), _num(avaliacao.get("ema21"))
    if preco and e21 and e21 > 0:
        # Distância curta acima da EMA21 é o melhor ponto de entrada: tendência
        # confirmada sem o papel já ter esticado.
        distancia = (preco / e21 - 1.0) * 100.0
        pontos = _faixa(distancia, PONTOS_DISTANCIA_EMA) if distancia > 0 else 0
        obtidos += pontos; possiveis += 22

    liquidez_pontos = _faixa(avaliacao.get("adtv"), PONTOS_LIQUIDEZ)
    if liquidez_pontos is not None:
        obtidos += liquidez_pontos; possiveis += 24

    e50 = _num(avaliacao.get("ema50"))
    if e21 is not None and e50 is not None:
        obtidos += 16 if e21 > e50 else 0; possiveis += 16

    compressao = avaliacao.get("compressao") or {}
    if compressao.get("percentil") is not None:
        obtidos += 12 if compressao.get("comprimido") else 0; possiveis += 12

    return _normalizar(obtidos, possiveis)


def score_bazin(avaliacao):
    obtidos = possiveis = 0.0
    for valor, pontos, teto in (
        (avaliacao.get("margem_seguranca"), PONTOS_MARGEM, 38),
        (avaliacao.get("dy_12m"), PONTOS_DY, 20),
        (avaliacao.get("payout"), PONTOS_PAYOUT, 18),
        (avaliacao.get("dl_ebit"), PONTOS_ALAVANCAGEM, 24),
    ):
        ganho = _faixa(valor, pontos)
        if ganho is not None:
            obtidos += ganho; possiveis += teto
    return _normalizar(obtidos, possiveis)


def score_greenblatt(posicao, total):
    """Posição no ranking vira score: o primeiro colocado tira 100, o último
    tira perto de zero. Linear na posição, não no múltiplo — é assim que a
    fórmula funciona, por ordem e não por magnitude."""
    p, n = _num(posicao), _num(total)
    if p is None or n is None or n <= 1:
        return None
    return round((1.0 - (p - 1) / (n - 1)) * 100.0, 1)


# Um papel de dividendo nunca vai pontuar em momentum, e um papel de momentum
# nunca vai pontuar em Bazin. Média entre os três puniria justamente o
# especialista — que é o que cada estratégia procura. O composto é o MELHOR
# segmento, e o nome do segmento vai junto para a tela dizer o que sustentou.
def compor_score_quant(scores):
    validos = {nome: s for nome, s in (scores or {}).items() if s is not None}
    if not validos:
        return None, None
    melhor = max(validos, key=validos.get)
    return validos[melhor], melhor


# --------------------------------------------------------------------------- #
# Itens não recorrentes
# --------------------------------------------------------------------------- #
# Impairment acima desta fração do EBIT torna o exercício atípico: o lucro
# daquele ano deixa de descrever a capacidade de geração da empresa, e P/L,
# ROE e margem calculados sobre ele descrevem o evento, não o negócio.
#
# O caso que motivou isto: Vale, exercício 2025. R$ 25,1 bi de perda por não
# recuperabilidade sobre EBIT de R$ 31,9 bi derrubaram o lucro para R$ 11,8 bi.
# O motor leu tudo certo — patrimônio, receita, lucro — e emitiu VENDA com
# score 25 sobre um P/L de 30,7x que era só denominador atípico. Ler certo e
# concluir errado é um defeito do modelo, não do dado.
LIMIAR_NAO_RECORRENTE = 0.20
ALIQUOTA_NOMINAL = 0.34   # IRPJ + CSLL: usada só para a estimativa indicativa


def exercicio_contaminado(perdas, ebit):
    """{contaminado, perdas, proporcao_ebit}. Nunca levanta.

    `perdas` vem negativa na DFP; o sinal não importa para a materialidade.
    """
    p, e = _num(perdas), _num(ebit)
    if p is None or e is None or e <= 0:
        return {"contaminado": False, "perdas": p, "proporcao_ebit": None}
    proporcao = abs(p) / e
    return {"contaminado": proporcao >= LIMIAR_NAO_RECORRENTE,
            "perdas": abs(p), "proporcao_ebit": proporcao}


def lucro_recorrente(lucro, perdas, aliquota=ALIQUOTA_NOMINAL):
    """Lucro somando de volta o impairment, líquido do efeito fiscal nominal.

    É ESTIMATIVA, e a tela diz isso. O efeito fiscal real depende de quanto da
    perda foi dedutível, o que só as notas explicativas informam — por isso o
    número serve para dimensionar, não para substituir o lucro publicado. Ele
    não entra em nenhum score: só acompanha o alerta, para você ver a ordem de
    grandeza do que o evento tirou.
    """
    base, p = _num(lucro), _num(perdas)
    if base is None or p is None:
        return None
    return base + abs(p) * (1.0 - aliquota)
