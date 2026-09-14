"""Carteira do assinante: validação de ticker, média ponderada e isolamento.

Três coisas aqui não são teste de aritmética, são teste de dano:

  - O merge por média ponderada. Errar isso não quebra a tela — produz um
    preço médio errado que o usuário leva para a declaração de imposto.
  - O isolamento entre contas. Saber o ticker de outra pessoa não pode bastar
    para ler nem apagar a posição dela.
  - O gate premium em CADA rota. Uma rota esquecida entrega a carteira inteira
    a quem não assina, e nenhum teste de UI pegaria isso.
"""

import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from modules import carteira, contas


def _banco_limpo():
    """Banco novo por classe de teste. Estado compartilhado entre testes de
    dinheiro é como se descobre um bug só quando ele já está em produção.

    Zera também o contador de tentativas de login: ele é global por IP, e o
    TestClient usa sempre o mesmo endereço. Sem isto, a décima primeira conta
    criada na suíte leva 429 — e o teste falha por um limite de produção que
    está funcionando certo.
    """
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestValidacaoDeTicker(unittest.TestCase):
    def test_aceita_as_sete_classes_da_b3(self):
        # 5, 6, 7 e 8 estão aqui porque o próprio AlphaForge varre CPLE6,
        # BRSR6 e USIM5. Uma regex que só aceitasse 3, 4 e 11 recusaria o
        # cadastro de papéis que o nosso scanner aprova.
        for ticker in ("PETR3", "PETR4", "USIM5", "CPLE6", "ABCD7", "ABCD8", "TAEE11"):
            self.assertTrue(carteira.FORMA_TICKER.match(ticker), ticker)

    def test_recusa_forma_invalida(self):
        for ticker in ("PETR", "PETR9", "PET4", "PETRO4", "PETR44", "1234", ""):
            self.assertIsNone(carteira.FORMA_TICKER.match(ticker), ticker)

    def test_normaliza_caixa_e_espaco(self):
        self.assertEqual(carteira.normalizar_ticker("  petr4 "), "PETR4")

    def test_ticker_com_forma_invalida_levanta_com_motivo(self):
        with self.assertRaises(carteira.ErroCarteira) as caso:
            carteira.validar("PETR9", 10, 30.0)
        self.assertIn("forma de ticker", str(caso.exception))

    def test_quantidade_e_preco_precisam_ser_positivos(self):
        for quantidade, preco in ((0, 30.0), (-5, 30.0), (10, 0), (10, -1)):
            with self.assertRaises(carteira.ErroCarteira):
                carteira.validar("PETR4", quantidade, preco)

    def test_texto_no_lugar_de_numero_levanta(self):
        with self.assertRaises(carteira.ErroCarteira):
            carteira.validar("PETR4", "muitas", 30.0)

    def test_nan_nao_passa_como_numero(self):
        with self.assertRaises(carteira.ErroCarteira):
            carteira.validar("PETR4", float("nan"), 30.0)

    def test_papel_fora_dos_registros_entra_marcado_em_vez_de_recusado(self):
        # "Não conhecemos" não é "não existe": as nossas bases envelhecem, e
        # recusar papel legítimo por falha nossa é o pior desfecho para quem paga.
        _, _, _, classe, verificado = carteira.validar("ZZZZ3", 10, 5.0)
        self.assertEqual(classe, "desconhecida")
        self.assertFalse(verificado)


class TestMediaPonderada(unittest.TestCase):
    def test_aporte_no_mesmo_preco_mantem_a_media(self):
        self.assertAlmostEqual(carteira.media_ponderada(100, 30.0, 100, 30.0), 30.0)

    def test_aporte_mais_caro_puxa_a_media_proporcionalmente(self):
        # 100 x 30 + 100 x 40 = 7000 / 200 = 35
        self.assertAlmostEqual(carteira.media_ponderada(100, 30.0, 100, 40.0), 35.0)

    def test_peso_da_quantidade_conta(self):
        # 300 x 20 + 100 x 40 = 10000 / 400 = 25 (não 30, que seria a média simples)
        self.assertAlmostEqual(carteira.media_ponderada(300, 20.0, 100, 40.0), 25.0)


class TestPersistencia(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        self.usuario, _ = contas.criar_usuario("dono@teste.com", "senha-boa-123")
        self.outro, _ = contas.criar_usuario("outro@teste.com", "senha-boa-123")

    def test_cria_posicao(self):
        linha, merge = carteira.adicionar(self.usuario["id"], "petr4", 100, 30.0)
        self.assertFalse(merge)
        self.assertEqual(linha["ticker"], "PETR4")
        self.assertEqual(linha["custo_total"], 3000.0)

    def test_segundo_aporte_faz_merge_e_nao_duplica(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)
        linha, merge = carteira.adicionar(self.usuario["id"], "PETR4", 100, 40.0)
        self.assertTrue(merge)
        self.assertEqual(linha["quantidade"], 200.0)
        self.assertAlmostEqual(linha["preco_medio"], 35.0)
        self.assertEqual(carteira.listar(self.usuario["id"])["posicoes_total"], 1)

    def test_listagem_traz_custo_e_peso(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)   # 3000
        carteira.adicionar(self.usuario["id"], "VALE3", 100, 10.0)   # 1000
        dados = carteira.listar(self.usuario["id"])
        self.assertEqual(dados["custo_total"], 4000.0)
        pesos = {p["ticker"]: p["peso_pct"] for p in dados["posicoes"]}
        self.assertAlmostEqual(pesos["PETR4"], 75.0)
        self.assertAlmostEqual(pesos["VALE3"], 25.0)

    def test_atualizar_substitui_em_vez_de_somar(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)
        linha = carteira.atualizar(self.usuario["id"], "PETR4", 50, 20.0)
        self.assertEqual(linha["quantidade"], 50.0)
        self.assertAlmostEqual(linha["preco_medio"], 20.0)

    def test_atualizar_papel_ausente_devolve_none(self):
        self.assertIsNone(carteira.atualizar(self.usuario["id"], "PETR4", 10, 5.0))

    def test_remover(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)
        self.assertTrue(carteira.remover(self.usuario["id"], "PETR4"))
        self.assertFalse(carteira.remover(self.usuario["id"], "PETR4"))

    def test_carteiras_de_usuarios_diferentes_nao_se_misturam(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)
        carteira.adicionar(self.outro["id"], "VALE3", 50, 60.0)
        meus = [p["ticker"] for p in carteira.listar(self.usuario["id"])["posicoes"]]
        self.assertEqual(meus, ["PETR4"])

    def test_nao_da_para_remover_posicao_de_outro_usuario(self):
        carteira.adicionar(self.outro["id"], "VALE3", 50, 60.0)
        self.assertFalse(carteira.remover(self.usuario["id"], "VALE3"))
        self.assertEqual(carteira.listar(self.outro["id"])["posicoes_total"], 1)

    def test_conta_apagada_leva_a_carteira_junto(self):
        carteira.adicionar(self.usuario["id"], "PETR4", 100, 30.0)
        with contas._conectar() as cx:
            cx.execute("PRAGMA foreign_keys = ON")
            cx.execute("DELETE FROM usuarios WHERE id = ?", (self.usuario["id"],))
        self.assertEqual(carteira.listar(self.usuario["id"])["posicoes_total"], 0)


class TestRotas(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        import api
        self.cliente = TestClient(api.app)

    def _entrar(self, email, premium=True):
        self.cliente.post("/conta/registrar", json={"email": email, "senha": "senha-boa-123"})
        usuario = contas.autenticar(email, "senha-boa-123")
        if premium:
            contas.definir_plano(usuario["id"], "premium")
        return usuario

    # GET e DELETE não levam corpo; POST e PATCH levam. Percorrer as quatro
    # rotas com a mesma chamada exigiria mandar JSON num GET, que o TestClient
    # recusa — e com razão.
    ROTAS = (("get", "/api/v1/carteira", None),
             ("post", "/api/v1/carteira/item",
              {"ticker": "PETR4", "quantidade": 1, "preco_medio": 1}),
             ("patch", "/api/v1/carteira/item/PETR4",
              {"ticker": "PETR4", "quantidade": 1, "preco_medio": 1}),
             ("delete", "/api/v1/carteira/item/PETR4", None))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_rotas_de_dado_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_conta_gratuita_recebe_402_em_cada_rota(self):
        self._entrar("free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_fluxo_completo_do_assinante(self):
        self._entrar("vip@teste.com")
        criar = self.cliente.post("/api/v1/carteira/item",
                                  json={"ticker": "petr4", "quantidade": 100, "preco_medio": 30.0})
        self.assertEqual(criar.status_code, 201, criar.text)
        self.assertFalse(criar.json()["aporte_incorporado"])

        aporte = self.cliente.post("/api/v1/carteira/item",
                                   json={"ticker": "PETR4", "quantidade": 100, "preco_medio": 40.0})
        self.assertTrue(aporte.json()["aporte_incorporado"])
        self.assertAlmostEqual(aporte.json()["posicao"]["preco_medio"], 35.0)

        lista = self.cliente.get("/api/v1/carteira").json()
        self.assertEqual(lista["posicoes_total"], 1)
        self.assertEqual(lista["custo_total"], 7000.0)

        editar = self.cliente.patch("/api/v1/carteira/item/PETR4",
                                    json={"ticker": "PETR4", "quantidade": 50, "preco_medio": 20.0})
        self.assertEqual(editar.json()["posicao"]["quantidade"], 50.0)

        self.assertEqual(self.cliente.delete("/api/v1/carteira/item/PETR4").status_code, 200)
        self.assertEqual(self.cliente.get("/api/v1/carteira").json()["posicoes_total"], 0)

    def test_entrada_invalida_devolve_422_com_motivo_legivel(self):
        self._entrar("vip422@teste.com")
        resposta = self.cliente.post("/api/v1/carteira/item",
                                     json={"ticker": "PETR9", "quantidade": 10, "preco_medio": 5})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("forma de ticker", resposta.json()["detail"]["motivo"])

    def test_papel_ausente_devolve_404(self):
        self._entrar("vip404@teste.com")
        self.assertEqual(
            self.cliente.delete("/api/v1/carteira/item/PETR4").status_code, 404)

    def test_login_vip_e_publico(self):
        self.cliente.cookies.clear()
        resposta = self.cliente.get("/vip/login")
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("Console VIP", resposta.text)

    def test_console_sem_sessao_redireciona_para_o_login(self):
        self.cliente.cookies.clear()
        resposta = self.cliente.get("/vip", follow_redirects=False)
        self.assertEqual(resposta.status_code, 303)
        self.assertEqual(resposta.headers["location"], "/vip/login")

    def test_console_com_sessao_gratuita_nao_redireciona(self):
        # Mandar para o login quem já está autenticado é beco sem saída: a
        # pessoa não precisa entrar, precisa assinar.
        self._entrar("freeconsole@teste.com", premium=False)
        resposta = self.cliente.get("/vip", follow_redirects=False)
        self.assertEqual(resposta.status_code, 200)

    def test_terminal_antigo_continua_de_pe(self):
        """Não-regressão: a raiz e o health não podem ter sido afetados."""
        self.assertEqual(self.cliente.get("/health").json()["status"], "ok")
        self.assertEqual(self.cliente.get("/").status_code, 200)


if __name__ == "__main__":
    unittest.main()


# ==========================================================================
# Importação de planilha
# ==========================================================================

class TestLeituraDePlanilha(unittest.TestCase):
    """O parser é o ponto mais frágil do módulo: o arquivo vem de fora, cada
    corretora exporta diferente, e o que ele produz vira posição de verdade."""

    @staticmethod
    def _xlsx(linhas):
        import io
        from openpyxl import Workbook
        livro = Workbook()
        for linha in linhas:
            livro.active.append(linha)
        buffer = io.BytesIO()
        livro.save(buffer)
        return buffer.getvalue()

    def test_planilha_simples(self):
        from modules import importacao
        dados = self._xlsx([["Ticker", "Quantidade", "Preço médio"],
                            ["PETR4", 100, 32.5]])
        linhas = importacao.ler(dados, "carteira.xlsx")
        self.assertEqual(len(linhas), 1)
        self.assertEqual(linhas[0]["ticker"], "PETR4")
        self.assertEqual(linhas[0]["quantidade"], 100)
        self.assertAlmostEqual(linhas[0]["preco_medio"], 32.5)
        self.assertIsNone(linhas[0]["erro"])

    def test_cabecalho_fora_da_primeira_linha(self):
        # Export de corretora vem com título, CNPJ e data antes da tabela.
        from modules import importacao
        dados = self._xlsx([["Posição consolidada"], ["CNPJ 00.000.000/0001-00"], [],
                            ["Papel", "Qtde", "PM"], ["VALE3", 50, 60.0]])
        linhas = importacao.ler(dados, "extrato.xlsx")
        self.assertEqual(linhas[0]["ticker"], "VALE3")

    def test_nomes_de_coluna_alternativos(self):
        from modules import importacao
        for cabecalho in (["Código", "Quantidade", "Preço Médio (R$)"],
                          ["Produto", "Qtd. Disponível", "Preco medio"],
                          ["ATIVO", "QUANT", "CUSTO MEDIO"]):
            dados = self._xlsx([cabecalho, ["ITUB4", 10, 30.0]])
            linhas = importacao.ler(dados, "x.xlsx")
            self.assertEqual(linhas[0]["ticker"], "ITUB4", cabecalho)
            self.assertEqual(linhas[0]["quantidade"], 10, cabecalho)

    def test_ticker_com_descricao_junto(self):
        from modules import importacao
        dados = self._xlsx([["Papel", "Qtde", "PM"],
                            ["PETR4 - PETROBRAS PN N2", 100, 32.5]])
        self.assertEqual(importacao.ler(dados, "x.xlsx")[0]["ticker"], "PETR4")

    def test_fracionario_vira_o_papel_cheio(self):
        # PETR4F é o mesmo ativo que PETR4; separar os dois criaria duas
        # posições do mesmo papel na carteira.
        from modules import importacao
        dados = self._xlsx([["Papel", "Qtde", "PM"], ["PETR4F", 7, 32.5]])
        self.assertEqual(importacao.ler(dados, "x.xlsx")[0]["ticker"], "PETR4")

    def test_numero_no_formato_brasileiro(self):
        from modules import importacao
        dados = self._xlsx([["Papel", "Qtde", "PM"],
                            ["TAEE11", "1.200", "R$ 34,10"]])
        linha = importacao.ler(dados, "x.xlsx")[0]
        self.assertEqual(linha["quantidade"], 1200)
        self.assertAlmostEqual(linha["preco_medio"], 34.10)

    def test_linha_ruim_nao_derruba_o_arquivo(self):
        from modules import importacao
        dados = self._xlsx([["Papel", "Qtde", "PM"],
                            ["PETR4", 100, 32.5],
                            ["XXXX9", 10, 5.0],      # forma inválida
                            ["VALE3", "-", 60.0],    # quantidade ilegível
                            ["ITUB4", 20, 30.0]])
        linhas = importacao.ler(dados, "x.xlsx")
        self.assertEqual(len(linhas), 4)
        self.assertEqual([l["erro"] is None for l in linhas], [True, False, False, True])

    def test_linha_de_total_sem_ticker_e_ignorada_em_silencio(self):
        from modules import importacao
        dados = self._xlsx([["Papel", "Qtde", "PM"], ["PETR4", 100, 32.5],
                            ["", 100, ""], ["TOTAL", "", 3250.0]])
        linhas = importacao.ler(dados, "x.xlsx")
        self.assertEqual(len(linhas), 1)

    def test_planilha_sem_preco_avisa_em_vez_de_inventar(self):
        from modules import importacao
        dados = self._xlsx([["Papel", "Quantidade"], ["PETR4", 100]])
        linha = importacao.ler(dados, "x.xlsx")[0]
        self.assertIsNotNone(linha["erro"])
        self.assertIn("preço", linha["erro"].lower())

    def test_csv_com_ponto_e_virgula(self):
        from modules import importacao
        conteudo = "Papel;Quantidade;Preço médio\nPETR4;100;32,50\n".encode("utf-8")
        linha = importacao.ler(conteudo, "carteira.csv")[0]
        self.assertEqual(linha["ticker"], "PETR4")
        self.assertAlmostEqual(linha["preco_medio"], 32.50)

    def test_csv_em_latin1_nao_quebra(self):
        from modules import importacao
        conteudo = "Papel;Quantidade;Preço médio\nVALE3;10;60,00\n".encode("latin-1")
        self.assertEqual(importacao.ler(conteudo, "x.csv")[0]["ticker"], "VALE3")

    def test_arquivo_sem_cabecalho_reconhecivel_levanta(self):
        from modules import importacao
        dados = self._xlsx([["Coluna A", "Coluna B"], ["xxx", "yyy"]])
        with self.assertRaises(importacao.ErroImportacao):
            importacao.ler(dados, "x.xlsx")

    def test_extensao_nao_aceita_levanta(self):
        from modules import importacao
        with self.assertRaises(importacao.ErroImportacao):
            importacao.ler(b"qualquer", "carteira.pdf")

    def test_arquivo_grande_demais_levanta(self):
        from modules import importacao
        with self.assertRaises(importacao.ErroImportacao):
            importacao.ler(b"x" * (importacao.MAXIMO_BYTES + 1), "x.csv")

    def test_planilha_modelo_e_lida_pelo_proprio_parser(self):
        # O modelo que entregamos precisa passar na nossa própria leitura —
        # senão estaríamos ensinando um formato que não aceitamos.
        from modules import importacao
        linhas = importacao.ler(importacao.planilha_modelo(), "modelo.xlsx")
        self.assertTrue(linhas)
        self.assertTrue(all(l["erro"] is None for l in linhas), linhas)


class TestRotasDeImportacao(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        import api
        self.cliente = TestClient(api.app)
        self.cliente.post("/conta/registrar",
                          json={"email": "imp@teste.com", "senha": "senha-boa-123"})
        contas.definir_plano(contas.autenticar("imp@teste.com", "senha-boa-123")["id"],
                             "premium")

    def _enviar(self, linhas, nome="carteira.xlsx"):
        dados = TestLeituraDePlanilha._xlsx(linhas)
        return self.cliente.post("/api/v1/carteira/importar/previa",
                                 files={"arquivo": (nome, dados, "application/vnd.ms-excel")})

    def test_previa_nao_grava_nada(self):
        resposta = self._enviar([["Papel", "Qtde", "PM"], ["PETR4", 100, 32.5]])
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["validas"], 1)
        # O ponto inteiro da prévia: a carteira continua vazia.
        self.assertEqual(self.cliente.get("/api/v1/carteira").json()["posicoes_total"], 0)

    def test_previa_diz_o_que_e_novo_e_o_que_ja_existe(self):
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 50, "preco_medio": 20.0})
        corpo = self._enviar([["Papel", "Qtde", "PM"],
                              ["PETR4", 100, 32.5], ["VALE3", 10, 60.0]]).json()
        self.assertEqual(corpo["existentes"], 1)
        self.assertEqual(corpo["novos"], 1)
        petr = next(l for l in corpo["linhas"] if l["ticker"] == "PETR4")
        self.assertEqual(petr["acao"], "atualiza")
        self.assertEqual(petr["quantidade_atual"], 50.0)

    def test_importar_substituindo_usa_a_planilha_como_verdade(self):
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 50, "preco_medio": 20.0})
        resposta = self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "substituir",
            "linhas": [{"ticker": "PETR4", "quantidade": 100, "preco_medio": 32.5}]})
        self.assertEqual(resposta.status_code, 200)
        posicao = self.cliente.get("/api/v1/carteira").json()["posicoes"][0]
        self.assertEqual(posicao["quantidade"], 100.0)
        self.assertAlmostEqual(posicao["preco_medio"], 32.5)

    def test_importar_somando_faz_media_ponderada(self):
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0})
        self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "somar",
            "linhas": [{"ticker": "PETR4", "quantidade": 100, "preco_medio": 40.0}]})
        posicao = self.cliente.get("/api/v1/carteira").json()["posicoes"][0]
        self.assertEqual(posicao["quantidade"], 200.0)
        self.assertAlmostEqual(posicao["preco_medio"], 35.0)

    def test_substituir_cria_papel_que_ainda_nao_existe(self):
        self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "substituir",
            "linhas": [{"ticker": "VALE3", "quantidade": 10, "preco_medio": 60.0}]})
        self.assertEqual(self.cliente.get("/api/v1/carteira").json()["posicoes_total"], 1)

    def test_linha_ruim_no_lote_nao_impede_as_boas(self):
        resposta = self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "substituir",
            "linhas": [{"ticker": "PETR4", "quantidade": 100, "preco_medio": 32.5},
                       {"ticker": "XXXX9", "quantidade": 10, "preco_medio": 5.0}]})
        corpo = resposta.json()
        self.assertEqual(corpo["gravadas"], 1)
        self.assertEqual(len(corpo["recusadas"]), 1)

    def test_modo_invalido_e_recusado(self):
        resposta = self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "apagar_tudo",
            "linhas": [{"ticker": "PETR4", "quantidade": 1, "preco_medio": 1}]})
        self.assertEqual(resposta.status_code, 422)

    def test_planilha_invalida_devolve_422_com_motivo(self):
        resposta = self.cliente.post(
            "/api/v1/carteira/importar/previa",
            files={"arquivo": ("x.pdf", b"conteudo", "application/pdf")})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("Formato", resposta.json()["detail"]["motivo"])

    def test_modelo_sai_como_xlsx(self):
        resposta = self.cliente.get("/api/v1/carteira/modelo")
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.content.startswith(b"PK"))  # zip = xlsx

    def test_rotas_de_importacao_exigem_premium(self):
        self.cliente.post("/conta/sair")
        self.cliente.post("/conta/registrar",
                          json={"email": "free-imp@teste.com", "senha": "senha-boa-123"})
        self.assertEqual(self.cliente.get("/api/v1/carteira/modelo").status_code, 402)
        self.assertEqual(self.cliente.post("/api/v1/carteira/importar", json={
            "modo": "substituir",
            "linhas": [{"ticker": "PETR4", "quantidade": 1, "preco_medio": 1}]}).status_code, 402)
        self.assertEqual(self._enviar([["Papel", "Qtde", "PM"], ["PETR4", 1, 1]]).status_code, 402)
