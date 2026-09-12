"""Motor de filosofias de investimento: Bogle, Barsi e Greenblatt.

Três escolas com horizontes e perguntas diferentes, num motor só porque
compartilham a mesma exigência: transformar critério declarado em número
auditável. Cada saída carrega **por que** um ativo passou ou não — lista que
só diz "compre" não é analisável, e critério que ninguém vê não é critério.

Três decisões estruturais:

  - **Momentum não é cruzamento de média.** O filtro global usa o 12M-1M
    acadêmico: retorno acumulado de doze meses ignorando o mês mais recente.
    O mês pulado é o ponto inteiro da métrica — o curtíssimo prazo reverte, e
    incluí-lo transforma um fator de continuidade num fator de reversão. Média
    móvel cruzada dá o mesmo sinal com defasagem e sem base empírica.

  - **"Não apurado" nunca vira zero.** Um ativo sem EBIT publicado não tem
    dívida/EBITDA infinita nem nula: tem indicador ausente, que não pontua nem
    a favor nem contra e aparece na lista de ressalvas. É o que separa um
    filtro de um gerador de ruído.

  - **Nada aqui é recomendação de investimento.** São filtros quantitativos
    aplicados igualmente a todos os ativos do universo, sem considerar perfil,
    objetivo ou situação de ninguém. O alvo da carteira Bogle, em particular, é
    política de quem investe — o motor calcula distância até o alvo, não
    escolhe o alvo.
"""

import logging
from datetime import datetime

import pandas as pd

from modules import cadastro_b3, fontes, fundamentos_cvm, taxas

registro = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Universos
# --------------------------------------------------------------------------

# BESST: bancos, energia, saneamento, seguros e telecomunicações. A lista é
# curada à mão porque o cadastro da B3 que este projeto guarda traz CNPJ e
# razão social, não setor — e BESST não é uma classificação setorial da bolsa,
# é uma tese sobre setores perenes e regulados. Derivar isso de um campo que
# não existe seria inventar precisão.
UNIVERSO_BESST = {
    "bancos": ["BBAS3", "ITSA4", "BBDC4", "ITUB4", "SANB11", "ABCB4", "BRSR6"],
    "energia": ["TAEE11", "TRPL4", "CMIG4", "CPLE6", "EGIE3", "ELET3",
                "NEOE3", "CPFE3", "ALUP11", "EQTL3"],
    "saneamento": ["SBSP3", "SAPR11", "CSMG3"],
    "seguros": ["BBSE3", "PSSA3", "CXSE3", "WIZC3"],
    "telecomunicacoes": ["VIVT3", "TIMS3"],
}

# Quantas ações cada unit embala. Sem isto o payout sai multiplicado pelo
# número de ações da unit: o provento é pago POR UNIT, enquanto o `lpa_on` da
# CVM é por ação ORDINÁRIA. Dividir um pelo outro comparou grandezas
# diferentes e reprovou a TAEE11 com "payout de 211%" — quando o real é 70%.
ACOES_POR_UNIT = {
    "TAEE11": 3,   # 1 ON + 2 PN
    "SANB11": 2,   # 1 ON + 1 PN
    "ALUP11": 3,   # 1 ON + 2 PN
    "SAPR11": 5,   # 1 ON + 4 PN
    "KLBN11": 5,   # 1 ON + 4 PN
    "ENGI11": 5,   # 1 ON + 4 PN
    "BPAC11": 3,   # 1 ON + 2 PN
}

# Acima disto o payout não é notícia sobre a empresa: é sinal de que o
# denominador está errado. Reprovar por número em que não se acredita é pior
# que admitir que não se sabe — então vira "não apurado".
PAYOUT_IMPLAUSIVEL = 150.0

# Quantos dos três critérios de qualidade (payout, alavancagem, constância de
# lucro) precisam ter sido REALMENTE medidos para um papel poder ser aprovado.
#
# Sem isso, "não apurado não pontua contra" vira aprovação por omissão: o
# BBAS3 passou com payout e alavancagem ambos ausentes, sustentado só pelo
# preço. Num filtro de renda, aprovar empresa cuja sustentabilidade do
# provento não se conseguiu medir é o erro mais caro possível. Mesmo espírito
# do MINIMO_CRITERIOS de `credito_score`.
MINIMO_CRITERIOS = 2

# Setores em que dívida/EBITDA não tem leitura: banco e seguradora captam
# recurso como matéria-prima, não como alavancagem. Para eles o critério
# equivalente é Basileia — que a base da CVM não publica (ver `_alavancagem`).
SETORES_FINANCEIROS = {"bancos", "seguros"}

# Universo americano padrão. Lista curada de nomes líquidos; financeiras e
# utilities entram aqui e são removidas em tempo de execução pelo setor que o
# Yahoo devolve — filtrar por nome na mão envelhece, filtrar pelo campo não.
UNIVERSO_EUA = [
    "AAPL", "MSFT", "GOOGL", "META", "NVDA", "AVGO", "ORCL", "CSCO", "ADBE",
    "CRM", "ACN", "TXN", "QCOM", "AMAT", "LRCX", "KLAC", "INTC", "IBM", "NOW",
    "AMZN", "HD", "MCD", "NKE", "SBUX", "LOW", "TJX", "BKNG", "ORLY", "AZO",
    "TSLA", "F", "GM", "EBAY", "YUM", "DHI", "LEN",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "DHR", "AMGN",
    "GILD", "CVS", "MCK", "COR", "HCA", "ELV", "ZTS", "SYK", "BSX", "MDT",
    "PG", "KO", "PEP", "COST", "WMT", "MDLZ", "CL", "KMB", "GIS", "KHC",
    "MO", "PM", "STZ", "HSY", "K", "SYY", "TGT", "DG",
    "CAT", "DE", "HON", "UNP", "UPS", "RTX", "LMT", "GD", "NOC", "BA",
    "MMM", "GE", "EMR", "ETN", "ITW", "PH", "CMI", "PCAR", "CSX", "NSC",
    "XOM", "CVX", "COP", "EOG", "SLB", "PSX", "VLO", "MPC", "OXY",
    "LIN", "SHW", "APD", "ECL", "NUE", "DOW", "FCX",
    "DIS", "NFLX", "CMCSA", "T", "VZ", "TMUS", "EA", "TTWO",
]

# --------------------------------------------------------------------------
# Parâmetros dos critérios
# --------------------------------------------------------------------------

# Barsi
YIELD_ALVO_BARSI = 0.06          # Preço teto = DPA projetado / 6%.
MARGEM_SEGURANCA_MINIMA = 0.10   # Só entra com 10% de desconto sobre o teto.
PAYOUT_MINIMO = 30.0
PAYOUT_MAXIMO = 80.0
DIVIDA_EBITDA_MAXIMA = 3.0
ANOS_DPA = 3                     # Média dos três últimos exercícios fechados.
BASILEIA_MINIMA = 13.0           # Documentado; ver `_alavancagem` sobre a fonte.

# Greenblatt
SHAREHOLDER_YIELD_MINIMO = 5.0
TOP_GREENBLATT = 20
SETORES_EXCLUIDOS_EUA = {"Financial Services", "Financials", "Utilities",
                         "Real Estate"}

# Momentum 12M-1M
MESES_JANELA = 12
MESES_PULADOS = 1
QUEDA_ESTRUTURAL = -0.20         # Abaixo disto o ativo sai da lista.
RSI_SOBREVENDIDO = 30.0
PERIODO_RSI = 14

# Bogle
BANDA_REBALANCEAMENTO = 0.05     # 5 pontos percentuais de tolerância.
ALVO_BOGLE_PADRAO = {"WRLD11.SA": 40.0, "VOO": 40.0, "SCHD": 20.0}

DIAS_POR_ANO = 252


# --------------------------------------------------------------------------
# Indicadores
# --------------------------------------------------------------------------

def rsi_wilder(serie, periodo=PERIODO_RSI):
    """RSI com a suavização de Wilder. None se a série for curta demais.

    `routers/equity.py` tem a sua própria implementação para o caso diário; a
    duplicação é deliberada. Um módulo importar de um router inverteria a
    dependência do projeto — o router é quem monta a resposta, não quem
    fornece cálculo.
    """
    if serie is None or len(serie) < periodo + 1:
        return None
    variacao = serie.diff().dropna()
    if len(variacao) < periodo:
        return None
    ganhos = variacao.clip(lower=0.0)
    perdas = -variacao.clip(upper=0.0)
    media_ganho = ganhos.ewm(alpha=1.0 / periodo, adjust=False).mean().iloc[-1]
    media_perda = perdas.ewm(alpha=1.0 / periodo, adjust=False).mean().iloc[-1]
    if media_perda == 0:
        return 100.0 if media_ganho > 0 else None
    forca = media_ganho / media_perda
    return fontes.na_faixa(100.0 - (100.0 / (1.0 + forca)), 0.0, 100.0)


def _retorno_entre(serie, inicio, fim):
    """Retorno entre duas datas, usando o pregão anterior mais próximo."""
    try:
        preco_inicio = serie.asof(inicio)
        preco_fim = serie.asof(fim)
    except Exception:  # noqa: BLE001 — índice sem ordenação utilizável
        return None
    preco_inicio = fontes.positivo(preco_inicio)
    preco_fim = fontes.positivo(preco_fim)
    if preco_inicio is None or preco_fim is None:
        return None
    return preco_fim / preco_inicio - 1.0


class MotorMomentum:
    """Momentum 12M-1M cruzado com um indicador de risco.

    O veredito tem três graus, e a diferença entre eles é o que os motores de
    ranking consomem:

        favoravel    — segue normalmente
        rebaixar     — entra na lista, mas penalizado
        excluir      — sai da lista

    `excluir` exige queda profunda (abaixo de -20% em doze meses) ou a
    combinação de momentum negativo com RSI semanal em sobrevenda — que é a
    assinatura de faca caindo, não de barganha. Momentum que não pôde ser
    apurado **não exclui ninguém**: ausência de dado não é evidência de queda.
    """

    def __init__(self, fonte, selic_aa=None):
        self._fonte = fonte
        self._selic = selic_aa

    def _taxa_livre(self):
        if self._selic is not None:
            return self._selic / 100.0
        try:
            return float(taxas.obter_selic_meta()["valor"]) / 100.0
        except Exception:  # noqa: BLE001
            return 0.0

    def avaliar(self, ticker):
        """{'momentum_12m_1m', 'rsi_semanal', 'volatilidade_anual', 'sharpe',
        'veredito', 'motivo'} — cada campo None quando não apurado."""
        vazio = {"momentum_12m_1m": None, "rsi_semanal": None,
                 "volatilidade_anual": None, "sharpe": None,
                 "veredito": "nao_apurado",
                 "motivo": "Sem histórico de preços suficiente."}

        serie = self._fonte.precos(ticker, periodo="2y")
        if serie is None or len(serie) < 60:
            return vazio

        try:
            serie = serie.sort_index()
            fim_serie = serie.index[-1]
            fim_janela = fim_serie - pd.DateOffset(months=MESES_PULADOS)
            inicio_janela = fim_serie - pd.DateOffset(months=MESES_JANELA)
        except Exception as falha:  # noqa: BLE001
            registro.warning("momentum(%s): índice inesperado: %s", ticker, falha)
            return vazio

        momentum = _retorno_entre(serie, inicio_janela, fim_janela)

        # RSI em barra semanal: o diário oscila demais para dizer se a queda é
        # estrutural, que é exatamente a pergunta deste filtro.
        try:
            semanal = serie.resample("W-FRI").last().dropna()
        except Exception:  # noqa: BLE001
            semanal = None
        rsi = rsi_wilder(semanal) if semanal is not None else None

        retornos = serie.pct_change().dropna()
        volatilidade = None
        if len(retornos) >= 30:
            desvio = fontes.numero(retornos.std())
            if desvio is not None:
                volatilidade = desvio * (DIAS_POR_ANO ** 0.5)

        retorno_12m = _retorno_entre(serie, inicio_janela, fim_serie)
        sharpe = None
        if retorno_12m is not None and volatilidade:
            sharpe = fontes.na_faixa(
                (retorno_12m - self._taxa_livre()) / volatilidade, -20.0, 20.0)

        veredito, motivo = self._julgar(momentum, rsi)
        return {
            "momentum_12m_1m": momentum,
            "rsi_semanal": rsi,
            "volatilidade_anual": volatilidade,
            "sharpe": sharpe,
            "veredito": veredito,
            "motivo": motivo,
        }

    @staticmethod
    def _julgar(momentum, rsi):
        if momentum is None:
            return "nao_apurado", "Momentum 12M-1M não pôde ser calculado."
        if momentum <= QUEDA_ESTRUTURAL:
            return "excluir", (
                f"Queda estrutural: {momentum * 100:.1f}% em 12 meses "
                f"(corte em {QUEDA_ESTRUTURAL * 100:.0f}%).")
        if momentum < 0 and rsi is not None and rsi < RSI_SOBREVENDIDO:
            return "excluir", (
                f"Momentum negativo ({momentum * 100:.1f}%) com RSI semanal em "
                f"sobrevenda ({rsi:.0f}) — tendência de queda, não desconto.")
        if momentum < 0:
            return "rebaixar", f"Momentum negativo ({momentum * 100:.1f}%) em 12M-1M."
        return "favoravel", f"Momentum de {momentum * 100:.1f}% em 12M-1M."


# --------------------------------------------------------------------------
# Motor principal
# --------------------------------------------------------------------------

class PhilosophyEngine:
    """Gera rankings e sinais segundo Bogle, Barsi e Greenblatt.

        motor = PhilosophyEngine()
        motor.satelite_barsi()
        motor.satelite_greenblatt()
        motor.nucleo_bogle({"VOO": 12, "WRLD11.SA": 300})

    `fonte` e `fonte_sec` são injetáveis para teste: com dublês, toda a regra
    de negócio é verificável sem rede.
    """

    def __init__(self, fonte=None, fonte_sec=None, selic_aa=None):
        self.fonte = fonte or fontes.FonteYahoo()
        self.fonte_sec = fonte_sec
        self.momentum = MotorMomentum(self.fonte, selic_aa)

    # ---------------------------------------------------------------- Bogle
    def nucleo_bogle(self, posicoes, alvo=None, banda=BANDA_REBALANCEAMENTO):
        """Distância até o alvo e o ajuste que a fecha.

        `posicoes`: {ticker: quantidade}. `alvo`: {ticker: percentual}, somando
        100 — é **política de quem investe**, não sugestão do motor; o padrão
        existe só para a chamada sem argumento não quebrar.

        Tudo é convertido para reais antes de comparar. Somar posição em VOO
        com posição em WRLD11 sem passar pelo câmbio é o erro que faz a
        carteira parecer equilibrada enquanto o dólar mexe o peso real.

        A banda de 5 pontos é doutrina, não detalhe: rebalancear a cada
        oscilação gera corretagem e imposto sem melhorar risco.
        """
        alvo = alvo or dict(ALVO_BOGLE_PADRAO)
        soma_alvo = sum(fontes.numero(v) or 0.0 for v in alvo.values())
        if abs(soma_alvo - 100.0) > 0.01:
            return {"erro": f"Os pesos-alvo somam {soma_alvo:.2f}%, não 100%."}

        dolar = self._cotacao_dolar()
        linhas, patrimonio, ressalvas = [], 0.0, []

        for ticker, quantidade in (posicoes or {}).items():
            qtd = fontes.numero(quantidade)
            perfil = self.fonte.perfil(ticker) or {}
            preco = fontes.positivo(perfil.get("preco"))
            if qtd is None or preco is None:
                ressalvas.append({"ticker": ticker,
                                  "motivo": "Preço ou quantidade não apurados."})
                continue

            moeda = (perfil.get("moeda") or "").upper()
            if moeda in ("USD", ""):
                if dolar is None and moeda == "USD":
                    ressalvas.append({"ticker": ticker,
                                      "motivo": "Sem câmbio para converter USD."})
                    continue
                valor = preco * qtd * (dolar if moeda == "USD" else 1.0)
            else:
                valor = preco * qtd

            patrimonio += valor
            linhas.append({"ticker": ticker, "quantidade": qtd, "preco": preco,
                           "moeda": moeda or "BRL", "valor_brl": valor})

        if patrimonio <= 0:
            return {"erro": "Nenhuma posição pôde ser avaliada.",
                    "ressalvas": ressalvas}

        # Ativo do alvo que ainda não está na carteira aparece com zero: é
        # justamente o que mais precisa de aporte, e some se for ignorado.
        presentes = {linha["ticker"] for linha in linhas}
        for ticker in alvo:
            if ticker not in presentes:
                linhas.append({"ticker": ticker, "quantidade": 0.0,
                               "preco": None, "moeda": None, "valor_brl": 0.0})

        resultado = []
        for linha in linhas:
            peso = linha["valor_brl"] / patrimonio * 100.0
            peso_alvo = fontes.numero(alvo.get(linha["ticker"])) or 0.0
            desvio = peso - peso_alvo
            financeiro = (peso_alvo - peso) / 100.0 * patrimonio
            dentro = abs(desvio) <= banda * 100.0
            resultado.append({
                **linha,
                "peso_atual": peso,
                "peso_alvo": peso_alvo,
                "desvio_pp": desvio,
                "acao": "manter" if dentro else ("comprar" if financeiro > 0 else "vender"),
                "ajuste_brl": 0.0 if dentro else financeiro,
                "dentro_da_banda": dentro,
            })

        resultado.sort(key=lambda l: -abs(l["desvio_pp"]))
        fora = [l for l in resultado if not l["dentro_da_banda"]]
        return {
            "filosofia": "bogle",
            "patrimonio_brl": patrimonio,
            "dolar": dolar,
            "banda_pp": banda * 100.0,
            "precisa_rebalancear": bool(fora),
            "posicoes": resultado,
            "ressalvas": ressalvas,
            "observacao": ("O alvo é política de quem investe. O motor mede a "
                           "distância até ele, não escolhe o alvo."),
            **_carimbo(),
        }

    def _cotacao_dolar(self):
        perfil = self.fonte.perfil("BRL=X") or {}
        return fontes.positivo(perfil.get("preco"))

    # ---------------------------------------------------------------- Barsi
    def satelite_barsi(self, universo=None, aplicar_momentum=True):
        """Ranking BESST por preço teto e margem de segurança."""
        setores = universo or UNIVERSO_BESST
        aprovados, reprovados, ressalvas = [], [], []

        for setor, tickers in setores.items():
            for ticker in tickers:
                try:
                    linha = self._avaliar_barsi(ticker, setor, aplicar_momentum)
                except Exception as falha:  # noqa: BLE001
                    registro.exception("barsi(%s)", ticker)
                    ressalvas.append({"ticker": ticker, "motivo": str(falha)[:200]})
                    continue
                if linha is None:
                    ressalvas.append({"ticker": ticker,
                                      "motivo": "Sem preço ou sem balanço na CVM."})
                    continue
                (aprovados if linha["aprovado"] else reprovados).append(linha)

        aprovados.sort(key=lambda l: -(l["margem_seguranca"] or -1))
        reprovados.sort(key=lambda l: l["ticker"])
        return {
            "filosofia": "barsi",
            "universo": "BESST",
            "yield_alvo": YIELD_ALVO_BARSI * 100.0,
            "criterios": {
                "payout_pct": [PAYOUT_MINIMO, PAYOUT_MAXIMO],
                "divida_ebit_maxima": DIVIDA_EBITDA_MAXIMA,
                "margem_seguranca_minima_pct": MARGEM_SEGURANCA_MINIMA * 100.0,
                "anos_de_dpa": ANOS_DPA,
                "basileia_minima_bancos": BASILEIA_MINIMA,
            },
            "aprovados": aprovados,
            "reprovados": reprovados,
            "ressalvas": ressalvas,
            **_carimbo(),
        }

    def _avaliar_barsi(self, ticker, setor, aplicar_momentum):
        simbolo = f"{ticker}.SA"
        perfil = self.fonte.perfil(simbolo) or {}
        preco = fontes.positivo(perfil.get("preco"))
        if preco is None:
            return None

        dpa, anos_usados = self._dpa_projetado(simbolo)
        balanco = self._balanco_cvm(ticker)

        motivos_reprova, nao_apurados = [], []

        # Preço teto de Barsi: o preço em que o provento projetado entrega o
        # yield alvo. Sem DPA não há teto — e sem teto não há tese.
        teto = dpa / YIELD_ALVO_BARSI if dpa else None
        margem = (teto - preco) / teto if teto else None
        if teto is None:
            motivos_reprova.append("Sem histórico de proventos para projetar o DPA.")
        elif margem < MARGEM_SEGURANCA_MINIMA:
            motivos_reprova.append(
                f"Margem de segurança de {margem * 100:.1f}% — mínimo "
                f"{MARGEM_SEGURANCA_MINIMA * 100:.0f}%.")

        payout, motivo_payout, origem_payout = self._payout(
            dpa, balanco, ticker, perfil.get("valor_mercado"), preco)
        if payout is None:
            nao_apurados.append(f"payout ({motivo_payout})")
        elif not (PAYOUT_MINIMO <= payout <= PAYOUT_MAXIMO):
            motivos_reprova.append(
                f"Payout de {payout:.0f}% fora da faixa "
                f"{PAYOUT_MINIMO:.0f}–{PAYOUT_MAXIMO:.0f}%.")

        alavancagem, motivo_alavancagem = self._alavancagem(balanco, setor)
        if alavancagem is None:
            nao_apurados.append(motivo_alavancagem or "alavancagem")
        elif alavancagem > DIVIDA_EBITDA_MAXIMA:
            motivos_reprova.append(
                f"Dívida líquida/EBIT de {alavancagem:.1f}x — teto "
                f"{DIVIDA_EBITDA_MAXIMA:.1f}x.")

        lucros, anos_lucro = self._historico_de_lucro(ticker)
        if not anos_lucro:
            nao_apurados.append("constância de lucro")
        elif any(valor is not None and valor <= 0 for valor in lucros):
            motivos_reprova.append(
                f"Prejuízo em pelo menos um dos {anos_lucro} exercícios apurados.")

        momento = self.momentum.avaliar(simbolo) if aplicar_momentum else None
        if momento and momento["veredito"] == "excluir":
            motivos_reprova.append(momento["motivo"])

        # Cobertura: quantos dos três critérios de qualidade saíram do papel
        # como número, e não como ausência. Preço barato com qualidade não
        # medida não é aprovação — é falta de informação.
        medidos = sum(1 for valor in (payout, alavancagem, anos_lucro or None)
                      if valor is not None)
        if medidos < MINIMO_CRITERIOS:
            motivos_reprova.append(
                f"Apenas {medidos} de 3 critérios de qualidade puderam ser "
                f"medidos (mínimo {MINIMO_CRITERIOS}) — sem base para aprovar.")

        return {
            "ticker": ticker,
            "setor": setor,
            "nome": perfil.get("nome"),
            "preco": preco,
            "dpa_projetado": dpa,
            "anos_de_dpa": anos_usados,
            "preco_teto": teto,
            "margem_seguranca": margem,
            "yield_sobre_preco": (dpa / preco * 100.0) if dpa else None,
            "payout": payout,
            "origem_payout": origem_payout,
            "criterios_medidos": medidos,
            "divida_liquida_ebit": alavancagem,
            "exercicios_com_lucro": anos_lucro,
            "momentum": momento,
            "aprovado": not motivos_reprova,
            "motivos": motivos_reprova,
            "nao_apurados": nao_apurados,
        }

    def _dpa_projetado(self, simbolo):
        """Média do provento por ação dos últimos exercícios FECHADOS.

        O ano corrente fica de fora de propósito: em setembro ele tem só parte
        dos proventos, e incluí-lo derrubaria a média — produzindo um teto
        artificialmente baixo e um "caro" que é só calendário.
        """
        serie = self.fonte.dividendos(simbolo)
        if serie is None or not len(serie):
            return None, 0

        try:
            por_ano = serie.groupby(serie.index.year).sum()
        except Exception:  # noqa: BLE001
            return None, 0

        ano_corrente = datetime.now().year
        fechados = {ano: valor for ano, valor in por_ano.items() if ano < ano_corrente}
        if not fechados:
            return None, 0

        recentes = sorted(fechados)[-ANOS_DPA:]
        valores = [fontes.positivo(fechados[ano]) for ano in recentes]
        valores = [v for v in valores if v is not None]
        if not valores:
            return None, 0
        return sum(valores) / len(valores), len(valores)

    def _balanco_cvm(self, ticker):
        try:
            cnpj = cadastro_b3.cnpj_do_ticker(ticker)
            return fundamentos_cvm.balanco_por_cnpj(cnpj) if cnpj else None
        except Exception as falha:  # noqa: BLE001
            registro.warning("balanço CVM de %s: %s", ticker, falha)
            return None

    @staticmethod
    def _payout_por_lucro_total(dpa, balanco, valor_mercado, preco):
        """Payout pela via agregada: proventos totais sobre lucro total.

        Serve de plano B quando o LPA não vem — que é o caso dos bancos na
        base da CVM, justamente metade do universo BESST. Sem isto, banco não
        tem nenhum critério de qualidade mensurável.

        O número de ações sai de valor de mercado sobre preço. Isso é exato
        para companhia de classe única e **aproximado** para quem tem ON e PN:
        o valor de mercado cobre as duas classes, o preço é de uma só, e o
        provento pode diferir entre elas. Por isso a origem viaja junto — quem
        lê precisa saber que este número tem margem.
        """
        lucro = fontes.positivo(balanco.get("lucro_liquido"))
        mercado = fontes.positivo(valor_mercado)
        cotacao = fontes.positivo(preco)
        if not (dpa and lucro and mercado and cotacao):
            return None
        acoes = mercado / cotacao
        return fontes.na_faixa(dpa * acoes / lucro * 100.0, 0.0,
                               fontes.PAYOUT_MAXIMO)

    @staticmethod
    def _payout(dpa, balanco, ticker=None, valor_mercado=None, preco=None):
        """Payout em porcentagem, ou (None, motivo) quando não dá para saber.

        O ajuste de unit é o ponto: o provento do Yahoo é por unit, o `lpa_on`
        da CVM é por ação ordinária. Na TAEE11, que embala três ações, ignorar
        isso produz payout de 211% e reprova uma empresa que paga 70%.

        Ticker terminado em 11 fora da tabela vira "não apurado" em vez de
        conta errada: pode ser unit de composição desconhecida, e chutar o
        multiplicador é como o erro aconteceu na primeira vez.
        """
        if not dpa or not balanco:
            return None, "sem DPA ou sem balanço", None
        lpa = fontes.positivo(balanco.get("lpa_on"))
        if lpa is None:
            agregado = PhilosophyEngine._payout_por_lucro_total(
                dpa, balanco, valor_mercado, preco)
            if agregado is None:
                return None, "LPA não publicado e lucro/ações não fecharam", None
            if agregado > PAYOUT_IMPLAUSIVEL:
                return None, f"payout agregado de {agregado:.0f}% é implausível", None
            return agregado, None, "lucro_total"

        codigo = (ticker or "").upper().strip()
        if codigo.endswith("11"):
            acoes = ACOES_POR_UNIT.get(codigo)
            if acoes is None:
                return None, (f"{codigo} parece unit e não está em "
                              "ACOES_POR_UNIT — o payout sairia multiplicado "
                              "pelo número de ações da unit"), None
            lpa = lpa * acoes

        payout = fontes.na_faixa(dpa / lpa * 100.0, 0.0, fontes.PAYOUT_MAXIMO)
        if payout is None:
            return None, "payout fora de qualquer faixa plausível", None
        if payout > PAYOUT_IMPLAUSIVEL:
            return None, (f"payout de {payout:.0f}% é implausível — o "
                          "denominador provavelmente está errado"), None
        return payout, None, "lpa"

    @staticmethod
    def _alavancagem(balanco, setor):
        """Dívida líquida sobre EBIT, ou o motivo de não haver número.

        Duas limitações declaradas, em vez de escondidas:

        1. É **EBIT**, não EBITDA. A DFP coletada não traz depreciação
           separada, então o denominador é menor que o EBITDA de verdade e o
           múltiplo sai **mais conservador** — reprova antes, nunca depois.
        2. Banco e seguradora não entram. Para eles o critério é o índice de
           Basileia, que a base da CVM não publica; marcá-los como "não
           apurado" é honesto, inventar um número a partir do balanço geral
           não seria.
        """
        if setor in SETORES_FINANCEIROS:
            return None, ("Basileia não apurada (a base da CVM não publica o "
                          "índice; dívida/EBIT não se aplica a instituição "
                          "financeira)")
        if not balanco:
            return None, "sem balanço"

        ebit = fontes.positivo(balanco.get("ebit"))
        if ebit is None:
            return None, "EBIT não apurado"

        curto = fontes.numero(balanco.get("divida_curto_prazo")) or 0.0
        longo = fontes.numero(balanco.get("divida_longo_prazo")) or 0.0
        caixa = fontes.numero(balanco.get("caixa")) or 0.0
        if curto == 0.0 and longo == 0.0:
            return None, "dívida não apurada"

        # Caixa líquido: empresa sem dívida líquida tem alavancagem zero, não
        # negativa — número negativo aqui confundiria a leitura do ranking.
        liquida = max(0.0, curto + longo - caixa)
        return fontes.na_faixa(liquida / ebit, 0.0, 100.0), None

    def _historico_de_lucro(self, ticker):
        """Lucro por exercício, do mais antigo ao mais recente.

        A base cobre três exercícios. O critério clássico pede cinco; declarar
        quantos foram de fato verificados é o que permite a quem lê saber o
        peso do "passou".
        """
        try:
            cnpj = cadastro_b3.cnpj_do_ticker(ticker)
        except Exception:  # noqa: BLE001
            return [], 0
        if not cnpj:
            return [], 0

        try:
            linhas = fundamentos_cvm.historico_por_cnpj(cnpj)
        except Exception:  # noqa: BLE001
            return [], 0

        lucros = [fontes.numero(linha.get("lucro_liquido")) for linha in linhas]
        return lucros, len([v for v in lucros if v is not None])

    # ----------------------------------------------------------- Greenblatt
    def satelite_greenblatt(self, universo=None, top=TOP_GREENBLATT,
                            aplicar_momentum=True):
        """Magic Formula com trava de Shareholder Yield.

        O rendimento ao acionista substitui o dividend yield puro porque, para
        o investidor brasileiro, dividendo americano sofre retenção de 30% na
        fonte e recompra não sofre nada. Duas empresas com o mesmo retorno de
        caixa entregam líquidos diferentes — ranquear pelo bruto premia a que
        devolve da forma mais cara.
        """
        tickers = universo or UNIVERSO_EUA
        candidatos, descartados, ressalvas = [], [], []

        for ticker in tickers:
            try:
                linha = self._avaliar_greenblatt(ticker)
            except Exception as falha:  # noqa: BLE001
                registro.exception("greenblatt(%s)", ticker)
                ressalvas.append({"ticker": ticker, "motivo": str(falha)[:200]})
                continue
            if linha is None:
                ressalvas.append({"ticker": ticker, "motivo": "Dados insuficientes."})
                continue
            (candidatos if linha["elegivel"] else descartados).append(linha)

        # Os postos são calculados só entre os elegíveis: incluir descartado na
        # ordenação empurraria os bons para trás por causa de quem nem disputa.
        _ranquear(candidatos, "roic", maior_melhor=True, campo="posto_roic")
        _ranquear(candidatos, "ev_ebit", maior_melhor=False, campo="posto_ev_ebit")

        for linha in candidatos:
            linha["posto_combinado"] = linha["posto_roic"] + linha["posto_ev_ebit"]

        if aplicar_momentum:
            for linha in candidatos:
                momento = self.momentum.avaliar(linha["ticker"])
                linha["momentum"] = momento
                # Rebaixar é somar postos: o ativo cai na lista em vez de sumir
                # dela, que é o tratamento certo para sinal fraco mas não fatal.
                if momento["veredito"] == "rebaixar":
                    linha["posto_combinado"] += len(candidatos)
                    linha["penalizado_por_momentum"] = True
            excluidos = [l for l in candidatos if l["momentum"]["veredito"] == "excluir"]
            candidatos = [l for l in candidatos if l["momentum"]["veredito"] != "excluir"]
            descartados.extend(excluidos)

        candidatos.sort(key=lambda l: l["posto_combinado"])
        return {
            "filosofia": "greenblatt",
            "criterios": {
                "shareholder_yield_minimo_pct": SHAREHOLDER_YIELD_MINIMO,
                "setores_excluidos": sorted(SETORES_EXCLUIDOS_EUA),
                "top": top,
            },
            "ranking": candidatos[:top],
            "descartados": descartados,
            "ressalvas": ressalvas,
            "avaliados": len(tickers),
            **_carimbo(),
        }

    def _avaliar_greenblatt(self, ticker):
        perfil = self.fonte.perfil(ticker) or {}
        preco = fontes.positivo(perfil.get("preco"))
        valor_mercado = fontes.positivo(perfil.get("valor_mercado"))
        setor = perfil.get("setor")
        if preco is None or valor_mercado is None:
            return None

        motivos = []
        if setor in SETORES_EXCLUIDOS_EUA:
            motivos.append(f"Setor excluído pela Magic Formula: {setor}.")

        contabil = self._contabil_eua(ticker)
        ebit = fontes.numero(contabil.get("ebit"))
        caixa = fontes.numero(contabil.get("caixa")) or 0.0
        divida = fontes.numero(contabil.get("divida_total")) or 0.0
        circulante = fontes.numero(contabil.get("ativo_circulante"))
        passivo_circ = fontes.numero(contabil.get("passivo_circulante"))
        imobilizado = fontes.numero(contabil.get("imobilizado"))

        # EV/EBIT — prejuízo operacional não produz múltiplo com leitura: a
        # empresa não é "barata", ela não gera lucro operacional.
        ev = valor_mercado + divida - caixa
        ev_ebit = None
        if ebit is not None and ebit > 0 and ev > 0:
            ev_ebit = fontes.na_faixa(ev / ebit, 0.0, fontes.EV_EBIT_MAXIMO)
        if ev_ebit is None:
            motivos.append("EV/EBIT não apurado (EBIT ausente ou não positivo).")

        # ROIC de Greenblatt: EBIT sobre capital tangível empregado. O piso em
        # zero no capital de giro evita denominador negativo virar ROIC
        # gigante e positivo, que é como empresa alavancada lidera ranking sem
        # merecer.
        roic = None
        if (ebit is not None and ebit > 0 and circulante is not None
                and passivo_circ is not None and imobilizado is not None):
            capital = max(0.0, circulante - passivo_circ) + imobilizado
            if capital > 0:
                roic = fontes.na_faixa(ebit / capital * 100.0, 0.0, fontes.ROIC_MAXIMO)
        if roic is None:
            motivos.append("ROIC não apurado.")

        dividendos = fontes.numero(contabil.get("dividendos_pagos")) or 0.0
        recompras = fontes.numero(contabil.get("recompras")) or 0.0
        shareholder_yield = None
        if dividendos or recompras:
            shareholder_yield = fontes.na_faixa(
                (dividendos + recompras) / valor_mercado * 100.0,
                0.0, fontes.YIELD_MAXIMO)
        if shareholder_yield is None:
            motivos.append("Shareholder Yield não apurado.")
        elif shareholder_yield < SHAREHOLDER_YIELD_MINIMO:
            motivos.append(
                f"Shareholder Yield de {shareholder_yield:.1f}% — mínimo "
                f"{SHAREHOLDER_YIELD_MINIMO:.0f}%.")

        return {
            "ticker": ticker,
            "nome": perfil.get("nome"),
            "setor": setor,
            "preco": preco,
            "valor_mercado": valor_mercado,
            "ebit": ebit,
            "ev": ev,
            "ev_ebit": ev_ebit,
            "roic": roic,
            "dividendos_pagos": dividendos or None,
            "recompras": recompras or None,
            "shareholder_yield": shareholder_yield,
            "origem_contabil": contabil.get("origem"),
            "exercicio": contabil.get("exercicio"),
            "elegivel": not motivos,
            "motivos": motivos,
            "posto_roic": None,
            "posto_ev_ebit": None,
            "posto_combinado": None,
            "penalizado_por_momentum": False,
            "momentum": None,
        }

    def _contabil_eua(self, ticker):
        """SEC primeiro, Yahoo como plano B.

        A SEC é a fonte auditada, mas não cobre tudo (ADR estrangeiro não
        protocola 10-K). Cair para o Yahoo mantém a cobertura; o campo
        `origem` diz de onde veio cada linha, para quem quiser desconfiar da
        segunda.
        """
        if self.fonte_sec is not None:
            try:
                dados = self.fonte_sec.contabil(ticker)
                if dados and dados.get("ebit") is not None:
                    return dados
            except Exception as falha:  # noqa: BLE001
                registro.warning("SEC falhou para %s: %s", ticker, falha)
        return self.fonte.contabil(ticker) or {}


# --------------------------------------------------------------------------
# Auxiliares
# --------------------------------------------------------------------------

def _ranquear(linhas, chave, maior_melhor, campo):
    """Atribui posto 1..N. Quem não tem o indicador vai para o fim.

    Empate recebe o mesmo posto — dois ativos com o mesmo EV/EBIT não podem
    ser separados por ordem alfabética e chamar isso de critério.
    """
    com_valor = [l for l in linhas if fontes.numero(l.get(chave)) is not None]
    sem_valor = [l for l in linhas if fontes.numero(l.get(chave)) is None]
    com_valor.sort(key=lambda l: l[chave], reverse=maior_melhor)

    posto, anterior = 0, object()
    for indice, linha in enumerate(com_valor, start=1):
        if linha[chave] != anterior:
            posto = indice
            anterior = linha[chave]
        linha[campo] = posto
    for linha in sem_valor:
        linha[campo] = len(linhas) + 1


def _carimbo():
    agora = datetime.now()
    return {"gerado_em": agora.isoformat(timespec="seconds"),
            "gerado_em_legivel": agora.strftime("%d/%m/%Y %H:%M")}
