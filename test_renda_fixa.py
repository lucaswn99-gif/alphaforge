"""Renda fixa privada: validação, marcação na curva e isolamento por usuário.

O que está sob teste aqui tem duas camadas bem diferentes:

  - A ARITMÉTICA da marcação na curva (`marcar_na_curva`), testada com séries
    de CDI/IPCA INJETADAS — nunca uma chamada real ao BCB. Um teste de
    unidade que depende de rede é flaky por definição, e séries sintéticas
    permitem verificar o fator exato, não só "o número mudou".

  - O CRUD (adicionar/listar/atualizar/remover), que só usa o indexador
    PRÉ-FIXADO — não precisa de série nenhuma para marcar, então o teste
    de isolamento entre contas não faz uma chamada de rede por acidente.

    python -m unittest test_renda_fixa -v
"""
import os
import tempfile
import unittest
from datetime import date, timedelta

from fastapi.testclient import TestClient

from modules import contas, renda_fixa


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestValidacao(unittest.TestCase):
    def test_tipo_desconhecido_e_recusado(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa) as erro:
            renda_fixa.validar("Banco X", "poupanca", "pre", 10, "2024-01-01",
                               None, 1000)
        self.assertIn("cdb", str(erro.exception))

    def test_indexador_desconhecido_e_recusado(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa) as erro:
            renda_fixa.validar("Banco X", "cdb", "selic", 10, "2024-01-01",
                               None, 1000)
        self.assertIn("pre", str(erro.exception))

    def test_emissor_vazio_e_recusado(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa):
            renda_fixa.validar("   ", "cdb", "pre", 10, "2024-01-01", None, 1000)

    def test_data_de_aplicacao_no_futuro_e_recusada(self):
        futuro = (date.today() + timedelta(days=5)).isoformat()
        with self.assertRaises(renda_fixa.ErroRendaFixa):
            renda_fixa.validar("Banco X", "cdb", "pre", 10, futuro, None, 1000)

    def test_vencimento_antes_ou_igual_a_aplicacao_e_recusado(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa):
            renda_fixa.validar("Banco X", "cdb", "pre", 10, "2024-06-01",
                               "2024-06-01", 1000)

    def test_taxa_fora_da_faixa_e_recusada(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa):
            renda_fixa.validar("Banco X", "cdb", "pre", 150, "2024-01-01",
                               None, 1000)

    def test_valor_aplicado_precisa_ser_positivo(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa):
            renda_fixa.validar("Banco X", "cdb", "pre", 10, "2024-01-01",
                               None, 0)

    def test_data_em_formato_errado_explica_o_formato_aceito(self):
        with self.assertRaises(renda_fixa.ErroRendaFixa) as erro:
            renda_fixa.validar("Banco X", "cdb", "pre", 10, "01/01/2024",
                               None, 1000)
        self.assertIn("AAAA-MM-DD", str(erro.exception))

    def test_entrada_valida_normaliza_tipos(self):
        emissor, tipo, indexador, taxa, aplicacao, vencimento, valor = renda_fixa.validar(
            "  Banco X  ", "CDB", "PRE", "10.5", "2024-01-01", "2026-01-01",
            "1000.0")
        self.assertEqual(emissor, "Banco X")
        self.assertEqual(tipo, "cdb")
        self.assertEqual(indexador, "pre")
        self.assertEqual(taxa, 10.5)
        self.assertEqual(aplicacao, date(2024, 1, 1))
        self.assertEqual(vencimento, date(2026, 1, 1))
        self.assertEqual(valor, 1000.0)


class TestMarcacaoNaCurva(unittest.TestCase):
    """Séries de CDI/IPCA sempre injetadas — nunca uma chamada real ao BCB."""

    def test_hoje_igual_a_aplicacao_ainda_sem_rentabilidade(self):
        hoje = date(2025, 1, 1)
        posicao = {"data_aplicacao": hoje, "valor_aplicado": 100.0,
                  "indexador": renda_fixa.PRE, "taxa": 10.0}
        valor, resumo, apurado, _ = renda_fixa.marcar_na_curva(posicao, hoje=hoje)
        self.assertEqual(valor, 100.0)
        self.assertTrue(apurado)
        self.assertIn("ainda sem rentabilidade", resumo)

    def test_prefixado_composto_por_dias_uteis(self):
        """Segunda a segunda seguinte: 5 dias úteis (exclui os dois sábados/
        domingos, inclui a segunda de chegada)."""
        aplicacao = date(2025, 1, 6)
        hoje = date(2025, 1, 13)
        posicao = {"data_aplicacao": aplicacao, "valor_aplicado": 10_000.0,
                  "indexador": renda_fixa.PRE, "taxa": 10.0}
        valor, resumo, apurado, detalhes = renda_fixa.marcar_na_curva(
            posicao, hoje=hoje)
        self.assertEqual(detalhes["dias_uteis"], 5)
        esperado = round(10_000.0 * (1.10 ** (5 / 252)), 2)
        self.assertTrue(apurado)
        self.assertEqual(valor, esperado)
        self.assertIn("Pré-fixado", resumo)
        self.assertIn("10.00", resumo)

    def test_percentual_do_cdi_usa_o_fator_acumulado_da_serie_real(self):
        """% do CDI é o fator ACUMULADO elevado ao percentual contratado —
        não o percentual aplicado dia a dia."""
        serie = [{"data": "06/01/2025", "valor": 0.04},
                {"data": "07/01/2025", "valor": 0.05}]
        posicao = {"data_aplicacao": date(2025, 1, 6), "valor_aplicado": 1_000.0,
                  "indexador": renda_fixa.PCT_CDI, "taxa": 100.0}
        valor, resumo, apurado, detalhes = renda_fixa.marcar_na_curva(
            posicao, hoje=date(2025, 1, 8), buscar_cdi=lambda i, f: serie)
        fator_cdi = (1 + 0.04 / 100.0) * (1 + 0.05 / 100.0)
        esperado = round(1_000.0 * fator_cdi ** 1.0, 2)
        self.assertTrue(apurado)
        self.assertEqual(valor, esperado)
        self.assertEqual(detalhes["dias_uteis"], 2)
        self.assertIn("100.0% do CDI", resumo)

    def test_cdi_mais_soma_o_fator_do_cdi_com_o_spread_prefixado(self):
        serie = [{"data": "06/01/2025", "valor": 0.04},
                {"data": "07/01/2025", "valor": 0.04}]
        posicao = {"data_aplicacao": date(2025, 1, 6), "valor_aplicado": 1_000.0,
                  "indexador": renda_fixa.CDI_MAIS, "taxa": 3.0}
        valor, resumo, apurado, detalhes = renda_fixa.marcar_na_curva(
            posicao, hoje=date(2025, 1, 8), buscar_cdi=lambda i, f: serie)
        fator_cdi = (1.0004 ** 2)
        fator_spread = 1.03 ** (2 / 252)
        esperado = round(1_000.0 * fator_cdi * fator_spread, 2)
        self.assertTrue(apurado)
        self.assertEqual(valor, esperado)
        self.assertIn("CDI + 3.00", resumo)

    def test_ipca_mais_soma_ipca_fechado_com_taxa_no_resto_do_periodo(self):
        serie = [{"data": "30/11/2024", "valor": 0.50}]
        aplicacao = date(2024, 11, 5)
        hoje = date(2024, 12, 10)
        posicao = {"data_aplicacao": aplicacao, "valor_aplicado": 5_000.0,
                  "indexador": renda_fixa.IPCA_MAIS, "taxa": 6.0}
        valor, resumo, apurado, detalhes = renda_fixa.marcar_na_curva(
            posicao, hoje=hoje, buscar_ipca=lambda i, f: serie)
        du_resto = renda_fixa._dias_uteis_aprox(date(2024, 11, 30), hoje)
        fator = 1.005 * (1.06 ** (du_resto / 252))
        esperado = round(5_000.0 * fator, 2)
        self.assertTrue(apurado)
        self.assertEqual(valor, esperado)
        self.assertIn("IPCA+6.00", resumo)
        self.assertIn("30/11/2024", resumo)

    def test_sem_serie_do_cdi_o_valor_e_o_aplicado_nao_apurado(self):
        """Nunca inventa um dia de CDI: sem série, o valor fica no aplicado
        (não zero disfarçado, não número calculado com dado que não veio)."""
        posicao = {"data_aplicacao": date(2025, 1, 1), "valor_aplicado": 2_000.0,
                  "indexador": renda_fixa.PCT_CDI, "taxa": 100.0}
        valor, resumo, apurado, _ = renda_fixa.marcar_na_curva(
            posicao, hoje=date(2025, 2, 1), buscar_cdi=lambda i, f: None)
        self.assertFalse(apurado)
        self.assertEqual(valor, 2_000.0)
        self.assertIn("indisponível", resumo.lower())

    def test_sem_serie_do_ipca_o_valor_e_o_aplicado_nao_apurado(self):
        posicao = {"data_aplicacao": date(2025, 1, 1), "valor_aplicado": 3_000.0,
                  "indexador": renda_fixa.IPCA_MAIS, "taxa": 6.0}
        valor, resumo, apurado, _ = renda_fixa.marcar_na_curva(
            posicao, hoje=date(2025, 2, 1), buscar_ipca=lambda i, f: None)
        self.assertFalse(apurado)
        self.assertEqual(valor, 3_000.0)
        self.assertIn("indisponível", resumo.lower())


class TestCRUD(unittest.TestCase):
    """Só indexador PRÉ aqui: não precisa de série, então o isolamento entre
    contas não dispara uma chamada de rede por acidente."""

    def setUp(self):
        _banco_limpo()
        self.usuario, _ = contas.criar_usuario("rf@teste.com", "senha-boa-123")
        self.outro, _ = contas.criar_usuario("rf-outro@teste.com", "senha-boa-123")

    def test_adicionar_e_listar(self):
        linha = renda_fixa.adicionar(self.usuario["id"], "Banco X", "cdb", "pre",
                                     10.0, "2024-01-01", None, 1_000.0)
        self.assertEqual(linha["emissor"], "Banco X")
        self.assertEqual(linha["rotulo_tipo"], "CDB")
        dados = renda_fixa.listar(self.usuario["id"])
        self.assertEqual(dados["posicoes_total"], 1)
        self.assertEqual(dados["valor_aplicado_total"], 1_000.0)

    def test_isolamento_entre_usuarios(self):
        renda_fixa.adicionar(self.outro["id"], "Banco Y", "lci", "pre",
                             8.0, "2024-01-01", None, 500.0)
        self.assertEqual(renda_fixa.listar(self.usuario["id"])["posicoes_total"], 0)
        self.assertEqual(renda_fixa.listar(self.outro["id"])["posicoes_total"], 1)

    def test_atualizar_substitui_campos(self):
        linha = renda_fixa.adicionar(self.usuario["id"], "Banco X", "cdb", "pre",
                                     10.0, "2024-01-01", None, 1_000.0)
        atualizada = renda_fixa.atualizar(
            self.usuario["id"], linha["id"], "Banco X", "cdb", "pre", 12.0,
            "2024-01-01", None, 1_000.0)
        self.assertEqual(atualizada["taxa"], 12.0)

    def test_atualizar_posicao_de_outro_usuario_devolve_none(self):
        linha = renda_fixa.adicionar(self.outro["id"], "Banco Y", "lci", "pre",
                                     8.0, "2024-01-01", None, 500.0)
        resultado = renda_fixa.atualizar(
            self.usuario["id"], linha["id"], "Banco Y", "lci", "pre", 9.0,
            "2024-01-01", None, 500.0)
        self.assertIsNone(resultado)

    def test_remover_so_a_propria_posicao(self):
        linha = renda_fixa.adicionar(self.outro["id"], "Banco Y", "lci", "pre",
                                     8.0, "2024-01-01", None, 500.0)
        self.assertFalse(renda_fixa.remover(self.usuario["id"], linha["id"]))
        self.assertTrue(renda_fixa.remover(self.outro["id"], linha["id"]))
        self.assertEqual(renda_fixa.listar(self.outro["id"])["posicoes_total"], 0)

    def test_limite_de_posicoes(self):
        original = renda_fixa.MAXIMO_POSICOES
        renda_fixa.MAXIMO_POSICOES = 1
        try:
            renda_fixa.adicionar(self.usuario["id"], "Banco X", "cdb", "pre",
                                 10.0, "2024-01-01", None, 1_000.0)
            with self.assertRaises(renda_fixa.ErroRendaFixa):
                renda_fixa.adicionar(self.usuario["id"], "Banco Z", "cdb", "pre",
                                     10.0, "2024-01-01", None, 1_000.0)
        finally:
            renda_fixa.MAXIMO_POSICOES = original


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

    ROTAS = (("get", "/api/v1/carteira/renda-fixa", None),
             ("post", "/api/v1/carteira/renda-fixa",
              {"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
               "taxa": 10.0, "data_aplicacao": "2024-01-01",
               "valor_aplicado": 1000.0}),
             ("patch", "/api/v1/carteira/renda-fixa/1",
              {"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
               "taxa": 10.0, "data_aplicacao": "2024-01-01",
               "valor_aplicado": 1000.0}),
             ("delete", "/api/v1/carteira/renda-fixa/1", None))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_exigem_assinatura(self):
        self._entrar("rf-free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_parametros_lista_tipos_e_indexadores(self):
        self._entrar("rf-vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/renda-fixa/parametros").json()
        self.assertEqual([t["chave"] for t in corpo["tipos"]], list(renda_fixa.TIPOS))
        self.assertEqual([i["chave"] for i in corpo["indexadores"]],
                         list(renda_fixa.INDEXADORES))

    def test_fluxo_completo_criar_listar_editar_remover(self):
        self._entrar("rf-vip2@teste.com")
        criar = self.cliente.post(
            "/api/v1/carteira/renda-fixa",
            json={"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
                 "taxa": 10.0, "data_aplicacao": "2024-01-01",
                 "valor_aplicado": 1000.0})
        self.assertEqual(criar.status_code, 201, criar.text)
        posicao_id = criar.json()["posicao"]["id"]

        listar = self.cliente.get("/api/v1/carteira/renda-fixa").json()
        self.assertEqual(listar["posicoes_total"], 1)

        editar = self.cliente.patch(
            f"/api/v1/carteira/renda-fixa/{posicao_id}",
            json={"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
                 "taxa": 11.5, "data_aplicacao": "2024-01-01",
                 "valor_aplicado": 1000.0})
        self.assertEqual(editar.status_code, 200)
        self.assertEqual(editar.json()["posicao"]["taxa"], 11.5)

        remover = self.cliente.delete(f"/api/v1/carteira/renda-fixa/{posicao_id}")
        self.assertEqual(remover.status_code, 200)
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/renda-fixa").json()["posicoes_total"], 0)

    def test_entrada_invalida_volta_422_com_motivo(self):
        self._entrar("rf-vip3@teste.com")
        resposta = self.cliente.post(
            "/api/v1/carteira/renda-fixa",
            json={"emissor": "Banco X", "tipo": "poupanca", "indexador": "pre",
                 "taxa": 10.0, "data_aplicacao": "2024-01-01",
                 "valor_aplicado": 1000.0})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("cdb", resposta.json()["detail"]["motivo"])

    def test_editar_ou_remover_posicao_inexistente_volta_404(self):
        self._entrar("rf-vip4@teste.com")
        editar = self.cliente.patch(
            "/api/v1/carteira/renda-fixa/999999",
            json={"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
                 "taxa": 10.0, "data_aplicacao": "2024-01-01",
                 "valor_aplicado": 1000.0})
        self.assertEqual(editar.status_code, 404)
        remover = self.cliente.delete("/api/v1/carteira/renda-fixa/999999")
        self.assertEqual(remover.status_code, 404)

    def test_nao_enxerga_nem_apaga_posicao_de_outra_conta(self):
        self._entrar("rf-vip5@teste.com")
        criar = self.cliente.post(
            "/api/v1/carteira/renda-fixa",
            json={"emissor": "Banco X", "tipo": "cdb", "indexador": "pre",
                 "taxa": 10.0, "data_aplicacao": "2024-01-01",
                 "valor_aplicado": 1000.0})
        posicao_id = criar.json()["posicao"]["id"]

        self.cliente.cookies.clear()
        self._entrar("rf-vip6@teste.com")
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/renda-fixa").json()["posicoes_total"], 0)
        remover = self.cliente.delete(f"/api/v1/carteira/renda-fixa/{posicao_id}")
        self.assertEqual(remover.status_code, 404)


if __name__ == "__main__":
    unittest.main()
