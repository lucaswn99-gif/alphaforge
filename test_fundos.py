"""Fundos de investimento: validação, CRUD, isolamento por usuário (Etapa A)
e a apuração pela cota diária da CVM (Etapa B, injetada via
`buscar_cota_recente` — sem tocar rede nem `cota_fundos_cvm.db` de verdade).

Sem CNPJ coletado (o caso comum destes testes, que não injetam nada), o
valor nunca é inventado: `apurado=False`, valor atual = aplicado. Com CNPJ
coletado (testes de `TestApuracaoPelaCotaCvm`), o valor atual e a
rentabilidade vêm da cota mais recente — nunca uma correção parcial.

    python -m unittest test_fundos -v
"""
import os
import tempfile
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from modules import contas, fundos


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestValidacao(unittest.TestCase):
    def test_classe_desconhecida_e_recusada(self):
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar_classe("imoveis")

    def test_cnpj_com_pontuacao_e_normalizado_para_so_digitos(self):
        cnpj = fundos.validar_cnpj("12.345.678/0001-99")
        self.assertEqual(cnpj, "12345678000199")

    def test_cnpj_com_menos_de_14_digitos_e_recusado(self):
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar_cnpj("123456")

    def test_formatar_cnpj_devolve_a_mascara_padrao(self):
        self.assertEqual(fundos.formatar_cnpj("12345678000199"),
                         "12.345.678/0001-99")

    def test_nome_vazio_e_recusado(self):
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar("", "12345678000199", "multimercado", 10, 100.0,
                           "2024-01-01")

    def test_numero_de_cotas_negativo_e_recusado(self):
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar("Fundo X", "12345678000199", "multimercado", -5,
                           100.0, "2024-01-01")

    def test_valor_da_cota_zero_e_recusado(self):
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar("Fundo X", "12345678000199", "multimercado", 10,
                           0, "2024-01-01")

    def test_data_de_aplicacao_no_futuro_e_recusada(self):
        amanha = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(fundos.ErroFundo):
            fundos.validar("Fundo X", "12345678000199", "multimercado", 10,
                           100.0, amanha)

    def test_entrada_valida_normaliza_e_devolve_a_tupla(self):
        nome, cnpj, classe, cotas, valor_cota, aplicacao = fundos.validar(
            "  Fundo X  ", "12.345.678/0001-99", "MULTIMERCADO", "10", "100.5",
            "2024-01-01")
        self.assertEqual((nome, cnpj, classe, cotas, valor_cota, aplicacao),
                         ("Fundo X", "12345678000199", "multimercado", 10.0,
                          100.5, date(2024, 1, 1)))


class TestCRUD(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        self.usuario, _ = contas.criar_usuario("fundo@teste.com", "senha-boa-123")
        self.outro, _ = contas.criar_usuario("fundo-outro@teste.com", "senha-boa-123")

    def test_adicionar_calcula_valor_aplicado_por_cotas_vezes_valor_da_cota(self):
        linha = fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                                 "multimercado", 100.0, 150.25, "2024-01-01")
        self.assertEqual(linha["valor_aplicado"], 15_025.0)
        self.assertEqual(linha["valor_atual"], 15_025.0)

    def test_posicao_nunca_apurada_ate_a_etapa_b(self):
        linha = fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                                 "multimercado", 10.0, 100.0, "2024-01-01")
        self.assertFalse(linha["apurado"])
        self.assertIn("cota diária", linha["marcacao"].lower())

    def test_listar_soma_o_total_aplicado(self):
        fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                         "multimercado", 10.0, 100.0, "2024-01-01")
        fundos.adicionar(self.usuario["id"], "Fundo Y", "98765432000188",
                         "acoes", 5.0, 200.0, "2024-01-01")
        dados = fundos.listar(self.usuario["id"])
        self.assertEqual(dados["posicoes_total"], 2)
        self.assertEqual(dados["valor_aplicado_total"], 1_000.0 + 1_000.0)

    def test_isolamento_entre_usuarios(self):
        fundos.adicionar(self.outro["id"], "Fundo Y", "98765432000188",
                         "acoes", 5.0, 200.0, "2024-01-01")
        self.assertEqual(fundos.listar(self.usuario["id"])["posicoes_total"], 0)
        self.assertEqual(fundos.listar(self.outro["id"])["posicoes_total"], 1)

    def test_atualizar_substitui_campos(self):
        linha = fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                                 "multimercado", 10.0, 100.0, "2024-01-01")
        atualizada = fundos.atualizar(
            self.usuario["id"], linha["id"], "Fundo X", "12345678000199",
            "multimercado", 20.0, 100.0, "2024-01-01")
        self.assertEqual(atualizada["numero_cotas"], 20.0)
        self.assertEqual(atualizada["valor_aplicado"], 2_000.0)

    def test_atualizar_posicao_de_outro_usuario_devolve_none(self):
        linha = fundos.adicionar(self.outro["id"], "Fundo Y", "98765432000188",
                                 "acoes", 5.0, 200.0, "2024-01-01")
        resultado = fundos.atualizar(
            self.usuario["id"], linha["id"], "Fundo Y", "98765432000188",
            "acoes", 6.0, 200.0, "2024-01-01")
        self.assertIsNone(resultado)

    def test_remover_so_a_propria_posicao(self):
        linha = fundos.adicionar(self.outro["id"], "Fundo Y", "98765432000188",
                                 "acoes", 5.0, 200.0, "2024-01-01")
        self.assertFalse(fundos.remover(self.usuario["id"], linha["id"]))
        self.assertTrue(fundos.remover(self.outro["id"], linha["id"]))
        self.assertEqual(fundos.listar(self.outro["id"])["posicoes_total"], 0)

    def test_limite_de_posicoes(self):
        original = fundos.MAXIMO_POSICOES
        fundos.MAXIMO_POSICOES = 1
        try:
            fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                             "multimercado", 10.0, 100.0, "2024-01-01")
            with self.assertRaises(fundos.ErroFundo):
                fundos.adicionar(self.usuario["id"], "Fundo Z", "98765432000188",
                                 "acoes", 5.0, 200.0, "2024-01-01")
        finally:
            fundos.MAXIMO_POSICOES = original


class TestApuracaoPelaCotaCvm(unittest.TestCase):
    """`buscar_cota_recente` é injetado — nenhum destes testes toca
    `cota_fundos_cvm.db` de verdade nem a rede."""

    def setUp(self):
        _banco_limpo()
        self.usuario, _ = contas.criar_usuario("fundo-cvm@teste.com", "senha-boa-123")

    def test_cnpj_coletado_fica_apurado_e_calcula_rentabilidade(self):
        fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                         "multimercado", 100.0, 150.0, "2024-01-01")
        dados = fundos.listar(self.usuario["id"],
                              buscar_cota_recente=lambda cnpj: (165.0, "2026-09-12"))
        linha = dados["posicoes"][0]
        self.assertTrue(linha["apurado"])
        self.assertEqual(linha["valor_atual"], 16_500.0)
        self.assertEqual(linha["rentabilidade_pct"], 10.0)
        self.assertIn("2026-09-12", linha["marcacao"])
        self.assertEqual(dados["nao_apurados"], 0)
        self.assertEqual(dados["valor_atual_total"], 16_500.0)

    def test_cnpj_nao_coletado_continua_no_piso_do_aplicado(self):
        fundos.adicionar(self.usuario["id"], "Fundo X", "12345678000199",
                         "multimercado", 100.0, 150.0, "2024-01-01")
        dados = fundos.listar(self.usuario["id"], buscar_cota_recente=lambda cnpj: (None, None))
        linha = dados["posicoes"][0]
        self.assertFalse(linha["apurado"])
        self.assertEqual(linha["valor_atual"], 15_000.0)
        self.assertEqual(dados["nao_apurados"], 1)

    def test_apurado_e_por_posicao_nao_uma_bandeira_unica(self):
        """Dois fundos, só um coletado: cada posição decide por si — Etapa B
        não é tudo-ou-nada."""
        fundos.adicionar(self.usuario["id"], "Fundo Coletado", "12345678000199",
                         "multimercado", 10.0, 100.0, "2024-01-01")
        fundos.adicionar(self.usuario["id"], "Fundo Sem Coleta", "98765432000188",
                         "acoes", 10.0, 100.0, "2024-01-01")

        def buscar(cnpj):
            return (120.0, "2026-09-12") if cnpj == "12345678000199" else (None, None)

        dados = fundos.listar(self.usuario["id"], buscar_cota_recente=buscar)
        por_cnpj = {p["cnpj"]: p for p in dados["posicoes"]}
        self.assertTrue(por_cnpj["12345678000199"]["apurado"])
        self.assertFalse(por_cnpj["98765432000188"]["apurado"])
        self.assertEqual(dados["nao_apurados"], 1)

    def test_peso_e_sobre_o_valor_atual_nao_o_aplicado(self):
        fundos.adicionar(self.usuario["id"], "Fundo A", "12345678000199",
                         "multimercado", 10.0, 100.0, "2024-01-01")  # aplicado 1.000
        fundos.adicionar(self.usuario["id"], "Fundo B", "98765432000188",
                         "acoes", 10.0, 100.0, "2024-01-01")  # aplicado 1.000

        def buscar(cnpj):
            # Fundo A dobrou de valor; Fundo B não tem coleta.
            return (200.0, "2026-09-12") if cnpj == "12345678000199" else (None, None)

        dados = fundos.listar(self.usuario["id"], buscar_cota_recente=buscar)
        por_cnpj = {p["cnpj"]: p for p in dados["posicoes"]}
        # Atual: A = 2.000, B = 1.000, total = 3.000 -> A pesa 66,67%.
        self.assertAlmostEqual(por_cnpj["12345678000199"]["peso_pct"], 66.67, places=1)


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

    ROTAS = (("get", "/api/v1/carteira/fundos", None),
             ("post", "/api/v1/carteira/fundos",
              {"nome_fundo": "Fundo X", "cnpj": "12345678000199",
               "classe": "multimercado", "numero_cotas": 10.0,
               "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"}),
             ("patch", "/api/v1/carteira/fundos/1",
              {"nome_fundo": "Fundo X", "cnpj": "12345678000199",
               "classe": "multimercado", "numero_cotas": 10.0,
               "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"}),
             ("delete", "/api/v1/carteira/fundos/1", None))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_exigem_assinatura(self):
        self._entrar("fundo-free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_parametros_lista_as_classes(self):
        self._entrar("fundo-vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/fundos/parametros").json()
        self.assertEqual([c["chave"] for c in corpo["classes"]], list(fundos.CLASSES))

    def test_fluxo_completo_criar_listar_editar_remover(self):
        self._entrar("fundo-vip2@teste.com")
        criar = self.cliente.post("/api/v1/carteira/fundos", json={
            "nome_fundo": "Fundo X", "cnpj": "12.345.678/0001-99",
            "classe": "multimercado", "numero_cotas": 10.0,
            "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"})
        self.assertEqual(criar.status_code, 201, criar.text)
        posicao_id = criar.json()["posicao"]["id"]
        self.assertEqual(criar.json()["posicao"]["valor_aplicado"], 1_000.0)
        self.assertFalse(criar.json()["posicao"]["apurado"])

        listar = self.cliente.get("/api/v1/carteira/fundos").json()
        self.assertEqual(listar["posicoes_total"], 1)

        editar = self.cliente.patch(f"/api/v1/carteira/fundos/{posicao_id}", json={
            "nome_fundo": "Fundo X", "cnpj": "12345678000199",
            "classe": "multimercado", "numero_cotas": 20.0,
            "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"})
        self.assertEqual(editar.json()["posicao"]["valor_aplicado"], 2_000.0)

        apagar = self.cliente.delete(f"/api/v1/carteira/fundos/{posicao_id}")
        self.assertEqual(apagar.status_code, 200)
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/fundos").json()["posicoes_total"], 0)

    def test_entrada_invalida_devolve_422_com_motivo(self):
        self._entrar("fundo-vip3@teste.com")
        resposta = self.cliente.post("/api/v1/carteira/fundos", json={
            "nome_fundo": "Fundo X", "cnpj": "123", "classe": "multimercado",
            "numero_cotas": 10.0, "valor_cota_aplicacao": 100.0,
            "data_aplicacao": "2024-01-01"})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("motivo", resposta.json()["detail"])

    def test_editar_ou_remover_posicao_de_outra_conta_devolve_404(self):
        self._entrar("fundo-dono@teste.com")
        criar = self.cliente.post("/api/v1/carteira/fundos", json={
            "nome_fundo": "Fundo X", "cnpj": "12345678000199",
            "classe": "multimercado", "numero_cotas": 10.0,
            "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"})
        posicao_id = criar.json()["posicao"]["id"]

        self._entrar("fundo-intruso@teste.com")
        editar = self.cliente.patch(f"/api/v1/carteira/fundos/{posicao_id}", json={
            "nome_fundo": "Fundo X", "cnpj": "12345678000199",
            "classe": "multimercado", "numero_cotas": 99.0,
            "valor_cota_aplicacao": 100.0, "data_aplicacao": "2024-01-01"})
        self.assertEqual(editar.status_code, 404)
        apagar = self.cliente.delete(f"/api/v1/carteira/fundos/{posicao_id}")
        self.assertEqual(apagar.status_code, 404)


if __name__ == "__main__":
    unittest.main()
