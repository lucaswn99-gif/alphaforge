"""Projeção de capital com aportes e metas: matemática pura (sem rede, sem
banco na parte de cálculo) e as rotas que a alimentam com o patrimônio real
da carteira e o perfil cadastrado.

    python -m unittest test_projecao -v
"""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from modules import contas, perfil, projecao


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestValidacao(unittest.TestCase):
    def test_taxa_fora_da_faixa_e_recusada(self):
        with self.assertRaises(projecao.ErroProjecao):
            projecao.validar(150.0, 0, 10)
        with self.assertRaises(projecao.ErroProjecao):
            projecao.validar(-60.0, 0, 10)

    def test_aporte_negativo_e_recusado(self):
        with self.assertRaises(projecao.ErroProjecao):
            projecao.validar(10.0, -100, 10)

    def test_horizonte_fora_da_faixa_e_recusado(self):
        with self.assertRaises(projecao.ErroProjecao):
            projecao.validar(10.0, 0, 0)
        with self.assertRaises(projecao.ErroProjecao):
            projecao.validar(10.0, 0, 81)

    def test_entrada_valida_normaliza_os_tipos(self):
        taxa, aporte, horizonte = projecao.validar("10", "500", "5")
        self.assertEqual((taxa, aporte, horizonte), (10.0, 500.0, 5))


class TestProjetar(unittest.TestCase):
    def test_sem_aporte_e_so_juros_compostos(self):
        """0% a.a.: sem aporte, o valor não sai do lugar."""
        resultado = projecao.projetar(1000.0, 0.0, 0.0, 3)
        self.assertEqual(resultado["valor_final"], 1000.0)
        self.assertEqual(resultado["total_aportado"], 0.0)
        self.assertEqual(resultado["rendimento_total"], 0.0)
        self.assertEqual(len(resultado["serie"]), 4)  # anos 0,1,2,3

    def test_com_aporte_e_zero_de_taxa_e_soma_simples(self):
        resultado = projecao.projetar(1000.0, 0.0, 100.0, 2)
        self.assertEqual(resultado["valor_final"], 1000.0 + 100.0 * 24)
        self.assertEqual(resultado["total_aportado"], 100.0 * 24)
        self.assertEqual(resultado["rendimento_total"], 0.0)

    def test_taxa_anual_bate_com_composicao_mensal_independente(self):
        """10% a.a. sem aporte, 1 ano: o valor final tem que bater com
        1000 * 1.10 (a taxa mensal composta 12x volta na taxa anual)."""
        resultado = projecao.projetar(1000.0, 10.0, 0.0, 1)
        self.assertAlmostEqual(resultado["valor_final"], 1100.0, places=2)

    def test_renda_mensal_sustentavel_e_taxa_mensal_vezes_patrimonio(self):
        resultado = projecao.projetar(1000.0, 12.0, 0.0, 1)
        taxa_mensal = (1.12 ** (1 / 12)) - 1
        esperado = round(resultado["valor_final"] * taxa_mensal, 2)
        self.assertAlmostEqual(resultado["renda_mensal_sustentavel_final"],
                               esperado, places=2)

    def test_serie_comeca_no_ano_zero_com_o_valor_inicial(self):
        resultado = projecao.projetar(5000.0, 8.0, 200.0, 5)
        self.assertEqual(resultado["serie"][0], {
            "ano": 0, "valor": 5000.0,
            "renda_mensal_sustentavel": resultado["serie"][0]["renda_mensal_sustentavel"]})
        self.assertEqual(resultado["serie"][-1]["ano"], 5)


class TestCalcularGaps(unittest.TestCase):
    def test_sem_objetivo_nao_ha_gap(self):
        self.assertEqual(projecao.calcular_gaps(None, None, None, None, 1000.0, 10.0), [])

    def test_renda_passiva_sem_meta_nao_gera_gap(self):
        self.assertEqual(
            projecao.calcular_gaps(perfil.RENDA_PASSIVA, None, None, None, 1000.0, 10.0), [])

    def test_renda_passiva_com_meta_mostra_gap_assinado(self):
        gaps = projecao.calcular_gaps(
            perfil.RENDA_PASSIVA, 5000.0, None, None, 1_000_000.0, 4000.0)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["tipo"], "renda_mensal")
        self.assertEqual(gaps[0]["gap"], 1000.0)  # falta 1000

    def test_renda_passiva_ja_coberta_da_gap_negativo_sobra(self):
        gaps = projecao.calcular_gaps(
            perfil.RENDA_PASSIVA, 3000.0, None, None, 1_000_000.0, 4000.0)
        self.assertEqual(gaps[0]["gap"], -1000.0)  # sobra 1000

    def test_aposentadoria_com_as_duas_metas_gera_dois_gaps(self):
        gaps = projecao.calcular_gaps(
            perfil.APOSENTADORIA, None, 2_000_000.0, 8000.0, 1_500_000.0, 6000.0)
        tipos = {g["tipo"] for g in gaps}
        self.assertEqual(tipos, {"patrimonio", "renda_mensal"})
        patrimonio = next(g for g in gaps if g["tipo"] == "patrimonio")
        self.assertEqual(patrimonio["gap"], 500_000.0)

    def test_aposentadoria_sem_nenhuma_meta_cadastrada_nao_inventa_gap(self):
        self.assertEqual(
            projecao.calcular_gaps(perfil.APOSENTADORIA, None, None, None,
                                   1_500_000.0, 6000.0), [])


class TestRotas(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        import api
        self.cliente = TestClient(api.app)

    def _entrar(self, email, premium=True):
        self.cliente.post("/conta/registrar",
                          json={"email": email, "senha": "senha-boa-123"})
        usuario = contas.autenticar(email, "senha-boa-123")
        if premium:
            contas.definir_plano(usuario["id"], "premium")
        return usuario

    def test_sem_sessao_devolve_401(self):
        self.cliente.cookies.clear()
        resposta = self.cliente.post("/api/v1/carteira/projecao", json={
            "taxa_anual_pct": 10, "aporte_mensal": 100, "horizonte_anos": 5})
        self.assertEqual(resposta.status_code, 401)

    def test_conta_gratuita_devolve_402(self):
        self._entrar("proj-free@teste.com", premium=False)
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 100, "horizonte_anos": 5})
        self.assertEqual(resposta.status_code, 402)

    def test_carteira_vazia_e_sem_aporte_devolve_422(self):
        self._entrar("proj-vazia@teste.com")
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 0, "horizonte_anos": 5})
        self.assertEqual(resposta.status_code, 422)

    def test_taxa_invalida_devolve_422(self):
        self._entrar("proj-taxa@teste.com")
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 999, "aporte_mensal": 100, "horizonte_anos": 5})
        self.assertEqual(resposta.status_code, 422)

    def test_so_com_aporte_ja_projeta_mesmo_sem_posicao(self):
        self._entrar("proj-aporte@teste.com")
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 500, "horizonte_anos": 5})
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["valor_inicial"], 0.0)
        self.assertIn("aviso", corpo)
        self.assertIn("hipotética", corpo["aviso"].lower())
        self.assertEqual(corpo["gaps"], [])  # sem perfil cadastrado

    def test_com_perfil_e_meta_devolve_gap(self):
        self._entrar("proj-perfil@teste.com")
        self.cliente.put("/api/v1/carteira/perfil", json={
            "perfil": "moderado", "objetivo": "renda_passiva",
            "meta_retirada_mensal": 3000.0})
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 1000, "horizonte_anos": 10})
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["gaps"]), 1)
        self.assertEqual(corpo["gaps"][0]["tipo"], "renda_mensal")
        self.assertEqual(corpo["objetivo"], "renda_passiva")

    def test_valor_inicial_soma_custo_de_acao_e_valor_atual_de_renda_fixa(self):
        self._entrar("proj-soma@teste.com")
        self.cliente.post("/api/v1/carteira/item", json={
            "ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0})
        self.cliente.post("/api/v1/carteira/renda-fixa", json={
            "emissor": "Banco Exemplo", "tipo": "cdb", "indexador": "pre",
            "taxa": 10.0, "data_aplicacao": "2024-01-02",
            "data_vencimento": None, "valor_aplicado": 5000.0})
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 0, "horizonte_anos": 1})
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        # custo de ação (100*30=3000) + valor atual de renda fixa (>= 5000
        # aplicado, marcado na curva) — nunca só um dos dois.
        self.assertGreaterEqual(corpo["valor_inicial"], 3000.0 + 5000.0)

    def test_valor_inicial_conta_tambem_fundos_pelo_valor_aplicado(self):
        """Fundos (Etapa A) entram no patrimônio total — literal, no sentido
        do documento de escopo — mesmo sem cota diária própria ainda."""
        self._entrar("proj-fundo@teste.com")
        self.cliente.post("/api/v1/carteira/fundos", json={
            "nome_fundo": "Fundo X", "cnpj": "12345678000199",
            "classe": "multimercado", "numero_cotas": 10.0,
            "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"})
        resposta = self.cliente.post(
            "/api/v1/carteira/projecao",
            json={"taxa_anual_pct": 10, "aporte_mensal": 0, "horizonte_anos": 1})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["valor_inicial"], 1_000.0)


if __name__ == "__main__":
    unittest.main()
