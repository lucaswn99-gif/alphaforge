"""Painel de ações americanas contra seus BDRs na B3.

A conta é simples e a armadilha é inteira no fator de paridade.

    Preço justo do BDR = (preço em Nova York × dólar) / BDRs por ação

Esse divisor não tem API gratuita, muda em desdobramento e em ajuste da
própria B3, e **errar nele fabrica arbitragem que não existe**: uma razão
trocada de 10 para 12 produz um "spread" de 20% que parece dinheiro na mesa e
é só uma constante velha no código.

Por isso o painel não confia na tabela. Ele calcula a razão **implícita** a
partir dos preços da tela e compara com a configurada. Divergência grande não
vira sinal de compra — vira aviso de que a tabela precisa ser conferida. E
quando não há razão configurada, a implícita arredondada para o valor comum
mais próximo assume, marcada como derivada.

A segunda honestidade é sobre o que o spread significa. BDR não é arbitragem
de varejo: não há como vender a descoberto com facilidade, os horários de
pregão só se sobrepõem em parte e muitos BDRs quase não negociam. Spread em
papel ilíquido costuma ser preço parado, não oportunidade — e é isso que o
campo `confiavel` mede.
"""

import logging
from datetime import datetime

import pandas as pd

from modules import fontes

registro = logging.getLogger(__name__)

TICKER_DOLAR = "BRL=X"

# Distância máxima entre a razão implícita e um número inteiro para que o
# arredondamento seja aceito. Spread real de BDR líquido fica abaixo de 2%.
TOLERANCIA_ARREDONDAMENTO = 0.02

# Tolerância entre a razão implícita e a configurada antes de desconfiar da
# tabela. Spread real de BDR líquido fica abaixo de 2%; 10% é folga generosa.
TOLERANCIA_RAZAO = 0.10

# Abaixo disto o "spread" provavelmente é preço parado, não distorção.
VOLUME_MINIMO_BDR = 5_000

# Aferida contra o preço de tela em 12/09/2026 por `conferir_bdr.py`. Dos
# vinte fatores que este arquivo trazia antes, DEZOITO estavam errados — o
# painel só não produziu arbitragem fantasma porque desconfia da própria
# tabela. Rode o script de novo depois de qualquer desdobramento.
VERIFICADO_EM = "2026-09-12"
BDRS_POR_ACAO = {
    "AAPL": ("AAPL34", 20),
    "AMZN": ("AMZO34", 20),
    "CSCO": ("CSCO34", 5),
    "DIS": ("DISB34", 15),
    "GOOGL": ("GOGL34", 12),
    "INTC": ("ITLC34", 6),
    "JNJ": ("JNJB34", 15),
    "JPM": ("JPMC34", 10),
    "KO": ("COCA34", 6),
    "META": ("M1TA34", 28),
    "MSFT": ("MSFT34", 24),
    "NFLX": ("NFLX34", 50),
    "NVDA": ("NVDC34", 48),
    "PEP": ("PEPB34", 15),
    "PFE": ("PFIZ34", 4),
    "PG": ("PGCO34", 14),
    "TSLA": ("TSLA34", 32),
    "V": ("VISA34", 20),
    "WMT": ("WALM34", 16),
    "XOM": ("EXXO34", 8),
}


class GlobalEquitiesPanel:
    """Ação em Nova York contra o BDR na B3, com a distorção entre as duas.

        painel = GlobalEquitiesPanel()
        tabela = painel.painel(["AAPL", "MSFT"])     # DataFrame
        dados  = painel.painel_json(["AAPL"])        # lista de dicionários

    `fonte` é injetável para teste.
    """

    def __init__(self, fonte=None, mapa=None):
        self.fonte = fonte or fontes.FonteYahoo()
        self.mapa = dict(mapa or BDRS_POR_ACAO)

    # ------------------------------------------------------------ auxiliares
    def _cotacao_e_variacao(self, ticker):
        """(preço, variação % do último pregão). Cada um pode vir None."""
        perfil = self.fonte.perfil(ticker) or {}
        preco = fontes.positivo(perfil.get("preco"))
        variacao = None
        serie = self.fonte.precos(ticker, periodo="5d")
        if serie is not None and len(serie) >= 2:
            anterior = fontes.positivo(serie.iloc[-2])
            ultimo = fontes.positivo(serie.iloc[-1])
            if anterior and ultimo:
                variacao = (ultimo / anterior - 1.0) * 100.0
            if preco is None:
                preco = ultimo
        return preco, variacao, perfil

    @staticmethod
    def _razao_mais_proxima(implicita):
        """A razão que o mercado está praticando, arredondada.

        Fator de BDR é um número inteiro (ou meio, nas razões fracionárias).
        Então a inferência é: se a implícita está a menos de 2% de um inteiro,
        é esse inteiro.

        A versão anterior escolhia de uma lista de razões "comuns" — e a lista
        estava incompleta. Com o MSFT34 a implícita deu 24,05 e a lista
        respondeu 25, porque 24 não estava nela. Lista curada de valores
        plausíveis tem o mesmo defeito da tabela que ela deveria corrigir:
        envelhece sem avisar.

        Os 2% são folga para o spread real, que em BDR líquido fica abaixo de
        2%. Implícita que não chega perto de inteiro nenhum vira None — pode
        ser preço parado, BDR sem negócio no dia, ou desdobramento em curso.
        """
        alvo = fontes.positivo(implicita)
        if alvo is None:
            return None

        # Meio-passo só abaixo de 1. Aceitá-lo em qualquer faixa anularia a
        # verificação: acima de 12,5 todo número está a menos de 2% de algum
        # "x,5", e a função passaria a aprovar qualquer implícita — inclusive
        # a de um BDR com preço parado, que é o caso que ela existe para pegar.
        candidatas = [round(alvo)]
        if alvo < 1.0:
            candidatas.append(0.5)

        for candidata in candidatas:
            if candidata <= 0:
                continue
            if abs(candidata - alvo) / candidata <= TOLERANCIA_ARREDONDAMENTO:
                return candidata
        return None

    def dolar(self):
        preco, variacao, _ = self._cotacao_e_variacao(TICKER_DOLAR)
        return preco, variacao

    # ---------------------------------------------------------------- painel
    def avaliar(self, ticker_eua, cambio=None, variacao_cambio=None):
        """Uma linha do painel. Nunca levanta; campos não apurados vêm None."""
        ticker_eua = (ticker_eua or "").upper().strip()
        registro_bdr = self.mapa.get(ticker_eua)
        ticker_bdr = registro_bdr[0] if registro_bdr else None
        razao_config = fontes.positivo(registro_bdr[1]) if registro_bdr else None

        linha = {
            "ativo": ticker_eua,
            "bdr": ticker_bdr,
            "nome": None,
            "preco_usd": None,
            "preco_brl": None,
            "var_pct_usd": None,
            "var_pct_brl": None,
            "dolar": cambio,
            "var_pct_dolar": variacao_cambio,
            "razao_configurada": razao_config,
            "razao_implicita": None,
            "razao_usada": None,
            "origem_razao": None,
            "preco_justo_brl": None,
            "spread_pct": None,
            "volume_bdr": None,
            "confiavel": False,
            "alertas": [],
        }

        if ticker_bdr is None:
            linha["alertas"].append("Sem BDR mapeado para este ativo.")
            return linha

        if cambio is None:
            cambio, variacao_cambio = self.dolar()
            linha["dolar"] = cambio
            linha["var_pct_dolar"] = variacao_cambio
        if cambio is None:
            linha["alertas"].append("Cotação do dólar não apurada.")
            return linha

        preco_usd, var_usd, perfil_eua = self._cotacao_e_variacao(ticker_eua)
        linha["preco_usd"] = preco_usd
        linha["var_pct_usd"] = var_usd
        linha["nome"] = perfil_eua.get("nome")
        if preco_usd is None:
            linha["alertas"].append(f"Preço de {ticker_eua} não apurado.")
            return linha

        simbolo_bdr = f"{ticker_bdr}.SA"
        preco_bdr, var_bdr, perfil_bdr = self._cotacao_e_variacao(simbolo_bdr)
        linha["preco_brl"] = preco_bdr
        linha["var_pct_brl"] = var_bdr
        linha["volume_bdr"] = fontes.numero(perfil_bdr.get("volume"))
        if preco_bdr is None:
            linha["alertas"].append(f"Preço de {ticker_bdr} não apurado.")
            return linha

        # A razão implícita é o que o mercado está praticando agora. Ela é o
        # juiz da tabela, não o contrário.
        implicita = (preco_usd * cambio) / preco_bdr
        linha["razao_implicita"] = implicita

        razao_usada, origem = None, None
        if razao_config:
            desvio = abs(implicita - razao_config) / razao_config
            if desvio <= TOLERANCIA_RAZAO:
                razao_usada, origem = razao_config, "tabela"
            else:
                sugerida = self._razao_mais_proxima(implicita)
                linha["alertas"].append(
                    f"A razão configurada ({razao_config:g}) não bate com a "
                    f"implícita ({implicita:.2f}). "
                    + (f"O mercado sugere {sugerida:g}. " if sugerida else "")
                    + f"Confira o fator na B3 — a tabela é de {VERIFICADO_EM}.")
        else:
            sugerida = self._razao_mais_proxima(implicita)
            if sugerida:
                razao_usada, origem = sugerida, "derivada"
                linha["alertas"].append(
                    f"Razão não configurada; usando {sugerida:g}, derivada do "
                    "preço de tela.")

        if razao_usada is None:
            linha["alertas"].append(
                "Sem fator de paridade confiável — spread não calculado.")
            return linha

        linha["razao_usada"] = razao_usada
        linha["origem_razao"] = origem
        justo = (preco_usd * cambio) / razao_usada
        linha["preco_justo_brl"] = justo
        linha["spread_pct"] = (preco_bdr / justo - 1.0) * 100.0

        volume = linha["volume_bdr"]
        if volume is not None and volume < VOLUME_MINIMO_BDR:
            linha["alertas"].append(
                f"BDR com volume médio de {volume:,.0f} — spread em papel "
                "ilíquido costuma ser preço parado, não distorção."
                .replace(",", "."))
        elif volume is None:
            linha["alertas"].append("Volume do BDR não apurado.")

        linha["confiavel"] = (origem == "tabela"
                              and volume is not None
                              and volume >= VOLUME_MINIMO_BDR)
        return linha

    def painel_json(self, tickers=None):
        """Lista de dicionários — o que a API devolve."""
        alvos = [t.upper().strip() for t in (tickers or list(self.mapa))]
        cambio, var_cambio = self.dolar()
        linhas = []
        for ticker in alvos:
            try:
                linhas.append(self.avaliar(ticker, cambio, var_cambio))
            except Exception as falha:  # noqa: BLE001
                registro.exception("painel BDR(%s)", ticker)
                linhas.append({"ativo": ticker, "bdr": self.mapa.get(ticker, (None,))[0],
                               "alertas": [f"Falha inesperada: {str(falha)[:180]}"],
                               "confiavel": False, "spread_pct": None})
        # Maior distorção primeiro, entre as confiáveis; o resto vai abaixo.
        linhas.sort(key=lambda l: (not l.get("confiavel"),
                                   -abs(l.get("spread_pct") or 0.0)))
        return linhas

    def painel(self, tickers=None):
        """DataFrame com as colunas pedidas pela mesa.

        `NaN` em vez de None nas colunas numéricas: é o que o pandas usa para
        ausência, e o que deixa `.dropna()` e as agregações funcionarem sem
        tratamento especial.
        """
        linhas = self.painel_json(tickers)
        tabela = pd.DataFrame([{
            "Ativo": l.get("ativo"),
            "BDR": l.get("bdr"),
            "Preço USD": l.get("preco_usd"),
            "Preço BRL": l.get("preco_brl"),
            "Var% USD": l.get("var_pct_usd"),
            "Var% BRL": l.get("var_pct_brl"),
            "Dólar Atual": l.get("dolar"),
            "Preço Justo BRL": l.get("preco_justo_brl"),
            "Spread %": l.get("spread_pct"),
            "Razão": l.get("razao_usada"),
            "Confiável": l.get("confiavel"),
            "Alertas": "; ".join(l.get("alertas") or []),
        } for l in linhas])

        numericas = ["Preço USD", "Preço BRL", "Var% USD", "Var% BRL",
                     "Dólar Atual", "Preço Justo BRL", "Spread %", "Razão"]
        for coluna in numericas:
            if coluna in tabela:
                tabela[coluna] = pd.to_numeric(tabela[coluna], errors="coerce")
        return tabela

    def sugerir_tabela(self, tickers=None):
        """Compara a tabela com o que o mercado pratica, ativo por ativo.

        Fator de paridade muda em desdobramento e não tem API gratuita. Em vez
        de confiar numa constante que envelhece em silêncio, esta rotina
        pergunta ao preço de tela — e devolve a tabela corrigida, pronta para
        colar em `BDRS_POR_ACAO`.

        Só sugere razão que exista na prática (`RAZOES_COMUNS`). Implícita que
        não chega perto de nenhuma delas vira aviso, não sugestão: pode ser
        preço parado, BDR sem negócio no dia, ou desdobramento em curso.
        """
        alvos = [t.upper().strip() for t in (tickers or list(self.mapa))]
        cambio, _ = self.dolar()
        if cambio is None:
            return {"erro": "Cotação do dólar não apurada.", "linhas": []}

        linhas = []
        for ticker in alvos:
            registro_bdr = self.mapa.get(ticker)
            if not registro_bdr:
                continue
            nome_bdr, configurada = registro_bdr[0], fontes.positivo(registro_bdr[1])
            try:
                preco_usd, _, _ = self._cotacao_e_variacao(ticker)
                preco_bdr, _, perfil = self._cotacao_e_variacao(f"{nome_bdr}.SA")
            except Exception as falha:  # noqa: BLE001
                linhas.append({"ativo": ticker, "bdr": nome_bdr,
                               "situacao": "erro", "detalhe": str(falha)[:120]})
                continue

            if not preco_usd or not preco_bdr:
                linhas.append({"ativo": ticker, "bdr": nome_bdr,
                               "situacao": "sem_preco",
                               "detalhe": "preço do ativo ou do BDR não apurado"})
                continue

            implicita = (preco_usd * cambio) / preco_bdr
            sugerida = self._razao_mais_proxima(implicita)
            if sugerida is None:
                situacao = "sem_razao_plausivel"
            elif configurada and abs(sugerida - configurada) < 1e-9:
                situacao = "confere"
            else:
                situacao = "corrigir"

            linhas.append({
                "ativo": ticker, "bdr": nome_bdr,
                "configurada": configurada, "implicita": implicita,
                "sugerida": sugerida, "situacao": situacao,
                "volume_bdr": fontes.numero(perfil.get("volume")),
            })

        corrigir = [l for l in linhas if l.get("situacao") == "corrigir"]
        return {
            "linhas": linhas,
            "conferem": sum(1 for l in linhas if l.get("situacao") == "confere"),
            "corrigir": len(corrigir),
            "dolar": cambio,
            "tabela_sugerida": {
                l["ativo"]: (l["bdr"], int(l["sugerida"]) if l["sugerida"]
                             and float(l["sugerida"]).is_integer() else l["sugerida"])
                for l in linhas if l.get("sugerida")
            },
        }

    def carimbo(self):
        agora = datetime.now()
        return {"gerado_em": agora.isoformat(timespec="seconds"),
                "gerado_em_legivel": agora.strftime("%d/%m/%Y %H:%M"),
                "tabela_de_razoes_verificada_em": VERIFICADO_EM,
                "aviso": ("Fator de paridade de BDR muda em desdobramento. O "
                          "painel compara a razão configurada com a implícita "
                          "e avisa quando divergem.")}
