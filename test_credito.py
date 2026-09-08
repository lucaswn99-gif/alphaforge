"""Testes do motor de crédito e da renda fixa.

O que estes testes protegem: nenhum índice de crédito pode ser produzido por
modelo de linguagem, e dado ausente nunca pode virar número.

    python -m unittest test_credito -v
"""
import os
import sys
import types
import unittest

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
from modules import credit_engine, taxas  # noqa: E402
from routers import fixed_income  # noqa: E402


# Empresa saudável: giro positivo, alavancagem baixa, juros bem cobertos.
BALANCO_SAUDAVEL = {
    "ativo_circulante": 50_000.0,
    "passivo_circulante": 30_000.0,
    "ativo_total": 200_000.0,
    "lucros_retidos": 40_000.0,
    "ebitda": 45_000.0,
    "patrimonio_liquido": 120_000.0,
    "passivo_total": 80_000.0,
}


class TestAltmanZ(unittest.TestCase):
    def test_calculo_confere_com_a_formula(self):
        z = credit_engine.calcular_altman_z_score_emergente(**BALANCO_SAUDAVEL)
        x1 = (50_000 - 30_000) / 200_000
        x2 = 40_000 / 200_000
        x3 = 45_000 / 200_000
        x4 = 120_000 / 80_000
        esperado = round(6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4, 2)
        self.assertEqual(z["z_score"], esperado)
        self.assertIn("Segura", z["classificacao"])

    def test_ativo_total_ausente_nao_produz_z(self):
        """Z calculado com denominador zerado não é conservador, é inventado."""
        dados = dict(BALANCO_SAUDAVEL, ativo_total=None)
        z = credit_engine.calcular_altman_z_score_emergente(**dados)
        self.assertIsNone(z["z_score"])
        self.assertIn("ativo_total", z["campos_faltantes"])
        self.assertEqual(z["classificacao"], "Não calculável")

    def test_ativo_total_zero_tambem_barra(self):
        dados = dict(BALANCO_SAUDAVEL, ativo_total=0)
        self.assertIsNone(credit_engine.calcular_altman_z_score_emergente(**dados)["z_score"])

    def test_lucros_retidos_ausentes_entram_como_zero_e_ficam_registrados(self):
        dados = dict(BALANCO_SAUDAVEL, lucros_retidos=None)
        z = credit_engine.calcular_altman_z_score_emergente(**dados)
        self.assertIsNotNone(z["z_score"])
        self.assertTrue(z["lucros_retidos_assumidos_zero"])
        self.assertEqual(z["componentes"]["x2_lucros_retidos"], 0.0)

    def test_faixas_de_classificacao(self):
        estresse = credit_engine.calcular_altman_z_score_emergente(
            ativo_circulante=1, passivo_circulante=90_000, ativo_total=100_000,
            lucros_retidos=-50_000, ebitda=100, patrimonio_liquido=1_000,
            passivo_total=99_000)
        self.assertLess(estresse["z_score"], 1.10)
        self.assertIn("Estresse", estresse["classificacao"])


class TestLaudoCorporativo(unittest.TestCase):
    def test_aprovado_quando_tudo_dentro_dos_criterios(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "TESTE SA", divida_liquida=60_000, ebitda=45_000,
            despesa_financeira_anual=-9_000, z_metrics=BALANCO_SAUDAVEL)
        self.assertTrue(laudo["status"].startswith("APROVADO"))
        self.assertEqual(laudo["alavancagem_dl_ebitda"], round(60_000 / 45_000, 2))
        self.assertEqual(laudo["cobertura_juros_icj"], round(45_000 / 9_000, 2))
        self.assertEqual(laudo["motivos_veto"], [])

    def test_despesa_negativa_nao_inverte_a_cobertura(self):
        """A DFP publica despesa financeira com sinal negativo."""
        negativa = credit_engine.auditar_credito_corporativo(
            "X", 60_000, 45_000, -9_000, BALANCO_SAUDAVEL)
        positiva = credit_engine.auditar_credito_corporativo(
            "X", 60_000, 45_000, 9_000, BALANCO_SAUDAVEL)
        self.assertEqual(negativa["cobertura_juros_icj"], positiva["cobertura_juros_icj"])
        self.assertGreater(negativa["cobertura_juros_icj"], 0)

    def test_alavancagem_alta_veta(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "ALAVANCADA", divida_liquida=200_000, ebitda=45_000,
            despesa_financeira_anual=9_000, z_metrics=BALANCO_SAUDAVEL)
        self.assertTrue(laudo["status"].startswith("REPROVADO"))
        self.assertTrue(any("Dívida Líquida/EBITDA" in v for v in laudo["motivos_veto"]))

    def test_cobertura_baixa_veta(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "APERTADA", divida_liquida=50_000, ebitda=45_000,
            despesa_financeira_anual=40_000, z_metrics=BALANCO_SAUDAVEL)
        self.assertTrue(any("Cobertura de Juros" in v for v in laudo["motivos_veto"]))

    def test_dado_faltando_e_inconclusivo_e_nunca_aprovado(self):
        """Ausência de veto não é evidência de solidez."""
        laudo = credit_engine.auditar_credito_corporativo(
            "SEM DADOS", divida_liquida=None, ebitda=None,
            despesa_financeira_anual=None,
            z_metrics=dict(BALANCO_SAUDAVEL, ativo_total=None))
        self.assertTrue(laudo["status"].startswith("INCONCLUSIVO"))
        self.assertIsNone(laudo["alavancagem_dl_ebitda"])
        self.assertIsNone(laudo["cobertura_juros_icj"])
        self.assertIsNone(laudo["altman_z_score"])
        self.assertIn("alavancagem", laudo["indices_indisponiveis"])

    def test_ebitda_negativo_veta(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "PREJUIZO", divida_liquida=10_000, ebitda=-5_000,
            despesa_financeira_anual=1_000,
            z_metrics=dict(BALANCO_SAUDAVEL, ebitda=-5_000))
        self.assertTrue(laudo["status"].startswith("REPROVADO"))
        self.assertTrue(any("EBITDA" in v for v in laudo["motivos_veto"]))

    def test_determinismo(self):
        a = credit_engine.auditar_credito_corporativo("X", 60_000, 45_000, 9_000, BALANCO_SAUDAVEL)
        b = credit_engine.auditar_credito_corporativo("X", 60_000, 45_000, 9_000, BALANCO_SAUDAVEL)
        self.assertEqual(a, b)


class TestLaudoBancario(unittest.TestCase):
    def test_basileia_abaixo_do_prudencial_veta(self):
        laudo = credit_engine.auditar_ativo_bancario("BANCO X", 9.5, 20.0, True)
        self.assertTrue(laudo["status"].startswith("REPROVADO"))

    def test_indicador_ausente_e_inconclusivo(self):
        laudo = credit_engine.auditar_ativo_bancario("BANCO Y", None, None, None)
        self.assertTrue(laudo["status"].startswith("INCONCLUSIVO"))
        self.assertEqual(laudo["historico_lucratividade"], "Não informado")


class TestNormalizacaoDaExtracao(unittest.TestCase):
    def test_escala_em_milhares_e_aplicada(self):
        campos, meta = fixed_income.normalizar_extracao(
            {"unidade": "milhares", "ativo_total": 1_500, "ebitda": 300})
        self.assertEqual(campos["ativo_total"], 1_500_000.0)
        self.assertEqual(meta["fator_aplicado"], 1_000.0)

    def test_indices_sao_invariantes_a_escala(self):
        """Escala errada não pode mudar veredito: os índices são razões."""
        bruto = {"ativo_total": 200, "ativo_circulante": 50, "passivo_total": 80,
                 "passivo_circulante": 30, "patrimonio_liquido": 120,
                 "lucros_retidos": 40, "ebitda": 45, "divida_bruta": 70,
                 "caixa_e_equivalentes": 10, "despesa_financeira": -9}

        def laudo_para(unidade):
            campos, meta = fixed_income.normalizar_extracao(dict(bruto, unidade=unidade))
            return credit_engine.auditar_credito_corporativo(
                "X", meta["divida_liquida"], campos["ebitda"], campos["despesa_financeira"],
                {k: campos[k] for k in ("ativo_circulante", "passivo_circulante", "ativo_total",
                                        "lucros_retidos", "ebitda", "patrimonio_liquido",
                                        "passivo_total")})

        em_unidades = laudo_para("unidades")
        em_milhares = laudo_para("milhares")
        self.assertEqual(em_unidades["altman_z_score"], em_milhares["altman_z_score"])
        self.assertEqual(em_unidades["alavancagem_dl_ebitda"], em_milhares["alavancagem_dl_ebitda"])
        self.assertEqual(em_unidades["status"], em_milhares["status"])

    def test_ebit_substitui_ebitda_e_fica_declarado(self):
        campos, meta = fixed_income.normalizar_extracao(
            {"unidade": "unidades", "ebitda": None, "ebit": 1_000})
        self.assertEqual(campos["ebitda"], 1_000.0)
        self.assertTrue(meta["ebitda_e_na_verdade_ebit"])

    def test_divida_liquida_desconta_caixa(self):
        _, meta = fixed_income.normalizar_extracao(
            {"unidade": "unidades", "divida_bruta": 100, "caixa_e_equivalentes": 30})
        self.assertEqual(meta["divida_liquida"], 70.0)

    def test_campo_nulo_permanece_nulo(self):
        campos, _ = fixed_income.normalizar_extracao({"unidade": "unidades", "ativo_total": None})
        self.assertIsNone(campos["ativo_total"])

    def test_texto_nao_numerico_vira_none(self):
        campos, _ = fixed_income.normalizar_extracao(
            {"unidade": "unidades", "ativo_total": "não informado"})
        self.assertIsNone(campos["ativo_total"])


class TestLaudoIndisponivel(unittest.TestCase):
    def test_indices_vem_nulos_e_nao_zerados(self):
        """0.0 na tela é lido como medição. Ausente tem que ser null."""
        laudo = fixed_income.laudo_indisponivel("ACME", "Documento ilegível")
        self.assertIsNone(laudo["alavancagem_dl_ebitda"])
        self.assertIsNone(laudo["altman_z_score"])
        self.assertTrue(laudo["status"].startswith("INCONCLUSIVO"))


class TestParecer(unittest.TestCase):
    def test_parecer_registra_o_que_faltou(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "X", None, None, None, dict(BALANCO_SAUDAVEL, ativo_total=None))
        texto = fixed_income.montar_parecer(laudo, {"ebitda_e_na_verdade_ebit": False})
        self.assertIn("inconclusivo", texto.lower())
        self.assertIn("ativo_total", texto)

    def test_parecer_declara_uso_de_ebit(self):
        laudo = credit_engine.auditar_credito_corporativo(
            "X", 60_000, 45_000, 9_000, BALANCO_SAUDAVEL)
        texto = fixed_income.montar_parecer(laudo, {"ebitda_e_na_verdade_ebit": True})
        self.assertIn("EBIT", texto)


class TestSelic(unittest.TestCase):
    def setUp(self):
        self.original = taxas.requests.get
        taxas._cache.update({"valor": None, "data": None, "carimbo": 0.0})

    def tearDown(self):
        taxas.requests.get = self.original
        taxas._cache.update({"valor": None, "data": None, "carimbo": 0.0})

    def _resposta(self, corpo, status=200):
        class R:
            status_code = status

            def json(self_inner):
                return corpo

        return lambda *a, **k: R()

    def test_le_do_bcb(self):
        taxas.requests.get = self._resposta([{"data": "16/09/2026", "valor": "14.00"}])
        selic = taxas.obter_selic_meta(forcar=True)
        self.assertEqual(selic["valor"], 14.0)
        self.assertEqual(selic["origem"], "bcb")

    def test_virgula_decimal_e_aceita(self):
        taxas.requests.get = self._resposta([{"data": "16/09/2026", "valor": "14,25"}])
        self.assertEqual(taxas.obter_selic_meta(forcar=True)["valor"], 14.25)

    def test_valor_implausivel_e_recusado(self):
        """Resposta corrompida não pode virar taxa livre de risco."""
        taxas.requests.get = self._resposta([{"data": "x", "valor": "9999"}])
        self.assertEqual(taxas.obter_selic_meta(forcar=True)["origem"], "fallback")

    def test_rede_fora_usa_cache_depois_fallback(self):
        taxas.requests.get = self._resposta([{"data": "16/09/2026", "valor": "14.00"}])
        taxas.obter_selic_meta(forcar=True)

        def explode(*a, **k):
            raise RuntimeError("sem rede")

        taxas.requests.get = explode
        self.assertEqual(taxas.obter_selic_meta(forcar=True)["origem"], "cache")

        taxas._cache.update({"valor": None, "data": None, "carimbo": 0.0})
        selic = taxas.obter_selic_meta(forcar=True)
        self.assertEqual(selic["origem"], "fallback")
        self.assertEqual(selic["valor"], taxas.SELIC_FALLBACK)


if __name__ == "__main__":
    unittest.main(verbosity=2)
