"""Prova as regras dos motores Philosophy e BDR, sem tocar a rede.

Toda fonte é dublê. Isso não é conveniência: com Yahoo de verdade, um teste
que falha não diz se a regra quebrou ou se a API oscilou — e um teste que não
distingue as duas coisas não serve para nada.

O que está sob teste é o que se decidiu, não o que se calculou: que o momentum
pula mesmo o mês recente, que carteira em duas moedas é convertida antes de
comparar, que banco não é reprovado por uma dívida/EBIT que não se aplica a
ele, e que uma razão de BDR errada vira aviso em vez de arbitragem.
"""

if __name__ != "__main__":  # pragma: no cover
    import pytest

    pytest.skip("script de verificação; rode com: python test_filosofias.py",
                allow_module_level=True)

import logging
from datetime import datetime

import numpy as np
import pandas as pd

logging.disable(logging.WARNING)

from modules import (bdr, cadastro_b3, filosofias, fontes,  # noqa: E402
                     fundamentos_cvm)

falhas = []


def checar(rotulo, condicao, extra=""):
    if condicao:
        print(f"  ok   {rotulo}")
    else:
        print(f"  FALHA {rotulo} {extra}")
        falhas.append(rotulo)


def perto(a, b, tolerancia=1e-6):
    if a is None or b is None:
        return False
    return abs(a - b) <= tolerancia * max(1.0, abs(b))


# --------------------------------------------------------------------------
# Dublês
# --------------------------------------------------------------------------

def serie_de_precos(pontos, fim=None, ruido=0.012, semente=7):
    """Série diária entre marcos {meses_atras: preço}, com ruído.

    O ruído não é enfeite. Interpolação pura produz uma reta, e numa reta
    descendente o RSI vai a zero — o que faz o motor classificar qualquer
    queda como faca caindo. Preço real oscila; testar contra reta mediria o
    comportamento do motor num mundo que não existe.
    """
    fim = fim or pd.Timestamp(datetime.now().date())
    inicio = fim - pd.DateOffset(months=max(pontos) + 1)
    dias = pd.date_range(inicio, fim, freq="B")
    marcos = sorted(pontos.items(), reverse=True)
    datas = [fim - pd.DateOffset(months=m) for m, _ in marcos]
    valores = [v for _, v in marcos]
    serie_marcos = pd.Series(valores, index=pd.DatetimeIndex(datas)).sort_index()
    base = serie_marcos.reindex(dias.union(serie_marcos.index)).interpolate(
        method="time").reindex(dias).ffill().bfill()
    if not ruido:
        return base
    gerador = np.random.default_rng(semente)
    tremor = gerador.normal(0.0, ruido, len(base)).cumsum()
    # O ruído é centrado para não deslocar os marcos: a série continua
    # passando pelos preços que o teste declarou.
    tremor = tremor - np.linspace(tremor[0], tremor[-1], len(tremor))
    return base * (1.0 + tremor)


class FonteFalsa(fontes.FonteMercado):
    """Fonte com dados fixos. `explode` faz o ticker levantar exceção."""

    def __init__(self, precos=None, dividendos=None, perfis=None,
                 contabeis=None, explode=()):
        self._precos = precos or {}
        self._dividendos = dividendos or {}
        self._perfis = perfis or {}
        self._contabeis = contabeis or {}
        self._explode = set(explode)
        self.chamadas = []

    def _guardar(self, ticker):
        self.chamadas.append(ticker)
        if ticker in self._explode:
            raise RuntimeError(f"fonte caiu para {ticker}")

    def precos(self, ticker, periodo="2y"):
        self._guardar(ticker)
        return self._precos.get(ticker)

    def dividendos(self, ticker):
        self._guardar(ticker)
        return self._dividendos.get(ticker)

    def perfil(self, ticker):
        self._guardar(ticker)
        return self._perfis.get(ticker, {"preco": None, "moeda": None,
                                         "setor": None, "valor_mercado": None,
                                         "volume": None, "nome": None})

    def contabil(self, ticker):
        self._guardar(ticker)
        return self._contabeis.get(ticker, {})


def principal():
    # ======================================================================
    print("\n[saneamento de número]")
    checar("NaN vira None", fontes.numero(float("nan")) is None)
    checar("infinito vira None", fontes.numero(float("inf")) is None)
    checar("texto vira None", fontes.numero("abc") is None)
    checar("número passa", perto(fontes.numero("12.5"), 12.5))
    checar("negativo não é positivo", fontes.positivo(-3) is None)
    checar("yield absurdo é recusado",
           fontes.na_faixa(300.0, 0.0, fontes.YIELD_MAXIMO) is None)
    checar("yield plausível passa",
           perto(fontes.na_faixa(7.5, 0.0, fontes.YIELD_MAXIMO), 7.5))

    # ======================================================================
    print("\n[momentum 12M-1M]")
    # Subiu 30% até um mês atrás, depois desabou. O 12M-1M tem que enxergar o
    # +30% — é justamente o mês desabado que a métrica ignora.
    subiu_depois_caiu = serie_de_precos({13: 100, 12: 100, 1: 130, 0: 90})
    fonte = FonteFalsa(precos={"XPTO": subiu_depois_caiu})
    motor = filosofias.MotorMomentum(fonte, selic_aa=10.0)
    resultado = motor.avaliar("XPTO")
    checar("ignora o mês mais recente",
           resultado["momentum_12m_1m"] is not None
           and resultado["momentum_12m_1m"] > 0.25,
           resultado["momentum_12m_1m"])
    checar("veredito favorável", resultado["veredito"] == "favoravel",
           resultado["veredito"])

    caindo = serie_de_precos({13: 100, 12: 100, 1: 90, 0: 88})
    motor = filosofias.MotorMomentum(FonteFalsa(precos={"CAI": caindo}), selic_aa=10.0)
    r = motor.avaliar("CAI")
    checar("momentum negativo rebaixa", r["veredito"] == "rebaixar", r["veredito"])

    despencando = serie_de_precos({13: 100, 12: 100, 1: 70, 0: 68})
    motor = filosofias.MotorMomentum(FonteFalsa(precos={"CAI2": despencando}), selic_aa=10.0)
    r = motor.avaliar("CAI2")
    checar("queda estrutural exclui", r["veredito"] == "excluir", r["veredito"])
    checar("exclusão explica o motivo", "estrutural" in (r["motivo"] or "").lower())

    motor = filosofias.MotorMomentum(FonteFalsa(), selic_aa=10.0)
    r = motor.avaliar("SEMDADO")
    checar("sem preço não exclui ninguém", r["veredito"] == "nao_apurado",
           r["veredito"])

    checar("RSI de série curta é None", filosofias.rsi_wilder(pd.Series([1, 2, 3])) is None)
    subindo = pd.Series(np.linspace(10, 30, 60))
    checar("RSI de alta contínua é alto", (filosofias.rsi_wilder(subindo) or 0) > 90,
           filosofias.rsi_wilder(subindo))

    # ======================================================================
    print("\n[Bogle — núcleo]")
    fonte = FonteFalsa(perfis={
        "VOO": {"preco": 500.0, "moeda": "USD", "nome": "Vanguard S&P 500",
                "setor": None, "valor_mercado": None, "volume": None},
        "WRLD11.SA": {"preco": 100.0, "moeda": "BRL", "nome": "Wrld",
                      "setor": None, "valor_mercado": None, "volume": None},
        "BRL=X": {"preco": 5.0, "moeda": "BRL", "nome": "USDBRL",
                  "setor": None, "valor_mercado": None, "volume": None},
    })
    motor = filosofias.PhilosophyEngine(fonte=fonte, selic_aa=10.0)

    # 10 VOO = 5.000 USD = 25.000 BRL; 250 WRLD11 = 25.000 BRL. Meio a meio.
    carteira = motor.nucleo_bogle({"VOO": 10, "WRLD11.SA": 250},
                                  alvo={"VOO": 50.0, "WRLD11.SA": 50.0})
    checar("converte USD antes de comparar",
           perto(carteira["patrimonio_brl"], 50000.0), carteira.get("patrimonio_brl"))
    pesos = {l["ticker"]: l["peso_atual"] for l in carteira["posicoes"]}
    checar("pesos corretos", perto(pesos["VOO"], 50.0) and perto(pesos["WRLD11.SA"], 50.0),
           pesos)
    checar("no alvo, não rebalanceia", carteira["precisa_rebalancear"] is False)

    desbalanceada = motor.nucleo_bogle({"VOO": 16, "WRLD11.SA": 100},
                                       alvo={"VOO": 50.0, "WRLD11.SA": 50.0})
    checar("desvio grande pede rebalanceamento",
           desbalanceada["precisa_rebalancear"] is True)
    linha_voo = next(l for l in desbalanceada["posicoes"] if l["ticker"] == "VOO")
    checar("manda vender o que passou do alvo", linha_voo["acao"] == "vender",
           linha_voo["acao"])
    checar("ajuste é negativo na venda", (linha_voo["ajuste_brl"] or 0) < 0)

    dentro = motor.nucleo_bogle({"VOO": 10, "WRLD11.SA": 240},
                                alvo={"VOO": 50.0, "WRLD11.SA": 50.0})
    checar("desvio dentro da banda não move", dentro["precisa_rebalancear"] is False,
           [l["desvio_pp"] for l in dentro["posicoes"]])

    faltante = motor.nucleo_bogle({"VOO": 10},
                                  alvo={"VOO": 50.0, "WRLD11.SA": 50.0})
    tickers = {l["ticker"] for l in faltante["posicoes"]}
    checar("ativo do alvo ausente aparece", "WRLD11.SA" in tickers, tickers)
    linha_ausente = next(l for l in faltante["posicoes"] if l["ticker"] == "WRLD11.SA")
    checar("ausente é compra", linha_ausente["acao"] == "comprar", linha_ausente["acao"])

    erro = motor.nucleo_bogle({"VOO": 1}, alvo={"VOO": 60.0, "WRLD11.SA": 30.0})
    checar("alvo que não soma 100 é recusado", "erro" in erro, erro)

    com_furo = motor.nucleo_bogle({"VOO": 10, "FANTASMA": 5},
                                  alvo={"VOO": 100.0})
    checar("ativo sem preço vira ressalva, não queda",
           any(r["ticker"] == "FANTASMA" for r in com_furo["ressalvas"]),
           com_furo.get("ressalvas"))

    # ======================================================================
    print("\n[Barsi — BESST]")
    dividendos_bons = pd.Series(
        [1.0, 1.0, 1.1, 1.1, 1.2, 1.2, 9.9],
        index=pd.to_datetime(["2023-03-01", "2023-09-01", "2024-03-01",
                              "2024-09-01", "2025-03-01", "2025-09-01",
                              f"{datetime.now().year}-03-01"]))
    precos_bons = serie_de_precos({13: 20, 12: 20, 1: 26, 0: 26})
    fonte = FonteFalsa(
        perfis={"TAEE11.SA": {"preco": 30.0, "moeda": "BRL", "nome": "Taesa",
                              "setor": None, "valor_mercado": None, "volume": None}},
        dividendos={"TAEE11.SA": dividendos_bons},
        precos={"TAEE11.SA": precos_bons})
    motor = filosofias.PhilosophyEngine(fonte=fonte, selic_aa=10.0)

    # A base da CVM é real; o teste precisa de balanço fixo para medir a
    # regra, não o balanço da Taesa deste trimestre.
    balanco_bom = {"lpa_on": 1.0, "ebit": 1000.0, "divida_curto_prazo": 200.0,
                   "divida_longo_prazo": 300.0, "caixa": 100.0,
                   "lucro_liquido": 800.0, "ano": 2025}
    original_cnpj = cadastro_b3.cnpj_do_ticker
    original_balanco = fundamentos_cvm.balanco_por_cnpj
    original_historico = fundamentos_cvm.historico_por_cnpj
    cadastro_b3.cnpj_do_ticker = lambda t, c=None: "11111111000191"
    fundamentos_cvm.balanco_por_cnpj = lambda cnpj, banco=None: dict(balanco_bom)
    fundamentos_cvm.historico_por_cnpj = lambda cnpj, banco=None: [
        {"ano": a, "lucro_liquido": 700.0 + a} for a in (2023, 2024, 2025)]

    dpa, anos = motor._dpa_projetado("TAEE11.SA")
    checar("DPA ignora o ano corrente", perto(dpa, (2.0 + 2.2 + 2.4) / 3), dpa)
    checar("DPA usa três exercícios", anos == 3, anos)

    # Preço teto = DPA/6%. Com DPA 2,2 → teto 36,67; preço 30 → MS de 18%.
    linha = motor._avaliar_barsi("TAEE11", "energia", aplicar_momentum=True)
    checar("preço teto = DPA / 6%", perto(linha["preco_teto"], 2.2 / 0.06), linha["preco_teto"])
    checar("margem de segurança correta",
           perto(linha["margem_seguranca"], (2.2 / 0.06 - 30) / (2.2 / 0.06), 1e-3),
           linha["margem_seguranca"])

    caro = FonteFalsa(
        perfis={"EGIE3.SA": {"preco": 36.0, "moeda": "BRL", "nome": "Engie",
                             "setor": None, "valor_mercado": None, "volume": None}},
        dividendos={"EGIE3.SA": dividendos_bons},
        precos={"EGIE3.SA": precos_bons})
    linha_cara = filosofias.PhilosophyEngine(fonte=caro, selic_aa=10.0) \
        ._avaliar_barsi("EGIE3", "energia", aplicar_momentum=True)
    checar("margem abaixo de 10% reprova", linha_cara["aprovado"] is False)
    checar("reprova explica a margem",
           any("margem" in m.lower() for m in linha_cara["motivos"]),
           linha_cara["motivos"])

    checar("banco não é reprovado por dívida/EBIT",
           filosofias.PhilosophyEngine._alavancagem({"ebit": 100}, "bancos")[0] is None)
    checar("banco declara Basileia como não apurada",
           "Basileia" in (filosofias.PhilosophyEngine._alavancagem({}, "bancos")[1] or ""))

    alavancagem, _ = filosofias.PhilosophyEngine._alavancagem(
        {"ebit": 100.0, "divida_curto_prazo": 200.0,
         "divida_longo_prazo": 300.0, "caixa": 100.0}, "energia")
    checar("dívida líquida/EBIT calculado", perto(alavancagem, 4.0), alavancagem)
    sem_divida, _ = filosofias.PhilosophyEngine._alavancagem(
        {"ebit": 100.0, "divida_curto_prazo": 10.0, "divida_longo_prazo": 0.0,
         "caixa": 500.0}, "energia")
    checar("caixa líquido não vira alavancagem negativa", perto(sem_divida, 0.0),
           sem_divida)

    _payout = filosofias.PhilosophyEngine._payout
    checar("payout fora da faixa é detectado", _payout(8.0, {"lpa_on": 10.0})[0] == 80.0)
    checar("payout por LPA declara a origem", _payout(8.0, {"lpa_on": 10.0})[2] == "lpa")
    checar("payout sem LPA e sem agregado é None",
           _payout(2.0, {"lpa_on": None})[0] is None)

    # Banco na base da CVM não tem lpa_on. Sem a via agregada, metade do
    # universo BESST ficaria sem nenhum critério de qualidade mensurável.
    agregado, _, origem = _payout(
        1.7549, {"lpa_on": None, "lucro_liquido": 16_781_938_000},
        "BBAS3", valor_mercado=128e9, preco=22.49)
    checar("sem LPA, cai para proventos sobre lucro total",
           agregado is not None and 50 <= agregado <= 70, agregado)
    checar("origem do payout agregado é declarada", origem == "lucro_total", origem)

    # O provento vem por UNIT; o lpa_on da CVM vem por ação ordinária. Sem o
    # ajuste, a TAEE11 reprovava com payout de 211% — o real é 70%.
    bruto = _payout(3.2254, {"lpa_on": 1.5287}, "BBAS3")[0]
    ajustado, _, _ = _payout(3.2254, {"lpa_on": 1.5287}, "TAEE11")
    checar("unit sem ajuste daria payout absurdo", bruto is None or bruto > 200,
           bruto)
    checar("unit ajustada cai na faixa real", perto(ajustado, 70.33, 1e-3), ajustado)
    checar("unit ajustada passa no critério",
           filosofias.PAYOUT_MINIMO <= ajustado <= filosofias.PAYOUT_MAXIMO)
    valor, motivo, _ = _payout(3.0, {"lpa_on": 1.5}, "XPTO11")
    checar("unit desconhecida vira não apurado, não conta errada",
           valor is None and "ACOES_POR_UNIT" in (motivo or ""), motivo)
    valor, motivo, _ = _payout(2.0, {"lpa_on": 1.0}, "BBAS3")
    checar("payout implausível vira não apurado",
           valor is None and "implausível" in (motivo or ""), motivo)

    # Momentum de exclusão tem que reprovar mesmo com preço atraente.
    fonte_faca = FonteFalsa(
        perfis={"CMIG4.SA": {"preco": 10.0, "moeda": "BRL", "nome": "Cemig",
                             "setor": None, "valor_mercado": None, "volume": None}},
        dividendos={"CMIG4.SA": dividendos_bons},
        precos={"CMIG4.SA": serie_de_precos({13: 100, 12: 100, 1: 60, 0: 58})})
    faca = filosofias.PhilosophyEngine(fonte=fonte_faca, selic_aa=10.0) \
        ._avaliar_barsi("CMIG4", "energia", aplicar_momentum=True)
    checar("faca caindo é reprovada mesmo barata", faca["aprovado"] is False)
    checar("momentum aparece no motivo",
           any("estrutural" in m.lower() or "momentum" in m.lower()
               for m in faca["motivos"]), faca["motivos"])

    # TAEE11 é unit de 3 ações: o LPA por unit é 3 x o lpa_on da CVM.
    checar("payout usa o LPA por unit, não por ação",
           perto(linha["payout"], 2.2 / (1.0 * 3) * 100, 1e-3), linha["payout"])
    checar("papel bom é aprovado", linha["aprovado"] is True, linha["motivos"])

    # Preço barato com qualidade não medida não pode aprovar: foi como o
    # BBAS3 passou com dois de três critérios ausentes.
    sem_balanco = FonteFalsa(
        perfis={"XPTO3.SA": {"preco": 10.0, "moeda": "BRL", "nome": "Xpto",
                             "setor": None, "valor_mercado": None, "volume": None}},
        dividendos={"XPTO3.SA": dividendos_bons},
        precos={"XPTO3.SA": precos_bons})
    fundamentos_cvm.balanco_por_cnpj = lambda cnpj, banco=None: None
    fundamentos_cvm.historico_por_cnpj = lambda cnpj, banco=None: []
    magro = filosofias.PhilosophyEngine(fonte=sem_balanco, selic_aa=10.0) \
        ._avaliar_barsi("XPTO3", "energia", aplicar_momentum=True)
    checar("sem critérios medidos, não aprova nem barato",
           magro["aprovado"] is False, magro["motivos"])
    checar("reprova explica a falta de cobertura",
           any("critérios de qualidade" in m for m in magro["motivos"]),
           magro["motivos"])
    checar("conta quantos critérios saíram", magro["criterios_medidos"] == 0,
           magro["criterios_medidos"])
    fundamentos_cvm.balanco_por_cnpj = lambda cnpj, banco=None: dict(balanco_bom)
    fundamentos_cvm.historico_por_cnpj = lambda cnpj, banco=None: [
        {"ano": a, "lucro_liquido": 700.0 + a} for a in (2023, 2024, 2025)]

    universo_pequeno = {"energia": ["TAEE11"]}
    relatorio = filosofias.PhilosophyEngine(fonte=fonte, selic_aa=10.0) \
        .satelite_barsi(universo=universo_pequeno)
    checar("relatório traz aprovados", len(relatorio["aprovados"]) == 1,
           relatorio["aprovados"])
    checar("relatório declara os critérios", "payout_pct" in relatorio["criterios"])

    quebrada = FonteFalsa(explode={"SBSP3.SA"})
    resiliente = filosofias.PhilosophyEngine(fonte=quebrada, selic_aa=10.0) \
        .satelite_barsi(universo={"saneamento": ["SBSP3"]})
    checar("fonte que explode vira ressalva, não queda",
           len(resiliente["ressalvas"]) == 1, resiliente["ressalvas"])

    cadastro_b3.cnpj_do_ticker = original_cnpj
    fundamentos_cvm.balanco_por_cnpj = original_balanco
    fundamentos_cvm.historico_por_cnpj = original_historico

    # ======================================================================
    print("\n[Greenblatt — EUA]")
    def contabil(ebit, divida, caixa, circ, passivo, imob, div_pagos, recompras):
        return {"ebit": ebit, "divida_total": divida, "caixa": caixa,
                "ativo_circulante": circ, "passivo_circulante": passivo,
                "imobilizado": imob, "dividendos_pagos": div_pagos,
                "recompras": recompras, "exercicio": 2025, "origem": "teste"}

    def perfil(preco, mcap, setor="Technology"):
        return {"preco": preco, "moeda": "USD", "setor": setor,
                "valor_mercado": mcap, "volume": 1e7, "nome": "Teste"}

    subindo_serie = serie_de_precos({13: 100, 12: 100, 1: 120, 0: 121})
    fonte = FonteFalsa(
        perfis={"BOA": perfil(100.0, 1_000.0),
                "CARA": perfil(100.0, 10_000.0),
                "BANCO": perfil(100.0, 1_000.0, "Financial Services"),
                "POUCO": perfil(100.0, 1_000.0)},
        contabeis={
            # ROIC = 200/(100-50+150) = 100%; EV = 1000+200-100 = 1100; EV/EBIT 5,5
            "BOA": contabil(200.0, 200.0, 100.0, 100.0, 50.0, 150.0, 40.0, 30.0),
            "CARA": contabil(200.0, 200.0, 100.0, 100.0, 50.0, 150.0, 400.0, 300.0),
            "BANCO": contabil(200.0, 200.0, 100.0, 100.0, 50.0, 150.0, 40.0, 30.0),
            # SY = (10+5)/1000 = 1,5% → abaixo do mínimo
            "POUCO": contabil(200.0, 200.0, 100.0, 100.0, 50.0, 150.0, 10.0, 5.0),
        },
        precos={t: subindo_serie for t in ("BOA", "CARA", "BANCO", "POUCO")})
    motor = filosofias.PhilosophyEngine(fonte=fonte, selic_aa=10.0)

    linha = motor._avaliar_greenblatt("BOA")
    checar("ROIC de Greenblatt", perto(linha["roic"], 100.0), linha["roic"])
    checar("EV/EBIT", perto(linha["ev_ebit"], 5.5), linha["ev_ebit"])
    checar("shareholder yield = (dividendo+recompra)/mcap",
           perto(linha["shareholder_yield"], 7.0), linha["shareholder_yield"])
    checar("elegível", linha["elegivel"] is True, linha["motivos"])

    banco = motor._avaliar_greenblatt("BANCO")
    checar("financeira é excluída", banco["elegivel"] is False)
    checar("motivo cita o setor",
           any("setor" in m.lower() for m in banco["motivos"]), banco["motivos"])

    pouco = motor._avaliar_greenblatt("POUCO")
    checar("SY abaixo de 5% não é elegível", pouco["elegivel"] is False)

    ranking = motor.satelite_greenblatt(universo=["BOA", "CARA", "BANCO", "POUCO"])
    checar("só elegíveis no ranking", len(ranking["ranking"]) == 2,
           [l["ticker"] for l in ranking["ranking"]])
    checar("o mais barato lidera", ranking["ranking"][0]["ticker"] == "BOA",
           [l["ticker"] for l in ranking["ranking"]])
    checar("descartados são preservados", len(ranking["descartados"]) == 2)

    # Momentum negativo tem que rebaixar sem sumir; queda profunda tem que sumir.
    fonte._precos["CARA"] = serie_de_precos({13: 100, 12: 100, 1: 95, 0: 94})
    rank2 = motor.satelite_greenblatt(universo=["BOA", "CARA"])
    cara = next((l for l in rank2["ranking"] if l["ticker"] == "CARA"), None)
    checar("momentum fraco rebaixa mas mantém", cara is not None
           and cara["penalizado_por_momentum"] is True,
           cara["penalizado_por_momentum"] if cara else "sumiu")

    fonte._precos["CARA"] = serie_de_precos({13: 100, 12: 100, 1: 60, 0: 59})
    rank3 = motor.satelite_greenblatt(universo=["BOA", "CARA"])
    checar("queda estrutural sai do ranking",
           all(l["ticker"] != "CARA" for l in rank3["ranking"]),
           [l["ticker"] for l in rank3["ranking"]])

    # ======================================================================
    print("\n[BDR — paridade]")
    def perfil_bdr(preco, volume=50_000):
        return {"preco": preco, "moeda": "BRL", "setor": None,
                "valor_mercado": None, "volume": volume, "nome": "BDR"}

    serie_curta = pd.Series([99.0, 100.0],
                            index=pd.to_datetime(["2026-09-10", "2026-09-11"]))
    # AAPL 200 USD, dólar 5 → 1000 BRL por ação; razão 10 → justo 100.
    fonte = FonteFalsa(
        perfis={"AAPL": {"preco": 200.0, "moeda": "USD", "setor": None,
                         "valor_mercado": None, "volume": 1e7, "nome": "Apple"},
                "AAPL34.SA": perfil_bdr(100.0),
                "BRL=X": {"preco": 5.0, "moeda": "BRL", "setor": None,
                          "valor_mercado": None, "volume": None, "nome": "USDBRL"}},
        precos={"AAPL": serie_curta, "AAPL34.SA": serie_curta,
                "BRL=X": serie_curta})
    painel = bdr.GlobalEquitiesPanel(fonte=fonte, mapa={"AAPL": ("AAPL34", 10)})
    linha = painel.avaliar("AAPL")
    checar("preço justo correto", perto(linha["preco_justo_brl"], 100.0),
           linha["preco_justo_brl"])
    checar("spread zero quando na paridade", perto(linha["spread_pct"], 0.0, 1e-3),
           linha["spread_pct"])
    checar("marcado como confiável", linha["confiavel"] is True, linha["alertas"])

    fonte._perfis["AAPL34.SA"] = perfil_bdr(103.0)
    linha = painel.avaliar("AAPL")
    checar("spread de 3% detectado", perto(linha["spread_pct"], 3.0, 1e-2),
           linha["spread_pct"])

    # Razão errada na tabela: implícita é 10, tabela diz 12.
    painel_errado = bdr.GlobalEquitiesPanel(fonte=fonte, mapa={"AAPL": ("AAPL34", 12)})
    fonte._perfis["AAPL34.SA"] = perfil_bdr(100.0)
    linha = painel_errado.avaliar("AAPL")
    checar("razão errada não vira arbitragem", linha["spread_pct"] is None,
           linha["spread_pct"])
    checar("razão errada gera aviso",
           any("não bate" in a for a in linha["alertas"]), linha["alertas"])
    checar("aviso sugere a razão certa",
           any("sugere 10" in a for a in linha["alertas"]), linha["alertas"])

    painel_sem = bdr.GlobalEquitiesPanel(fonte=fonte, mapa={"AAPL": ("AAPL34", None)})
    linha = painel_sem.avaliar("AAPL")
    checar("sem razão configurada, deriva do mercado",
           linha["origem_razao"] == "derivada" and perto(linha["razao_usada"], 10),
           linha.get("razao_usada"))
    checar("derivada não é 'confiável'", linha["confiavel"] is False)

    fonte._perfis["AAPL34.SA"] = perfil_bdr(100.0, volume=100)
    linha = painel.avaliar("AAPL")
    checar("BDR ilíquido perde a confiança", linha["confiavel"] is False)
    checar("ilíquido é explicado",
           any("ilíquido" in a for a in linha["alertas"]), linha["alertas"])

    linha = painel.avaliar("ZZZZ")
    checar("ativo sem BDR mapeado é tratado", linha["bdr"] is None
           and linha["spread_pct"] is None)

    vazia = bdr.GlobalEquitiesPanel(fonte=FonteFalsa(), mapa={"AAPL": ("AAPL34", 10)})
    linha = vazia.avaliar("AAPL")
    checar("sem dólar não quebra", linha["spread_pct"] is None, linha["alertas"])

    explosiva = bdr.GlobalEquitiesPanel(
        fonte=FonteFalsa(explode={"AAPL"},
                         perfis={"BRL=X": {"preco": 5.0, "moeda": "BRL",
                                           "setor": None, "valor_mercado": None,
                                           "volume": None, "nome": "USDBRL"}}),
        mapa={"AAPL": ("AAPL34", 10)})
    linhas = explosiva.painel_json(["AAPL"])
    checar("exceção no painel vira linha com alerta",
           len(linhas) == 1 and linhas[0]["alertas"], linhas)

    # sugerir_tabela: a razão configurada errada tem que ser apontada.
    fonte._perfis["AAPL34.SA"] = perfil_bdr(100.0)
    sugestao = painel_errado.sugerir_tabela(["AAPL"])
    linha_sug = sugestao["linhas"][0]
    checar("sugestão detecta razão a corrigir", linha_sug["situacao"] == "corrigir",
           linha_sug["situacao"])
    checar("sugestão traz a razão certa", perto(linha_sug["sugerida"], 10),
           linha_sug["sugerida"])
    checar("tabela sugerida vem pronta para colar",
           sugestao["tabela_sugerida"]["AAPL"] == ("AAPL34", 10),
           sugestao["tabela_sugerida"])
    confere = painel.sugerir_tabela(["AAPL"])
    checar("razão correta é marcada como confere",
           confere["linhas"][0]["situacao"] == "confere", confere["linhas"][0])

    # A lista de "razões comuns" respondia 25 para uma implícita de 24,05.
    _razao = bdr.GlobalEquitiesPanel._razao_mais_proxima
    checar("24,05 infere 24, não 25", perto(_razao(24.05), 24), _razao(24.05))
    checar("27,92 infere 28, não 30", perto(_razao(27.92), 28), _razao(27.92))
    checar("15,99 infere 16, não 15", perto(_razao(15.99), 16), _razao(15.99))
    checar("razão fracionária é aceita", perto(_razao(0.502), 0.5), _razao(0.502))
    checar("implícita longe de inteiro vira None", _razao(7.4) is None, _razao(7.4))

    tabela = painel.painel(["AAPL"])
    esperadas = ["Ativo", "BDR", "Preço USD", "Preço BRL", "Var% USD", "Var% BRL",
                 "Dólar Atual", "Preço Justo BRL", "Spread %"]
    checar("DataFrame tem as colunas pedidas",
           all(c in tabela.columns for c in esperadas), list(tabela.columns))
    checar("colunas numéricas são numéricas",
           pd.api.types.is_numeric_dtype(tabela["Spread %"]), tabela.dtypes.to_dict())

    # ======================================================================
    print("\n[integração com a base da CVM]")
    checar("base de fundamentos disponível", fundamentos_cvm.base_disponivel())
    historico = fundamentos_cvm.historico_por_cnpj("00000000000191")
    checar("histórico por CNPJ devolve exercícios", len(historico) >= 1, len(historico))
    checar("CNPJ inexistente devolve vazio",
           fundamentos_cvm.historico_por_cnpj("99999999999999") == [])

    # ======================================================================
    print("\n[rotas — plano e corte]")
    import os as _os
    import tempfile as _tempfile

    from modules import contas as _contas

    _contas.CAMINHO_BANCO = _os.path.join(_tempfile.mkdtemp(), "contas_filo.db")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import conta as rota_conta
    from routers import filosofias as rota_filo

    # Motor de mentira: as rotas são o que está sob teste, não a varredura.
    class MotorFalso:
        def satelite_barsi(self):
            return {"filosofia": "barsi",
                    "aprovados": [{"ticker": f"A{i}"} for i in range(10)],
                    "reprovados": [{"ticker": f"R{i}"} for i in range(5)],
                    "ressalvas": [], "criterios": {}}

        def satelite_greenblatt(self, universo=None):
            return {"filosofia": "greenblatt",
                    "ranking": [{"ticker": f"G{i}"} for i in range(len(universo or []))],
                    "descartados": [{"ticker": "X"}], "ressalvas": [],
                    "avaliados": len(universo or [])}

        def nucleo_bogle(self, carteira, alvo=None, banda=0.05):
            return {"filosofia": "bogle", "recebido": carteira, "alvo": alvo,
                    "banda": banda}

    class PainelFalso:
        def painel_json(self, lista=None):
            return [{"ativo": f"T{i}", "spread_pct": float(i)} for i in range(12)]

        def carimbo(self):
            return {"gerado_em": "2026-09-12T10:00:00"}

    rota_filo.motor = lambda: MotorFalso()
    rota_filo.painel = lambda: PainelFalso()

    app = FastAPI()
    app.include_router(rota_conta.router)
    app.include_router(rota_filo.router)
    cliente = TestClient(app)

    r = cliente.get("/filosofias/barsi").json()
    checar("free vê 3 aprovados", len(r["aprovados"]) == 3, len(r["aprovados"]))
    checar("free não vê reprovados", r["reprovados"] == [], r["reprovados"])
    checar("free sabe quantos ficaram ocultos", r["reprovados_ocultos"] == 5)
    checar("resposta marcada como truncada", r.get("truncado") is True)

    r = cliente.get("/filosofias/greenblatt").json()
    checar("free vê 3 do ranking", len(r["ranking"]) == 3, len(r["ranking"]))
    checar("free roda o universo rápido",
           r["avaliados"] == rota_filo.UNIVERSO_EUA_RAPIDO, r["avaliados"])
    checar("completo=true não abre para free", r["universo_completo"] is False)

    r = cliente.get("/filosofias/bdr").json()
    checar("free acompanha 5 BDRs", len(r["linhas"]) == 5, len(r["linhas"]))

    r2 = cliente.get("/filosofias/barsi").json()
    checar("segunda chamada usa cache", r2.get("cache") is True, r2.get("cache"))
    r3 = cliente.get("/filosofias/barsi?forcar=true").json()
    checar("forcar fura o cache", r3.get("cache") is False, r3.get("cache"))

    r = cliente.get("/filosofias/bogle?posicoes=VOO").json()
    checar("posição malformada é recusada com motivo", "erro" in r, r)
    r = cliente.get("/filosofias/bogle?posicoes=VOO:abc").json()
    checar("quantidade não numérica é recusada", "erro" in r, r)
    r = cliente.get("/filosofias/bogle?posicoes=VOO:10,WRLD11.SA:300").json()
    checar("posições válidas chegam ao motor",
           r.get("recebido") == {"VOO": 10.0, "WRLD11.SA": 300.0}, r.get("recebido"))

    # As três chamadas malformadas acima não podem ter custado cota.
    codigos = [cliente.get("/filosofias/bogle?posicoes=VOO:1").status_code
               for _ in range(4)]
    checar("entrada inválida não consome cota",
           codigos[0] == 200, codigos)
    # Uma consulta válida já foi feita acima, então destas quatro só duas
    # cabem nas três do dia.
    checar("bogle tem cota de 3 por dia no free",
           codigos == [200, 200, 402, 402], codigos)
    corpo = cliente.get("/filosofias/bogle?posicoes=VOO:1").json()["detail"]
    checar("402 do bogle diz o limite", corpo["limite"] == 3, corpo)

    cliente.post("/conta/registrar", json={"email": "filo@teste.com",
                                           "senha": "senha-boa-123"})
    _contas.definir_plano(
        _contas.autenticar("filo@teste.com", "senha-boa-123")["id"], "premium")

    r = cliente.get("/filosofias/barsi?forcar=true").json()
    checar("premium vê todos os aprovados", len(r["aprovados"]) == 10,
           len(r["aprovados"]))
    checar("premium vê os reprovados", len(r["reprovados"]) == 5)
    checar("premium não vem truncado", "truncado" not in r)

    r = cliente.get("/filosofias/greenblatt?completo=true&forcar=true").json()
    checar("premium roda o universo completo",
           r["avaliados"] == len(filosofias.UNIVERSO_EUA), r["avaliados"])

    r = cliente.get("/filosofias/bdr?forcar=true").json()
    checar("premium vê o painel inteiro", len(r["linhas"]) == 12, len(r["linhas"]))

    r = cliente.get("/filosofias/universos").json()
    checar("rota de universos expõe os critérios", "criterios" in r and "besst" in r)
    checar("universos declara o total BESST", r["besst_total"] == 26, r["besst_total"])

    print()
    if falhas:
        print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
        raise SystemExit(1)
    print("Tudo passou.")


if __name__ == "__main__":
    principal()
