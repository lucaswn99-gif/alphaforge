"""Perfil do investidor e objetivo: campo simples, sem questionário próprio.

O que está sob teste: perfil e objetivo são independentes e cada um pode
ficar sem valor (sem padrão para nenhum dos dois); o objetivo carrega só os
campos que ele precisa, e trocar de objetivo não deixa resto do anterior.

    python -m unittest test_perfil -v
"""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from modules import contas, perfil


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestValidacao(unittest.TestCase):
    def test_perfil_vazio_normaliza_para_none(self):
        self.assertIsNone(perfil.validar_perfil(""))
        self.assertIsNone(perfil.validar_perfil(None))

    def test_perfil_desconhecido_e_recusado(self):
        with self.assertRaises(perfil.ErroPerfil) as erro:
            perfil.validar_perfil("agressivo")
        self.assertIn("conservador", str(erro.exception))

    def test_perfil_normaliza_caixa(self):
        self.assertEqual(perfil.validar_perfil("  MODERADO "), "moderado")

    def test_sem_objetivo_todos_os_campos_voltam_none(self):
        resultado = perfil.validar("arrojado", None)
        self.assertEqual(resultado, ("arrojado", None, None, None, None, None))

    def test_objetivo_desconhecido_e_recusado(self):
        with self.assertRaises(perfil.ErroPerfil):
            perfil.validar("moderado", "ficar_rico")

    def test_renda_passiva_exige_meta_de_retirada(self):
        with self.assertRaises(perfil.ErroPerfil) as erro:
            perfil.validar("moderado", "renda_passiva")
        self.assertIn("retirada mensal", str(erro.exception))

    def test_renda_passiva_valida_zera_campos_de_aposentadoria(self):
        resultado = perfil.validar("moderado", "renda_passiva",
                                   meta_retirada_mensal=5000)
        self.assertEqual(resultado,
                         ("moderado", "renda_passiva", 5000.0, None, None, None))

    def test_aposentadoria_exige_horizonte(self):
        with self.assertRaises(perfil.ErroPerfil) as erro:
            perfil.validar("conservador", "aposentadoria")
        self.assertIn("horizonte", str(erro.exception).lower())

    def test_aposentadoria_aceita_metas_opcionais(self):
        resultado = perfil.validar(
            "arrojado", "aposentadoria", horizonte_anos=20,
            meta_patrimonio=1_000_000, meta_renda_mensal=8_000)
        self.assertEqual(resultado,
                         ("arrojado", "aposentadoria", None, 20, 1_000_000.0, 8_000.0))

    def test_aposentadoria_sem_metas_opcionais_fica_none(self):
        resultado = perfil.validar("arrojado", "aposentadoria", horizonte_anos=15)
        self.assertEqual(resultado, ("arrojado", "aposentadoria", None, 15, None, None))

    def test_horizonte_precisa_ser_inteiro_positivo(self):
        with self.assertRaises(perfil.ErroPerfil):
            perfil.validar("moderado", "aposentadoria", horizonte_anos=0)
        with self.assertRaises(perfil.ErroPerfil):
            perfil.validar("moderado", "aposentadoria", horizonte_anos=-5)

    def test_meta_de_retirada_precisa_ser_positiva(self):
        with self.assertRaises(perfil.ErroPerfil):
            perfil.validar("moderado", "renda_passiva", meta_retirada_mensal=0)


class TestObterEDefinir(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        self.usuario, _ = contas.criar_usuario("perfil@teste.com", "senha-boa-123")

    def test_sem_registro_devolve_tudo_none(self):
        dados = perfil.obter(self.usuario["id"])
        self.assertIsNone(dados["perfil"])
        self.assertIsNone(dados["objetivo"])

    def test_definir_e_ler_de_volta(self):
        perfil.definir(self.usuario["id"], "conservador", "renda_passiva",
                       meta_retirada_mensal=3000)
        dados = perfil.obter(self.usuario["id"])
        self.assertEqual(dados["perfil"], "conservador")
        self.assertEqual(dados["objetivo"], "renda_passiva")
        self.assertEqual(dados["meta_retirada_mensal"], 3000.0)
        self.assertEqual(dados["rotulo_perfil"], "Conservador")

    def test_trocar_de_objetivo_nao_deixa_resto_do_anterior(self):
        """O caso real: renda passiva com meta de retirada, depois trocado
        para aposentadoria — a meta de retirada antiga não pode sobreviver."""
        perfil.definir(self.usuario["id"], "moderado", "renda_passiva",
                       meta_retirada_mensal=5000)
        perfil.definir(self.usuario["id"], "moderado", "aposentadoria",
                       horizonte_anos=20)
        dados = perfil.obter(self.usuario["id"])
        self.assertEqual(dados["objetivo"], "aposentadoria")
        self.assertIsNone(dados["meta_retirada_mensal"])
        self.assertEqual(dados["horizonte_anos"], 20)

    def test_redefinir_substitui_nao_acumula(self):
        perfil.definir(self.usuario["id"], "conservador", None)
        perfil.definir(self.usuario["id"], "arrojado", None)
        self.assertEqual(perfil.obter(self.usuario["id"])["perfil"], "arrojado")

    def test_nao_vaza_entre_usuarios(self):
        outro, _ = contas.criar_usuario("perfil-outro@teste.com", "senha-boa-123")
        perfil.definir(self.usuario["id"], "arrojado", None)
        self.assertIsNone(perfil.obter(outro["id"])["perfil"])


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

    ROTAS = (("get", "/api/v1/carteira/perfil", None),
             ("put", "/api/v1/carteira/perfil", {"perfil": "moderado"}))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_exigem_assinatura(self):
        self._entrar("perfil-free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_sem_registro_devolve_tudo_none(self):
        self._entrar("perfil-vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/perfil").json()
        self.assertIsNone(corpo["perfil"])
        self.assertIsNone(corpo["objetivo"])

    def test_definir_e_ler_de_volta_pela_rota(self):
        self._entrar("perfil-vip2@teste.com")
        gravar = self.cliente.put(
            "/api/v1/carteira/perfil",
            json={"perfil": "arrojado", "objetivo": "aposentadoria",
                 "horizonte_anos": 25, "meta_patrimonio": 2_000_000})
        self.assertEqual(gravar.status_code, 200, gravar.text)
        ler = self.cliente.get("/api/v1/carteira/perfil").json()
        self.assertEqual(ler["perfil"], "arrojado")
        self.assertEqual(ler["objetivo"], "aposentadoria")
        self.assertEqual(ler["horizonte_anos"], 25)
        self.assertEqual(ler["meta_patrimonio"], 2_000_000.0)

    def test_objetivo_sem_campo_obrigatorio_volta_422(self):
        self._entrar("perfil-vip3@teste.com")
        resposta = self.cliente.put(
            "/api/v1/carteira/perfil",
            json={"perfil": "moderado", "objetivo": "renda_passiva"})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("retirada mensal", resposta.json()["detail"]["motivo"])


if __name__ == "__main__":
    unittest.main()
