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
from modules import credit_engine, credito_score, taxas  # noqa: E402


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


# Emissor de perfil aprovável, em R$ mil — o mesmo do botão "Exemplo" da tela.
EMISSOR_BOM = {
    "nome_emissor": "Companhia Exemplo S.A.",
    "ativo_total": 1_000_000.0, "ativo_circulante": 400_000.0, "caixa": 80_000.0,
    "passivo_circulante": 250_000.0, "divida_bruta": 300_000.0,
    "patrimonio_liquido": 450_000.0, "lucros_retidos": 200_000.0,
    "ebitda": 180_000.0, "despesa_financeira": 40_000.0,
}


class TestCalculadoraDeCredito(unittest.TestCase):
    """A aba de crédito virou calculadora de entrada manual: o emissor de CRI,
    CRA ou debênture não publica DFP, e o analista digita o prospecto.

    O que estes testes protegem: campo em branco NUNCA vira número, e o score
    sempre vem acompanhado do porquê de cada critério."""

    def test_emissor_solido_aprova_com_todos_os_criterios(self):
        r = credito_score.avaliar(EMISSOR_BOM)
        self.assertEqual(r["veredito"], "APROVADO")
        self.assertEqual(r["criterios_apurados"], 5)
        self.assertEqual(r["vetos"], [])
        self.assertGreaterEqual(r["score"], 75)

    def test_divida_liquida_desconta_o_caixa(self):
        """Dívida bruta chamada de líquida reprova emissor que tem caixa."""
        r = credito_score.avaliar(EMISSOR_BOM)
        self.assertAlmostEqual(r["calculados"]["divida_liquida"], 220_000.0)
        self.assertAlmostEqual(r["calculados"]["alavancagem"], round(220_000 / 180_000, 2))

    def test_sem_caixa_nao_ha_alavancagem(self):
        dados = dict(EMISSOR_BOM); dados["caixa"] = None
        r = credito_score.avaliar(dados)
        self.assertIsNone(r["calculados"]["alavancagem"])
        criterio = next(c for c in r["criterios"] if c["chave"] == "alavancagem")
        self.assertEqual(criterio["situacao"], "nao_apurado")

    def test_passivo_total_sai_por_identidade_contabil(self):
        """Ativo menos patrimônio é identidade, não estimativa."""
        r = credito_score.avaliar(EMISSOR_BOM)
        self.assertAlmostEqual(r["calculados"]["passivo_total"], 550_000.0)

    def test_alavancagem_acima_do_teto_veta(self):
        dados = dict(EMISSOR_BOM); dados["ebitda"] = 40_000.0
        r = credito_score.avaliar(dados)
        self.assertEqual(r["veredito"], "REPROVADO")
        self.assertTrue(any("Alavancagem" in v for v in r["vetos"]))

    def test_cobertura_baixa_veta(self):
        dados = dict(EMISSOR_BOM); dados["despesa_financeira"] = 150_000.0
        r = credito_score.avaliar(dados)
        self.assertEqual(r["veredito"], "REPROVADO")
        self.assertTrue(any("Cobertura" in v for v in r["vetos"]))

    def test_ebitda_negativo_veta(self):
        dados = dict(EMISSOR_BOM); dados["ebitda"] = -10_000.0
        r = credito_score.avaliar(dados)
        self.assertEqual(r["veredito"], "REPROVADO")
        self.assertTrue(any("EBITDA" in v for v in r["vetos"]))

    def test_poucos_criterios_e_inconclusivo_e_nunca_aprovado(self):
        """Ausência de veto não é aprovação: sem os dados que faltam, o laudo
        não afirma nada sobre o emissor."""
        r = credito_score.avaliar({"ebitda": 100_000.0, "divida_bruta": 50_000.0,
                                   "caixa": 20_000.0})
        self.assertEqual(r["veredito"], "INCONCLUSIVO")
        self.assertLessEqual(r["score"], 55)

    def test_todo_criterio_carrega_a_explicacao(self):
        """Score sem o porquê não serve para decidir nem para explicar depois."""
        for dados in (EMISSOR_BOM, {"ebitda": 1.0}):
            for c in credito_score.avaliar(dados)["criterios"]:
                self.assertTrue(c["explicacao"], c["rotulo"])
                self.assertIn(c["situacao"], ("aprovado", "reprovado", "nao_apurado"))
                self.assertTrue(c["referencia"])

    def test_score_e_normalizado_pela_cobertura(self):
        """Um critério não apurado reduz o denominador, não vira zero: o papel
        não pode ser punido por um dado que o analista não tinha."""
        parcial = dict(EMISSOR_BOM)
        parcial["ativo_circulante"] = None
        parcial["passivo_circulante"] = None
        r = credito_score.avaliar(parcial)
        self.assertLess(r["criterios_apurados"], 5)
        self.assertIsNotNone(r["score"])
        self.assertLessEqual(r["score"], 100)

    def test_entrada_vazia_nao_levanta(self):
        r = credito_score.avaliar({})
        self.assertEqual(r["veredito"], "INCONCLUSIVO")
        self.assertEqual(r["criterios_apurados"], 0)

    def test_texto_no_lugar_de_numero_vira_nao_apurado(self):
        dados = dict(EMISSOR_BOM); dados["ebitda"] = "n/d"
        r = credito_score.avaliar(dados)
        self.assertIsNone(r["calculados"]["alavancagem"])

    def test_escala_nao_muda_os_indices(self):
        """Digitar em mil ou em unidade tem que dar o mesmo score: os índices
        são razões, e razão é invariante a escala."""
        em_unidades = {k: (v * 1000 if isinstance(v, (int, float)) else v)
                       for k, v in EMISSOR_BOM.items()}
        a = credito_score.avaliar(EMISSOR_BOM)
        b = credito_score.avaliar(em_unidades)
        self.assertEqual(a["score"], b["score"])
        self.assertEqual(a["veredito"], b["veredito"])
        self.assertAlmostEqual(a["calculados"]["altman_z"], b["calculados"]["altman_z"])

    def test_determinismo(self):
        self.assertEqual(credito_score.avaliar(EMISSOR_BOM),
                         credito_score.avaliar(EMISSOR_BOM))
