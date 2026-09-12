"""Fontes de dado de mercado, com validação na entrada.

Todo número que entra no sistema passa por aqui, e passa por duas perguntas:
**existe?** e **é plausível?** São perguntas diferentes. Campo ausente vira
`None` — "não apurado". Campo presente mas absurdo (P/L de 4.000, yield de
300%, patrimônio negativo virando ROE astronômico) **também** vira `None`, e
não o número. A distinção importa: zero e "não sei" produzem decisões opostas
num ranking, e API gratuita devolve as duas coisas sem avisar qual é qual.

A classe `FonteMercado` existe para ser trocada. Em produção é o Yahoo; no
teste é um dublê com números fixos. Sem essa costura, testar a regra de
negócio exigiria internet — e um teste que depende do Yahoo estar de bom humor
não é teste, é sorte.
"""

import logging
import threading
import time

from modules import rede

registro = logging.getLogger(__name__)

CACHE_TTL = 900  # 15 min, igual ao scanner: preço com atraso de 15 min mesmo.

# Faixas de plausibilidade. Fora delas, o dado é descartado como corrompido.
PL_MAXIMO = 1000.0
PVP_MAXIMO = 100.0
ROE_MAXIMO = 200.0
YIELD_MAXIMO = 100.0      # DY acima de 100% é erro de fonte, não oportunidade.
PAYOUT_MAXIMO = 500.0     # Acima disso o lucro do denominador está errado.
EV_EBIT_MAXIMO = 500.0
ROIC_MAXIMO = 500.0


# --------------------------------------------------------------------------
# Saneamento
# --------------------------------------------------------------------------

def numero(valor):
    """Float utilizável, ou None. Cobre NaN, infinito, string e vazio."""
    try:
        convertido = float(valor)
    except (TypeError, ValueError):
        return None
    # NaN é o único valor diferente de si mesmo; infinito passa no teste de
    # igualdade mas estoura qualquer conta adiante.
    if convertido != convertido or convertido in (float("inf"), float("-inf")):
        return None
    return convertido


def positivo(valor):
    """Float estritamente maior que zero, ou None."""
    convertido = numero(valor)
    return convertido if convertido is not None and convertido > 0 else None


def na_faixa(valor, minimo=None, maximo=None):
    """O valor, se couber na faixa; None se faltar ou for absurdo."""
    convertido = numero(valor)
    if convertido is None:
        return None
    if minimo is not None and convertido < minimo:
        return None
    if maximo is not None and convertido > maximo:
        return None
    return convertido


# --------------------------------------------------------------------------
# Contrato
# --------------------------------------------------------------------------

class FonteMercado:
    """O que os motores precisam saber do mundo.

    Métodos devolvem None (ou estrutura vazia) quando a fonte falha. Nenhum
    deles levanta: quem varre trinta ativos não pode perder os vinte e nove
    que responderam por causa do que não respondeu.
    """

    def precos(self, ticker, periodo="2y"):
        """Série de fechamentos ajustados, indexada por data. None se falhar."""
        raise NotImplementedError

    def dividendos(self, ticker):
        """Série de proventos por ação, indexada pela data-com. None se falhar."""
        raise NotImplementedError

    def perfil(self, ticker):
        """{'preco', 'moeda', 'setor', 'industria', 'valor_mercado', 'volume',
        'nome'} — campos ausentes vêm None."""
        raise NotImplementedError

    def contabil(self, ticker):
        """{'ebit', 'divida_total', 'caixa', 'ativo_circulante',
        'passivo_circulante', 'imobilizado', 'dividendos_pagos',
        'recompras', 'exercicio'} — para o motor americano."""
        raise NotImplementedError


# --------------------------------------------------------------------------
# Yahoo
# --------------------------------------------------------------------------

class FonteYahoo(FonteMercado):
    """yfinance, com cache curto e tolerância a falha por ativo.

    O cache não é otimização de conforto: um ranking que consulta o mesmo
    ticker no filtro de momentum, no cálculo de teto e no perfil faria três
    viagens ao Yahoo por ativo, e é assim que se toma 429 no meio da varredura.
    """

    def __init__(self, ttl=CACHE_TTL):
        self._ttl = ttl
        self._cache = {}
        self._trava = threading.Lock()

    # -- cache ------------------------------------------------------------
    def _lembrar(self, chave, produzir):
        agora = time.time()
        with self._trava:
            guardado = self._cache.get(chave)
            if guardado and agora - guardado[0] < self._ttl:
                return guardado[1]
        valor = produzir()
        with self._trava:
            self._cache[chave] = (time.time(), valor)
        return valor

    def limpar_cache(self):
        with self._trava:
            self._cache.clear()

    def _papel(self, ticker):
        import yfinance as yf
        return yf.Ticker(ticker)

    # -- dados ------------------------------------------------------------
    def precos(self, ticker, periodo="2y"):
        def buscar():
            try:
                historico = self._papel(ticker).history(period=periodo,
                                                        auto_adjust=True)
            except Exception as falha:  # noqa: BLE001
                registro.warning("precos(%s): %s", ticker, falha)
                return None
            if historico is None or historico.empty or "Close" not in historico:
                registro.info("precos(%s): sem histórico no período %s", ticker, periodo)
                return None
            serie = historico["Close"].dropna()
            return serie if len(serie) else None

        return self._lembrar(("precos", ticker, periodo), buscar)

    def dividendos(self, ticker):
        def buscar():
            try:
                serie = self._papel(ticker).dividends
            except Exception as falha:  # noqa: BLE001
                registro.warning("dividendos(%s): %s", ticker, falha)
                return None
            if serie is None or not len(serie):
                return None
            return serie.dropna()

        return self._lembrar(("dividendos", ticker), buscar)

    def perfil(self, ticker):
        def buscar():
            vazio = {"preco": None, "moeda": None, "setor": None,
                     "industria": None, "valor_mercado": None,
                     "volume": None, "nome": None}
            try:
                bruto = self._papel(ticker).info or {}
            except Exception as falha:  # noqa: BLE001
                registro.warning("perfil(%s): %s", ticker, falha)
                return vazio

            preco = positivo(bruto.get("currentPrice")
                             or bruto.get("regularMarketPrice")
                             or bruto.get("previousClose"))
            return {
                "preco": preco,
                "moeda": bruto.get("currency"),
                "setor": bruto.get("sector"),
                "industria": bruto.get("industry"),
                "valor_mercado": positivo(bruto.get("marketCap")),
                "volume": numero(bruto.get("averageVolume")
                                 or bruto.get("regularMarketVolume")),
                "nome": bruto.get("longName") or bruto.get("shortName"),
            }

        return self._lembrar(("perfil", ticker), buscar)

    def contabil(self, ticker):
        """Balanço e fluxo de caixa pelo Yahoo.

        É o plano B do motor americano: o plano A é a SEC, que publica o número
        auditado. O Yahoo entra quando o EDGAR não tem a empresa (ADR, por
        exemplo) ou não respondeu.
        """
        def buscar():
            vazio = {"ebit": None, "divida_total": None, "caixa": None,
                     "ativo_circulante": None, "passivo_circulante": None,
                     "imobilizado": None, "dividendos_pagos": None,
                     "recompras": None, "exercicio": None, "origem": "yahoo"}
            try:
                papel = self._papel(ticker)
                resultado = papel.financials
                balanco = papel.balance_sheet
                caixa_fluxo = papel.cashflow
            except Exception as falha:  # noqa: BLE001
                registro.warning("contabil(%s): %s", ticker, falha)
                return vazio

            def primeiro(tabela, *nomes):
                if tabela is None or getattr(tabela, "empty", True):
                    return None
                for nome in nomes:
                    if nome in tabela.index:
                        linha = tabela.loc[nome].dropna()
                        if len(linha):
                            return numero(linha.iloc[0])
                return None

            exercicio = None
            for tabela in (resultado, balanco, caixa_fluxo):
                if tabela is not None and not getattr(tabela, "empty", True) and len(tabela.columns):
                    exercicio = getattr(tabela.columns[0], "year", None)
                    break

            # Recompra e dividendo saem negativos no fluxo de caixa (são
            # saídas). O sinal é invertido aqui para o consumidor somar em vez
            # de lembrar de subtrair.
            recompras = primeiro(caixa_fluxo, "Repurchase Of Capital Stock",
                                 "Repurchase Of Common Stock")
            dividendos = primeiro(caixa_fluxo, "Cash Dividends Paid",
                                  "Common Stock Dividend Paid")

            return {
                "ebit": primeiro(resultado, "EBIT", "Operating Income"),
                "divida_total": primeiro(balanco, "Total Debt"),
                "caixa": primeiro(balanco, "Cash And Cash Equivalents",
                                  "Cash Cash Equivalents And Short Term Investments"),
                "ativo_circulante": primeiro(balanco, "Current Assets"),
                "passivo_circulante": primeiro(balanco, "Current Liabilities"),
                # Sem alternativa para o imobilizado: "Net Tangible Assets" é
                # patrimônio tangível líquido, conceito diferente de PP&E, e
                # entraria no denominador do ROIC como se fosse a mesma coisa.
                # ROIC não apurado é melhor que ROIC errado.
                "imobilizado": primeiro(balanco, "Net PPE"),
                "dividendos_pagos": abs(dividendos) if dividendos is not None else None,
                "recompras": abs(recompras) if recompras is not None else None,
                "exercicio": exercicio,
                "origem": "yahoo",
            }

        return self._lembrar(("contabil", ticker), buscar)


# --------------------------------------------------------------------------
# SEC EDGAR
# --------------------------------------------------------------------------

class FonteSEC:
    """Balanço americano direto da fonte primária.

    A SEC exige `User-Agent` identificando quem chama — requisição sem ele é
    bloqueada, e é a causa mais comum de "o EDGAR não funciona". O limite
    publicado é de 10 requisições por segundo; aqui há uma pausa entre
    chamadas porque tomar bloqueio de IP da SEC custa horas, não segundos.

    Por que não usar só o Yahoo: o Yahoo renomeia linha de balanço entre
    versões e some com histórico sem avisar. O EDGAR publica o que foi
    protocolado, com o conceito XBRL estável e a data do documento.
    """

    URL_TICKERS = "https://www.sec.gov/files/company_tickers.json"
    URL_CONCEITO = ("https://data.sec.gov/api/xbrl/companyconcept/"
                    "CIK{cik}/us-gaap/{conceito}.json")

    PAUSA_ENTRE_CHAMADAS = 0.12

    def __init__(self, identificacao, ttl=6 * 3600):
        if not identificacao or "@" not in identificacao:
            raise ValueError(
                "A SEC exige identificação no User-Agent, no formato "
                "'Nome email@dominio'. Sem isso a API responde 403.")
        self._cabecalhos = {"User-Agent": identificacao,
                            "Accept-Encoding": "gzip, deflate"}
        self._ttl = ttl
        self._mapa_cik = None
        self._cache = {}
        self._trava = threading.Lock()
        self._ultima_chamada = 0.0

        import requests
        self._sessao = requests.Session()

    def _respeitar_limite(self):
        espera = self.PAUSA_ENTRE_CHAMADAS - (time.time() - self._ultima_chamada)
        if espera > 0:
            time.sleep(espera)
        self._ultima_chamada = time.time()

    def cik(self, ticker):
        """CIK com zeros à esquerda, ou None. O mapa inteiro vem numa chamada."""
        with self._trava:
            if self._mapa_cik is None:
                self._respeitar_limite()
                bruto = rede.obter_json(self.URL_TICKERS, cabecalhos=self._cabecalhos)
                if not isinstance(bruto, dict):
                    registro.warning("SEC: não consegui a lista de tickers")
                    self._mapa_cik = {}
                else:
                    self._mapa_cik = {
                        str(item.get("ticker", "")).upper():
                            str(item.get("cik_str", "")).zfill(10)
                        for item in bruto.values()
                        if isinstance(item, dict)
                    }
            return self._mapa_cik.get((ticker or "").upper())

    def conceito(self, ticker, nome_conceito):
        """Valor anual mais recente de um conceito XBRL. (valor, exercício).

        Prefere o formulário 10-K: o 10-Q traz trimestre, e somar trimestre com
        ano num mesmo ranking produz empresa que parece quatro vezes maior.
        """
        codigo = self.cik(ticker)
        if not codigo:
            return None, None

        chave = (ticker.upper(), nome_conceito)
        agora = time.time()
        with self._trava:
            guardado = self._cache.get(chave)
            if guardado and agora - guardado[0] < self._ttl:
                return guardado[1]

        self._respeitar_limite()
        corpo = rede.obter_json(
            self.URL_CONCEITO.format(cik=codigo, conceito=nome_conceito),
            cabecalhos=self._cabecalhos)

        resultado = (None, None)
        if isinstance(corpo, dict):
            registros = []
            for moeda, lista in (corpo.get("units") or {}).items():
                if moeda != "USD" or not isinstance(lista, list):
                    continue
                for item in lista:
                    if not isinstance(item, dict) or item.get("form") != "10-K":
                        continue
                    valor = numero(item.get("val"))
                    fim = item.get("end")
                    if valor is not None and fim:
                        registros.append((fim, valor, item.get("fy")))
            if registros:
                registros.sort(key=lambda r: r[0])
                _, valor, exercicio = registros[-1]
                resultado = (valor, exercicio)

        with self._trava:
            self._cache[chave] = (time.time(), resultado)
        return resultado

    def contabil(self, ticker):
        """Os mesmos campos de `FonteMercado.contabil`, vindos do EDGAR.

        Cada conceito tem mais de um nome possível no XBRL porque empresas
        diferentes etiquetam a mesma linha de jeitos diferentes — tenta-se na
        ordem e fica o primeiro que responder.
        """
        def buscar(*conceitos):
            for nome in conceitos:
                valor, exercicio = self.conceito(ticker, nome)
                if valor is not None:
                    return valor, exercicio
            return None, None

        ebit, exercicio = buscar("OperatingIncomeLoss")
        divida_cp, _ = buscar("LongTermDebtCurrent", "DebtCurrent")
        divida_lp, _ = buscar("LongTermDebtNoncurrent", "LongTermDebt")
        caixa, _ = buscar("CashAndCashEquivalentsAtCarryingValue")
        ativo_circ, _ = buscar("AssetsCurrent")
        passivo_circ, _ = buscar("LiabilitiesCurrent")
        imobilizado, _ = buscar("PropertyPlantAndEquipmentNet")
        dividendos, _ = buscar("PaymentsOfDividendsCommonStock", "PaymentsOfDividends")
        recompras, _ = buscar("PaymentsForRepurchaseOfCommonStock")

        divida = None
        if divida_cp is not None or divida_lp is not None:
            divida = (divida_cp or 0.0) + (divida_lp or 0.0)

        return {
            "ebit": ebit,
            "divida_total": divida,
            "caixa": caixa,
            "ativo_circulante": ativo_circ,
            "passivo_circulante": passivo_circ,
            "imobilizado": imobilizado,
            "dividendos_pagos": abs(dividendos) if dividendos is not None else None,
            "recompras": abs(recompras) if recompras is not None else None,
            "exercicio": exercicio,
            "origem": "sec",
        }
