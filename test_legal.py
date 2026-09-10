"""Aviso de não-recomendação, páginas públicas e Digital Asset Links.

O que se testa aqui não é comportamento de cálculo — é a presença de texto
que precisa estar na tela por obrigação. Aviso é o tipo de coisa que some numa
refatoração sem ninguém perceber, porque nenhum número muda quando ele some.
Por isso tem teste.
"""

import importlib
import json
import os
import unittest

from fastapi.testclient import TestClient

from modules import legal


def _cliente():
    import api
    return TestClient(api.app)


class TestTextoLegal(unittest.TestCase):
    def test_aviso_tem_as_pecas_obrigatorias(self):
        aviso = legal.aviso()
        self.assertTrue(aviso["versao"])
        self.assertTrue(aviso["atualizado_em"])
        self.assertGreaterEqual(len(aviso["paragrafos"]), 6)

    def test_aviso_nega_recomendacao_e_cita_as_resolucoes(self):
        texto = " ".join(legal.aviso()["paragrafos"]).lower()
        self.assertIn("nada aqui constitui recomendação", texto)
        self.assertIn("consultoria de valores mobiliários", texto)
        self.assertIn("análise de valores mobiliários", texto)
        self.assertIn("20/2021", texto)   # análise
        self.assertIn("19/2021", texto)   # consultoria

    def test_aviso_trata_risco_e_desempenho_passado(self):
        texto = " ".join(legal.aviso()["paragrafos"]).lower()
        self.assertIn("rentabilidade passada", texto)
        self.assertIn("perdas superiores ao capital", texto)

    def test_resumo_curto_cabe_num_rodape(self):
        self.assertLess(len(legal.AVISO_CURTO), 160)

    def test_placeholders_sao_resolvidos(self):
        for _, paragrafos in (legal.secoes(legal.TERMOS)
                              + legal.secoes(legal.PRIVACIDADE)):
            for p in paragrafos:
                self.assertNotIn("{", p, msg=p)

    def test_contato_nao_inventa_endereco(self):
        """Sem CONTATO_LEGAL no ambiente, a página diz que não foi configurado
        em vez de publicar um e-mail que não existe."""
        anterior = legal.CONTATO
        try:
            legal.CONTATO = ""
            texto = legal.secoes(legal.PRIVACIDADE)[-1][1][0]
            self.assertIn("não configurado", texto.lower())
            self.assertNotIn("@", texto)
        finally:
            legal.CONTATO = anterior


class TestRotasPublicas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cliente = _cliente()

    def test_termos_responde_html(self):
        r = self.cliente.get("/termos")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["content-type"])
        self.assertIn("Termos de uso", r.text)
        self.assertIn("não constitui recomendação", r.text)

    def test_privacidade_responde_html(self):
        r = self.cliente.get("/privacidade")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Política de privacidade", r.text)
        # Exigido pelo Google Play: dizer o que se coleta e o que não se coleta.
        self.assertIn("Dados que coletamos", r.text)
        self.assertIn("Dados que NÃO coletamos", r.text)
        self.assertIn("13.709", r.text)  # LGPD

    def test_paginas_linkam_uma_a_outra_e_o_terminal(self):
        for rota in ("/termos", "/privacidade"):
            with self.subTest(rota=rota):
                texto = self.cliente.get(rota).text
                self.assertIn('href="/"', texto)
                self.assertIn('href="/termos"', texto)
                self.assertIn('href="/privacidade"', texto)

    def test_api_legal_devolve_o_mesmo_texto_da_pagina(self):
        dados = self.cliente.get("/api/legal").json()
        self.assertEqual(dados["paragrafos"], legal.aviso()["paragrafos"])
        self.assertEqual(dados["versao"], legal.VERSAO)


class TestAssetLinks(unittest.TestCase):
    """Digital Asset Links: sem ele o TWA abre com barra de navegador."""

    def _recarregar(self, sha=None, pacote=None):
        if sha is None:
            os.environ.pop("ANDROID_SHA256", None)
        else:
            os.environ["ANDROID_SHA256"] = sha
        if pacote:
            os.environ["ANDROID_PACOTE"] = pacote
        import routers.legal as rl
        importlib.reload(rl)
        import api
        importlib.reload(api)
        return TestClient(api.app)

    def tearDown(self):
        os.environ.pop("ANDROID_SHA256", None)
        os.environ.pop("ANDROID_PACOTE", None)
        self._recarregar()

    def test_sem_impressao_devolve_503_com_instrucao(self):
        r = self._recarregar().get("/.well-known/assetlinks.json")
        self.assertEqual(r.status_code, 503)
        self.assertIn("keytool", r.json()["como_obter"])

    def test_com_impressao_devolve_o_json_no_formato_do_google(self):
        digital = ":".join(["AB"] * 32)
        r = self._recarregar(sha=digital, pacote="br.api.alphaforge.twa").get(
            "/.well-known/assetlinks.json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["content-type"])
        conteudo = json.loads(r.text)
        self.assertEqual(len(conteudo), 1)
        alvo = conteudo[0]["target"]
        self.assertEqual(alvo["namespace"], "android_app")
        self.assertEqual(alvo["package_name"], "br.api.alphaforge.twa")
        self.assertEqual(alvo["sha256_cert_fingerprints"], [digital])
        self.assertEqual(conteudo[0]["relation"],
                         ["delegate_permission/common.handle_all_urls"])

    def test_aceita_mais_de_uma_impressao(self):
        """Chave de upload e chave de assinatura do Play são diferentes: as
        duas precisam estar no arquivo, ou o app assinado pelo Google falha."""
        a, b = ":".join(["AB"] * 32), ":".join(["CD"] * 32)
        r = self._recarregar(sha=f"{a}, {b}").get("/.well-known/assetlinks.json")
        self.assertEqual(
            json.loads(r.text)[0]["target"]["sha256_cert_fingerprints"], [a, b])


class TestAvisoNaInterface(unittest.TestCase):
    """A tela é o lugar onde o aviso precisa estar de verdade."""

    @classmethod
    def setUpClass(cls):
        cls.html = _cliente().get("/").text

    def test_rodape_permanente_nega_recomendacao(self):
        self.assertIn("Não é recomendação de investimento", self.html)
        self.assertIn("Res. CVM 20/2021", self.html)
        self.assertIn("Res. CVM 19/2021", self.html)

    def test_rodape_alerta_sobre_derivativos_e_passado(self):
        self.assertIn("perda superior ao capital aplicado", self.html)
        self.assertIn("Rentabilidade passada não garante", self.html)

    def test_rodape_linka_termos_e_privacidade(self):
        self.assertIn('href="/termos"', self.html)
        self.assertIn('href="/privacidade"', self.html)

    def test_faixa_de_primeira_visita_existe_e_e_dispensavel(self):
        self.assertIn('id="avisoLegal"', self.html)
        self.assertIn("prepararAviso()", self.html)
        self.assertIn("dispensarAviso()", self.html)

    def test_faixa_tolera_navegador_sem_storage(self):
        """localStorage lança em janela privada de alguns navegadores. Se
        lançar, a faixa reaparece — nunca quebra a página."""
        trecho = self.html[self.html.index("function prepararAviso"):
                           self.html.index("function dispensarAviso")]
        self.assertIn("try", trecho)
        self.assertIn("catch", trecho)


if __name__ == "__main__":
    unittest.main()
