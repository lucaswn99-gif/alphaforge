"""Testes do motor multicritério: momentum, Bazin e Greenblatt.

Sem rede: as séries são construídas à mão, com números que dá para conferir de
cabeça. Onde há uma escolha de modelagem (EBIT no lugar de EBITDA, capital
empregado no lugar do ROIC original), o teste trava a escolha e o docstring diz
por quê — para ninguém "corrigir" no futuro achando que é bug.
"""
import os
import sys
import types
import unittest

import numpy as np
import pandas as pd

_yf = types.ModuleType("yfinance")
_yf.download = lambda *a, **k: pd.DataFrame()


class _Ticker:
    def __init__(self, *a, **k):
        self.info = {}

    def history(self, *a, **k):
        return pd.DataFrame()


_yf.Ticker = _Ticker
sys.modules.setdefault("yfinance", _yf)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import quant  # noqa: E402


def _serie(valores, inicio="2025-01-01"):
    return pd.Series(valores, index=pd.date_range(inicio, periods=len(valores), freq="B"),
                     dtype=float)


def _tendencia(n=260, base=10.0, passo=0.05):
    """Série em alta constante: EMA21 > EMA50 e preço acima das duas."""
    return _serie([base + passo * i for i in range(n)])


class TestMediasEIndicadores(unittest.TestCase):
    def test_ema_exige_a_janela_inteira(self):
        """EMA21 sobre 5 pregões não é EMA21 — é outra coisa com o mesmo nome,
        e mentiria sobre a tendência."""
        self.assertIsNone(quant.ema(_serie([1, 2, 3, 4, 5]), 21))
        self.assertIsNotNone(quant.ema(_tendencia(), 21))

    def test_ema_curta_acima_da_longa_em_alta(self):
        serie = _tendencia()
        self.assertGreater(quant.ema(serie, 21), quant.ema(serie, 50))

    def test_ema_curta_abaixo_da_longa_em_queda(self):
        serie = _tendencia(passo=-0.03, base=100.0)
        self.assertLess(quant.ema(serie, 21), quant.ema(serie, 50))

    def test_adtv_e_financeiro_e_nao_quantidade(self):
        """Um milhão de cotas a R$ 0,80 não é o mesmo mercado que a R$ 80."""
        preco = _serie([10.0] * 30)
        volume = _serie([1_000_000.0] * 30)
        self.assertAlmostEqual(quant.adtv(preco, volume), 10_000_000.0)

        barato = _serie([0.8] * 30)
        self.assertLess(quant.adtv(barato, volume), quant.ADTV_MINIMO)

    def test_adtv_sem_janela_completa_e_none(self):
        self.assertIsNone(quant.adtv(_serie([10.0] * 5), _serie([1e6] * 5)))


class TestCompressaoDeVolatilidade(unittest.TestCase):
    """Compressão é medida contra a própria história do papel. Um limiar
    absoluto marcaria sempre os mesmos papéis: a largura normal de uma blue
    chip e a de uma small cap são grandezas diferentes."""

    def _com_compressao(self):
        agitada = list(np.tile([10.0, 11.5, 9.5, 11.0, 9.0], 30))   # 150 pregões
        calma = [10.0 + 0.01 * (i % 3) for i in range(40)]           # apertando
        return _serie(agitada + calma)

    def test_reconhece_estreitamento(self):
        preco = self._com_compressao()
        volume = _serie([1e6] * (len(preco) - 5) + [2e6] * 5)   # volume subindo
        d = quant.compressao_volatilidade(preco, volume)
        self.assertLessEqual(d["percentil"], quant.PERCENTIL_COMPRESSAO)
        self.assertGreaterEqual(d["volume_relativo"], quant.VOLUME_RELATIVO_MINIMO)
        self.assertTrue(d["comprimido"])

    def test_estreitamento_sem_volume_nao_conta(self):
        """Compressão sem volume é marasmo, não preparação de rompimento."""
        preco = self._com_compressao()
        volume = _serie([1e6] * len(preco))
        d = quant.compressao_volatilidade(preco, volume)
        self.assertLessEqual(d["percentil"], quant.PERCENTIL_COMPRESSAO)
        self.assertFalse(d["comprimido"])

    def test_serie_curta_nao_afirma_compressao(self):
        d = quant.compressao_volatilidade(_serie([10.0] * 25), _serie([1e6] * 25))
        self.assertFalse(d["comprimido"])
        self.assertIsNone(d["percentil"])


class TestMomentum(unittest.TestCase):
    LIQUIDO = 3_000_000.0    # x preço 20+ => bem acima do piso de R$ 5 mi

    def test_setup_completo_aprova(self):
        preco = _tendencia()
        volume = _serie([self.LIQUIDO] * len(preco))
        d = quant.avaliar_momentum(preco, volume, ifr=58.0)
        self.assertTrue(d["aprovado"], d["criterios"])
        self.assertGreater(quant.score_momentum(d), 50)

    def test_ifr_esticado_reprova(self):
        """IFR 75 é entrada tardia: o filtro existe para não comprar o topo."""
        preco = _tendencia()
        volume = _serie([self.LIQUIDO] * len(preco))
        d = quant.avaliar_momentum(preco, volume, ifr=75.0)
        self.assertFalse(d["aprovado"])
        self.assertFalse(d["criterios"]["ifr_na_faixa"])

    def test_ifr_fraco_reprova(self):
        preco = _tendencia()
        d = quant.avaliar_momentum(preco, _serie([self.LIQUIDO] * len(preco)), ifr=41.0)
        self.assertFalse(d["criterios"]["ifr_na_faixa"])

    def test_iliquido_reprova_mesmo_com_tendencia_perfeita(self):
        preco = _tendencia()
        volume = _serie([1_000.0] * len(preco))
        d = quant.avaliar_momentum(preco, volume, ifr=58.0)
        self.assertFalse(d["aprovado"])
        self.assertFalse(d["criterios"]["liquidez_suficiente"])

    def test_dado_ausente_nao_vira_reprovacao_silenciosa(self):
        """Papel sem volume no arquivo do dia não pode ser dado como ilíquido:
        'não apurado' e 'reprovado' são coisas diferentes."""
        preco = _tendencia()
        d = quant.avaliar_momentum(preco, None, ifr=58.0)
        self.assertIsNone(d["criterios"]["liquidez_suficiente"])
        self.assertIn("liquidez_suficiente", d["criterios_nao_apurados"])
        self.assertFalse(d["aprovado"])

    def test_queda_reprova_a_ordem_das_medias(self):
        preco = _tendencia(passo=-0.03, base=100.0)
        volume = _serie([self.LIQUIDO] * len(preco))
        d = quant.avaliar_momentum(preco, volume, ifr=55.0)
        self.assertFalse(d["criterios"]["ema21_acima_ema50"])


class TestBazin(unittest.TestCase):
    def test_preco_teto_e_o_preco_que_rende_seis_por_cento(self):
        self.assertAlmostEqual(quant.preco_teto_bazin(3.00), 50.0)
        self.assertAlmostEqual(quant.preco_teto_bazin(1.20), 20.0)

    def test_quem_nao_paga_nao_tem_teto(self):
        """Teto zero seria lido como 'nunca comprar'. O correto é 'não se
        aplica' — e isso é None."""
        self.assertIsNone(quant.preco_teto_bazin(0.0))
        self.assertIsNone(quant.preco_teto_bazin(None))

    def test_margem_de_seguranca(self):
        self.assertAlmostEqual(quant.margem_de_seguranca(50.0, 40.0), 25.0)
        self.assertAlmostEqual(quant.margem_de_seguranca(50.0, 50.0), 0.0)
        self.assertLess(quant.margem_de_seguranca(50.0, 62.5), 0)   # acima do teto

    def test_payout_e_por_acao_dos_dois_lados(self):
        self.assertAlmostEqual(quant.payout(3.0, 5.0), 60.0)

    def test_prejuizo_nao_gera_payout(self):
        """LPA negativo produziria percentual negativo sem significado."""
        self.assertIsNone(quant.payout(3.0, -2.0))
        self.assertIsNone(quant.payout(3.0, 0.0))

    def test_divida_liquida_exige_caixa(self):
        """Dívida bruta chamada de líquida superestima a alavancagem."""
        self.assertAlmostEqual(quant.divida_liquida(10e9, 40e9, 15e9), 35e9)
        self.assertIsNone(quant.divida_liquida(10e9, 40e9, None))

    def test_caixa_liquido_nao_vira_alavancagem_negativa(self):
        """Empresa sem dívida não pode ganhar duas vezes na ordenação."""
        self.assertEqual(quant.dl_sobre_ebit(-20e9, 10e9), 0.0)

    def test_sem_ebit_positivo_nao_ha_alavancagem_apurada(self):
        self.assertIsNone(quant.dl_sobre_ebit(35e9, -1e9))
        self.assertIsNone(quant.dl_sobre_ebit(35e9, 0.0))

    def test_perfil_bazin_classico_aprova(self):
        d = quant.avaliar_bazin(preco=40.0, dpa_12m=3.0, dy_12m=7.5, lpa=5.0,
                                divida_liq=35e9, ebit=30e9)
        self.assertTrue(d["aprovado"], d["criterios"])
        self.assertAlmostEqual(d["preco_teto"], 50.0)
        self.assertAlmostEqual(d["margem_seguranca"], 25.0)
        self.assertAlmostEqual(d["payout"], 60.0)

    def test_payout_acima_de_oitenta_reprova(self):
        """Distribuir quase tudo não é generosidade: é dividendo sem lastro em
        reinvestimento, e costuma anteceder corte."""
        d = quant.avaliar_bazin(preco=40.0, dpa_12m=4.8, dy_12m=12.0, lpa=5.0,
                                divida_liq=10e9, ebit=30e9)
        self.assertFalse(d["criterios"]["payout_saudavel"])
        self.assertFalse(d["aprovado"])

    def test_alavancagem_alta_reprova_mesmo_com_yield_gordo(self):
        d = quant.avaliar_bazin(preco=40.0, dpa_12m=3.0, dy_12m=7.5, lpa=5.0,
                                divida_liq=120e9, ebit=30e9)
        self.assertFalse(d["criterios"]["alavancagem_ok"])

    def test_acima_do_teto_reprova(self):
        d = quant.avaliar_bazin(preco=70.0, dpa_12m=3.0, dy_12m=4.3, lpa=5.0,
                                divida_liq=10e9, ebit=30e9)
        self.assertFalse(d["criterios"]["abaixo_do_teto"])


class TestDividendos12m(unittest.TestCase):
    def test_janela_ancorada_no_presente(self):
        """Ancorar no último pagamento faria quem parou de pagar em 2023 exibir
        o yield de 2023 para sempre."""
        antigos = pd.Series([2.0], index=pd.to_datetime(["2020-05-01"]))
        self.assertEqual(quant.dividendos_12m(antigos), 0.0)

    def test_soma_os_doze_meses(self):
        hoje = pd.Timestamp.now(tz="UTC").tz_localize(None)
        datas = [hoje - pd.Timedelta(days=d) for d in (30, 120, 200, 400)]
        serie = pd.Series([1.0, 1.0, 1.0, 5.0], index=pd.to_datetime(datas))
        self.assertAlmostEqual(quant.dividendos_12m(serie), 3.0)

    def test_serie_vazia_e_none_e_nao_zero(self):
        """'Não sabemos' e 'não pagou' são coisas diferentes."""
        self.assertIsNone(quant.dividendos_12m(None))
        self.assertIsNone(quant.dividendos_12m(pd.Series([], dtype=float)))


class TestGreenblatt(unittest.TestCase):
    def test_acoes_implicitas_por_lucro_sobre_lpa(self):
        self.assertAlmostEqual(quant.acoes_implicitas(20e9, 5.0), 4e9)

    def test_enterprise_value_soma_divida(self):
        ev = quant.enterprise_value(preco=40.0, acoes=4e9, divida_liq=35e9)
        self.assertAlmostEqual(ev, 4e9 * 40 + 35e9)

    def test_ev_ebit(self):
        self.assertAlmostEqual(quant.ev_sobre_ebit(195e9, 30e9), 6.5)

    def test_ebit_nao_positivo_fica_fora_do_ranking(self):
        self.assertIsNone(quant.ev_sobre_ebit(195e9, -1e9))

    def test_capital_empregado_e_roic(self):
        capital = quant.capital_empregado(ativo_total=200e9, passivo_circulante=30e9)
        self.assertAlmostEqual(capital, 170e9)
        self.assertAlmostEqual(quant.roic(34e9, capital), 20.0)

    def test_ranking_combina_barato_e_rentavel(self):
        """O ponto da fórmula: nem o mais barato nem o mais rentável, mas a
        melhor combinação dos dois."""
        r = quant.ranking_greenblatt([
            {"ticker": "BARATO_RUIM", "ev_ebit": 2.0, "roic": 5.0},
            {"ticker": "EQUILIBRADO", "ev_ebit": 5.0, "roic": 35.0},
            {"ticker": "CARO_OTIMO", "ev_ebit": 25.0, "roic": 60.0},
            {"ticker": "MEDIANO", "ev_ebit": 9.0, "roic": 18.0},
        ])
        self.assertEqual(r[0]["ticker"], "EQUILIBRADO")
        self.assertEqual(r[0]["rank_greenblatt"], 1)

    def test_papel_sem_um_dos_lados_fica_de_fora(self):
        """Ranquear com metade ausente inventaria uma posição."""
        r = quant.ranking_greenblatt([
            {"ticker": "A", "ev_ebit": 5.0, "roic": 30.0},
            {"ticker": "B", "ev_ebit": None, "roic": 40.0},
            {"ticker": "C", "ev_ebit": 6.0, "roic": None},
        ])
        self.assertEqual([x["ticker"] for x in r], ["A"])

    def test_universo_vazio_nao_levanta(self):
        self.assertEqual(quant.ranking_greenblatt([]), [])


class TestScoreQuant(unittest.TestCase):
    def test_greenblatt_e_linear_na_posicao(self):
        self.assertEqual(quant.score_greenblatt(1, 100), 100.0)
        self.assertEqual(quant.score_greenblatt(100, 100), 0.0)
        self.assertAlmostEqual(quant.score_greenblatt(50, 99), 50.0, places=0)

    def test_composto_e_o_melhor_segmento_e_nao_a_media(self):
        """Média puniria o especialista. Um papel de dividendo nunca vai
        pontuar em momentum, e isso não o torna pior como papel de dividendo."""
        valor, segmento = quant.compor_score_quant(
            {"momentum": 12.0, "bazin": 88.0, "greenblatt": 40.0})
        self.assertEqual(valor, 88.0)
        self.assertEqual(segmento, "bazin")

    def test_composto_ignora_segmento_nao_apurado(self):
        valor, segmento = quant.compor_score_quant(
            {"momentum": None, "bazin": 61.0, "greenblatt": None})
        self.assertEqual((valor, segmento), (61.0, "bazin"))

    def test_sem_segmento_algum_devolve_none(self):
        self.assertEqual(quant.compor_score_quant(
            {"momentum": None, "bazin": None, "greenblatt": None}), (None, None))

    def test_score_sempre_entre_zero_e_cem(self):
        for ifr in (10.0, 45.0, 58.0, 72.0, 95.0):
            d = quant.avaliar_momentum(_tendencia(), _serie([3e6] * 260), ifr=ifr)
            s = quant.score_momentum(d)
            self.assertTrue(0.0 <= s <= 100.0, (ifr, s))


if __name__ == "__main__":
    unittest.main()


class TestExercicioAtipico(unittest.TestCase):
    """Regressão do caso VALE3, exercício 2025 — e da minha própria conclusão
    errada sobre ele.

    Os números são os da DFP: EBIT de R$ 31,969 bi, perda por não
    recuperabilidade de R$ 25,147 bi, lucro de R$ 11,811 bi sobre patrimônio de
    R$ 188,926 bi. O motor lia tudo CERTO — ROE de 6,25% e margem de 5,53% são
    aritmética exata sobre esses valores — e mesmo assim emitia VENDA com score
    25 sobre um P/L de 30,7x que era só denominador atípico.

    O defeito não era o dado. Era concluir venda a partir de um evento isolado.
    """

    EBIT = 31.969e9
    PERDAS = -25.147e9
    LUCRO = 11.811e9

    def test_impairment_material_marca_o_exercicio(self):
        d = quant.exercicio_contaminado(self.PERDAS, self.EBIT)
        self.assertTrue(d["contaminado"])
        self.assertAlmostEqual(d["proporcao_ebit"], 0.787, places=2)

    def test_perda_pequena_nao_marca(self):
        """Baixa rotineira não pode disparar o alerta: se tudo é atípico,
        nada é."""
        self.assertFalse(quant.exercicio_contaminado(-0.5e9, self.EBIT)["contaminado"])

    def test_sinal_da_perda_nao_importa(self):
        """A DFP publica a conta negativa; alguém pode gravá-la positiva."""
        a = quant.exercicio_contaminado(self.PERDAS, self.EBIT)
        b = quant.exercicio_contaminado(abs(self.PERDAS), self.EBIT)
        self.assertEqual(a["contaminado"], b["contaminado"])

    def test_sem_ebit_positivo_nao_afirma_nada(self):
        d = quant.exercicio_contaminado(self.PERDAS, -1e9)
        self.assertFalse(d["contaminado"])
        self.assertIsNone(d["proporcao_ebit"])

    def test_lucro_recorrente_e_estimativa_e_fica_acima_do_publicado(self):
        estimado = quant.lucro_recorrente(self.LUCRO, self.PERDAS)
        self.assertGreater(estimado, self.LUCRO)
        # 11,811 + 25,147 x (1 - 0,34) = 28,4 bi
        self.assertAlmostEqual(estimado / 1e9, 28.4, places=1)

    def test_sem_dado_nao_estima(self):
        self.assertIsNone(quant.lucro_recorrente(None, self.PERDAS))
        self.assertIsNone(quant.lucro_recorrente(self.LUCRO, None))
