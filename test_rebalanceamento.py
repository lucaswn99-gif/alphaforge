"""Testes do Pilar 3: alvo por classe e rebalanceamento só por aporte.

O motor é função pura, então tudo aqui é aritmética verificável à mão — que é
o ponto: é a conta que decide onde alguém põe dinheiro.

    python -m unittest test_rebalanceamento -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import alvos, rebalanceamento  # noqa: E402


def _posicao(ticker, quantidade, preco_medio, classe):
    return {"ticker": ticker, "quantidade": quantidade,
            "preco_medio": preco_medio, "classe": classe,
            "custo_total": round(quantidade * preco_medio, 2)}


class TestValidacaoDeAlvo(unittest.TestCase):
    def test_alvo_completo_passa(self):
        self.assertEqual(alvos.validar({"acao": 60, "fii": 30, "etf": 10}),
                         {"acao": 60.0, "fii": 30.0, "etf": 10.0})

    def test_classe_ausente_vale_zero(self):
        """Quem não quer ETF não deveria ter que digitar 'etf: 0'."""
        self.assertEqual(alvos.validar({"acao": 70, "fii": 30}),
                         {"acao": 70.0, "fii": 30.0, "etf": 0.0})

    def test_soma_diferente_de_cem_e_recusada(self):
        """Alvo que soma 90 deixaria 10% do aporte sem destino declarado, e o
        motor teria que inventar para onde mandar."""
        with self.assertRaises(alvos.ErroAlvo) as erro:
            alvos.validar({"acao": 60, "fii": 30})
        self.assertIn("90", str(erro.exception))

    def test_tolerancia_de_arredondamento(self):
        alvos.validar({"acao": 33.33, "fii": 33.33, "etf": 33.34})

    def test_negativo_e_recusado(self):
        with self.assertRaises(alvos.ErroAlvo):
            alvos.validar({"acao": 110, "fii": -10})

    def test_classe_inexistente_e_recusada(self):
        with self.assertRaises(alvos.ErroAlvo) as erro:
            alvos.validar({"acao": 50, "cripto": 50})
        self.assertIn("cripto", str(erro.exception))

    def test_texto_nao_numerico_e_recusado(self):
        with self.assertRaises(alvos.ErroAlvo):
            alvos.validar({"acao": "sessenta", "fii": 40})


class TestDistribuicaoEntreClasses(unittest.TestCase):
    """Carteira de R$ 10.000: 7.000 em ação, 3.000 em FII, nada em ETF."""

    POSICOES = [
        _posicao("PETR4", 200, 35.0, "acao"),      # 7.000
        _posicao("HGLG11", 20, 150.0, "fii"),      # 3.000
    ]
    PRECOS = {"PETR4": 35.0, "HGLG11": 150.0}

    def test_aporte_vai_para_quem_esta_abaixo_do_alvo(self):
        """Alvo 50/50 sobre 12.000 = 6.000 cada. Ação já tem 7.000 (acima), FII
        tem 3.000 e precisa de 3.000. Todo o aporte vai para o FII."""
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 50, "fii": 50, "etf": 0}, 2000)
        por_classe = {c["classe"]: c for c in plano["classes"]}
        self.assertEqual(por_classe["acao"]["aporte"], 0.0)
        self.assertGreater(por_classe["fii"]["aporte"], 0.0)

    def test_classe_acima_do_alvo_nao_vende(self):
        """A ação está sobrealocada e o motor não devolve ordem negativa: o
        próximo aporte corrige de graça o que a venda corrigiria pagando
        imposto e corretagem."""
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 50, "fii": 50, "etf": 0}, 2000)
        self.assertTrue(all(o["quantidade"] > 0 for o in plano["ordens"]))
        por_classe = {c["classe"]: c for c in plano["classes"]}
        self.assertGreater(por_classe["acao"]["excesso"], 0.0)

    def test_deficit_proporcional_quando_o_aporte_nao_cobre_tudo(self):
        """Alvo 40/40/20 sobre 11.000: ação 4.400 (tem 7.000, zero déficit),
        FII 4.400 (tem 3.000, déficit 1.400), ETF 2.200 (tem 0, déficit 2.200).
        Déficit total 3.600 > aporte 1.000, então divide na proporção."""
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 40, "fii": 40, "etf": 20}, 1000)
        por_classe = {c["classe"]: c for c in plano["classes"]}
        self.assertEqual(por_classe["acao"]["deficit"], 0.0)
        self.assertAlmostEqual(por_classe["fii"]["deficit"], 1400.0, places=0)
        self.assertAlmostEqual(por_classe["etf"]["deficit"], 2200.0, places=0)

    def test_aporte_maior_que_o_deficit_distribui_o_resto_pelo_alvo(self):
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 50, "fii": 50, "etf": 0}, 50000)
        por_classe = {c["classe"]: c for c in plano["classes"]}
        # Com aporte muito maior que o déficit, as duas classes convergem para
        # perto do alvo.
        self.assertAlmostEqual(por_classe["acao"]["peso_depois_pct"], 50.0, delta=2.0)
        self.assertAlmostEqual(por_classe["fii"]["peso_depois_pct"], 50.0, delta=2.0)

    def test_classe_sem_posicao_nao_vira_ordem_e_diz_por_que(self):
        """Não dá para comprar 'ETF' — é preciso um papel. O dinheiro fica
        declarado como não alocado, com o motivo e o que fazer."""
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 40, "fii": 40, "etf": 20}, 1000)
        etf = [n for n in plano["nao_alocado"] if n["classe"] == "etf"]
        self.assertTrue(etf)
        self.assertIn("Cadastre", etf[0]["motivo"])


class TestOrdensEmAcoesInteiras(unittest.TestCase):
    def test_quantidade_e_inteira_e_arredonda_para_baixo(self):
        plano = rebalanceamento.planejar(
            [_posicao("BBAS3", 10, 50.0, "acao")], {"BBAS3": 30.0},
            {"acao": 100}, 100)
        ordem = plano["ordens"][0]
        self.assertEqual(ordem["quantidade"], 3)      # 100 / 30 = 3,33
        self.assertEqual(ordem["valor"], 90.0)
        self.assertIsInstance(ordem["quantidade"], int)

    def test_aporte_menor_que_uma_acao_nao_gera_ordem(self):
        """Nada de 'compre 0,4 de BBAS3'. A sobra volta declarada."""
        plano = rebalanceamento.planejar(
            [_posicao("BBAS3", 10, 50.0, "acao")], {"BBAS3": 30.0},
            {"acao": 100}, 12)
        self.assertEqual(plano["ordens"], [])
        self.assertEqual(plano["sobra"], 12.0)

    def test_o_troco_de_varias_ordens_vira_mais_uma_compra(self):
        """Dois papéis de R$ 30 com R$ 100: a divisão dá 50 para cada, que
        compra 1 ação e deixa 20 de cada lado. Os dois trocos somam 40 e
        compram mais uma ação — dinheiro parado por arredondamento não é
        decisão de ninguém."""
        plano = rebalanceamento.planejar(
            [_posicao("AAAA3", 10, 30.0, "acao"), _posicao("BBBB3", 10, 30.0, "acao")],
            {"AAAA3": 30.0, "BBBB3": 30.0}, {"acao": 100}, 100)
        total = sum(o["quantidade"] for o in plano["ordens"])
        self.assertEqual(total, 3)
        self.assertEqual(plano["sobra"], 10.0)

    def test_a_sobra_nunca_estoura_o_aporte(self):
        plano = rebalanceamento.planejar(
            [_posicao("AAAA3", 10, 30.0, "acao"), _posicao("BBBB3", 5, 70.0, "acao")],
            {"AAAA3": 30.0, "BBBB3": 70.0}, {"acao": 100}, 1000)
        gasto = sum(o["valor"] for o in plano["ordens"])
        self.assertLessEqual(gasto, 1000.0 + 1e-6)
        self.assertAlmostEqual(gasto + plano["sobra"], 1000.0, places=2)


class TestBloqueioPeloDiagnostico(unittest.TestCase):
    POSICOES = [
        _posicao("BOA3", 100, 10.0, "acao"),
        _posicao("RUIM3", 100, 10.0, "acao"),
    ]
    PRECOS = {"BOA3": 10.0, "RUIM3": 10.0}

    def test_posicao_desconforme_nao_recebe_aporte(self):
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 100}, 1000,
            bloqueados={"RUIM3"})
        tickers = {o["ticker"] for o in plano["ordens"]}
        self.assertEqual(tickers, {"BOA3"})
        self.assertEqual(plano["bloqueados"], ["RUIM3"])

    def test_o_valor_bloqueado_continua_contando_no_peso(self):
        """O dinheiro está lá: ignorá-lo no peso faria a classe parecer menor
        do que é e puxaria aporte a mais para ela."""
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 100}, 0, bloqueados={"RUIM3"})
        self.assertEqual(plano["valor_atual"], 2000.0)

    def test_classe_inteira_bloqueada_declara_o_motivo(self):
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 100}, 1000,
            bloqueados={"BOA3", "RUIM3"})
        self.assertEqual(plano["ordens"], [])
        self.assertTrue(plano["nao_alocado"])
        self.assertIn("desconformidade", plano["nao_alocado"][0]["motivo"])

    def test_a_fatia_do_bloqueado_vai_para_os_outros_da_classe(self):
        plano = rebalanceamento.planejar(
            self.POSICOES, self.PRECOS, {"acao": 100}, 1000,
            bloqueados={"RUIM3"})
        self.assertEqual(sum(o["valor"] for o in plano["ordens"]), 1000.0)


class TestValorDeMercadoEnaoCusto(unittest.TestCase):
    def test_o_peso_usa_a_cotacao_de_hoje(self):
        """Comprou a 10, hoje vale 20: a exposição é 2.000, não 1.000. Peso
        sobre custo diria onde o dinheiro foi posto no passado."""
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao")], {"PETR4": 20.0},
            {"acao": 100}, 0)
        self.assertEqual(plano["valor_atual"], 2000.0)

    def test_sem_cotacao_entra_pelo_custo_e_fica_marcada(self):
        """Tirar do denominador distorceria o peso das outras classes; entrar
        calada pelo custo misturaria duas bases sem avisar."""
        plano = rebalanceamento.planejar(
            [_posicao("XPTO3", 100, 10.0, "acao")], {}, {"acao": 100}, 500)
        self.assertEqual(plano["valor_atual"], 1000.0)
        self.assertEqual(plano["posicoes_sem_preco"], ["XPTO3"])

    def test_posicao_sem_cotacao_nao_recebe_ordem(self):
        """Sem preço não há como calcular quantas ações comprar."""
        plano = rebalanceamento.planejar(
            [_posicao("XPTO3", 100, 10.0, "acao")], {}, {"acao": 100}, 500)
        self.assertEqual(plano["ordens"], [])
        self.assertTrue(plano["nao_alocado"])
        self.assertIn("cotação", plano["nao_alocado"][0]["motivo"])


class TestClasseSemAlvo(unittest.TestCase):
    def test_desconhecida_fica_fora_do_denominador(self):
        """Manter no denominador criaria um excesso permanente que nenhum
        aporte corrige, e o alvo nunca fecharia."""
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao"),
             _posicao("ZZZZ11", 100, 10.0, "desconhecida")],
            {"PETR4": 10.0, "ZZZZ11": 10.0}, {"acao": 100}, 0)
        self.assertEqual(plano["valor_atual"], 1000.0)
        self.assertEqual(plano["valor_fora_do_alvo"], 1000.0)
        self.assertEqual([l["ticker"] for l in plano["fora_do_alvo"]], ["ZZZZ11"])

    def test_desconhecida_nunca_recebe_ordem(self):
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao"),
             _posicao("ZZZZ11", 100, 10.0, "desconhecida")],
            {"PETR4": 10.0, "ZZZZ11": 10.0}, {"acao": 100}, 500)
        self.assertEqual({o["ticker"] for o in plano["ordens"]}, {"PETR4"})


class TestBordas(unittest.TestCase):
    def test_carteira_vazia_nao_estoura(self):
        plano = rebalanceamento.planejar([], {}, {"acao": 100}, 1000)
        self.assertEqual(plano["ordens"], [])
        self.assertEqual(plano["valor_atual"], 0.0)
        self.assertTrue(plano["nao_alocado"])

    def test_aporte_zero_devolve_o_retrato_sem_ordens(self):
        """Serve para a tela mostrar o desvio atual antes de o usuário digitar
        qualquer valor."""
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao")], {"PETR4": 10.0},
            {"acao": 60, "fii": 40}, 0)
        self.assertEqual(plano["ordens"], [])
        por_classe = {c["classe"]: c for c in plano["classes"]}
        self.assertEqual(por_classe["acao"]["peso_atual_pct"], 100.0)
        self.assertEqual(por_classe["fii"]["peso_atual_pct"], 0.0)

    def test_aporte_negativo_vira_zero(self):
        """Aporte negativo seria venda, e este motor não vende."""
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao")], {"PETR4": 10.0},
            {"acao": 100}, -5000)
        self.assertEqual(plano["aporte"], 0.0)
        self.assertEqual(plano["ordens"], [])

    def test_aporte_nao_numerico_vira_zero(self):
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao")], {"PETR4": 10.0},
            {"acao": 100}, "muito")
        self.assertEqual(plano["aporte"], 0.0)

    def test_preco_zero_e_tratado_como_ausente(self):
        plano = rebalanceamento.planejar(
            [_posicao("PETR4", 100, 10.0, "acao")], {"PETR4": 0.0},
            {"acao": 100}, 500)
        self.assertEqual(plano["posicoes_sem_preco"], ["PETR4"])
        self.assertEqual(plano["ordens"], [])



# ---------------------------------------------------------------- persistência

import os as _os            # noqa: E402
import tempfile             # noqa: E402

from fastapi.testclient import TestClient   # noqa: E402

from modules import contas  # noqa: E402


def _banco_limpo():
    contas.CAMINHO_BANCO = _os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestPersistenciaDoAlvo(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        usuario, _ = contas.criar_usuario("alvo@teste.com", "senha-boa-123")
        self.usuario = usuario["id"]

    def test_sem_alvo_definido_devolve_none(self):
        """None e 'tudo zero' são coisas diferentes: a primeira é não
        configurado, a segunda seria um alvo que `validar` nem aceita."""
        self.assertIsNone(alvos.obter(self.usuario))

    def test_grava_e_le(self):
        alvos.definir(self.usuario, {"acao": 60, "fii": 30, "etf": 10})
        self.assertEqual(alvos.obter(self.usuario),
                         {"acao": 60.0, "fii": 30.0, "etf": 10.0})

    def test_redefinir_substitui_o_conjunto_inteiro(self):
        """Alvo é um retrato que soma 100, não campos independentes: gravar
        classe a classe deixaria o banco passar por estados que somam 140."""
        alvos.definir(self.usuario, {"acao": 60, "fii": 30, "etf": 10})
        alvos.definir(self.usuario, {"acao": 100})
        self.assertEqual(alvos.obter(self.usuario),
                         {"acao": 100.0, "fii": 0.0, "etf": 0.0})

    def test_alvo_de_um_usuario_nao_vaza_para_o_outro(self):
        criado, _ = contas.criar_usuario("outro@teste.com", "senha-boa-123")
        outro = criado["id"]
        alvos.definir(self.usuario, {"acao": 100})
        self.assertIsNone(alvos.obter(outro))

    def test_alvo_invalido_nao_chega_a_gravar(self):
        with self.assertRaises(alvos.ErroAlvo):
            alvos.definir(self.usuario, {"acao": 60, "fii": 30})
        self.assertIsNone(alvos.obter(self.usuario))


class TestRotasDoRebalanceamento(unittest.TestCase):
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

    ROTAS = (("get", "/api/v1/carteira/alvos", None),
             ("put", "/api/v1/carteira/alvos", {"alvos": {"acao": 100}}),
             ("get", "/api/v1/carteira/rebalanceamento", None))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_as_rotas_novas_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_as_rotas_novas_exigem_assinatura(self):
        """Uma rota esquecida entrega a camada de portfólio a quem não assina,
        e nenhum teste de interface pegaria isso."""
        self._entrar("free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_alvo_comeca_indefinido(self):
        self._entrar("vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/alvos").json()
        self.assertFalse(corpo["definido"])
        self.assertIsNone(corpo["alvos"])
        self.assertEqual(corpo["classes"], ["acao", "fii", "etf"])

    def test_define_e_le_de_volta(self):
        self._entrar("vip2@teste.com")
        gravar = self.cliente.put("/api/v1/carteira/alvos",
                                  json={"alvos": {"acao": 70, "fii": 30}})
        self.assertEqual(gravar.status_code, 200)
        self.assertEqual(gravar.json()["alvos"]["acao"], 70.0)
        self.assertTrue(self.cliente.get("/api/v1/carteira/alvos").json()["definido"])

    def test_alvo_que_nao_soma_cem_volta_422_com_motivo(self):
        self._entrar("vip3@teste.com")
        resposta = self.cliente.put("/api/v1/carteira/alvos",
                                    json={"alvos": {"acao": 60, "fii": 30}})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("100%", resposta.json()["detail"]["motivo"])

    def test_rebalancear_sem_alvo_recusa_e_explica(self):
        """Inventar uma alocação para o dinheiro de alguém é recomendação, não
        configuração — a rota diz isso em vez de escolher um padrão."""
        self._entrar("vip4@teste.com")
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 100,
                                "preco_medio": 30.0})
        resposta = self.cliente.get("/api/v1/carteira/rebalanceamento?aporte=1000")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.json()["detail"]["erro"], "alvo_nao_definido")

    def test_rebalancear_com_carteira_vazia_recusa(self):
        self._entrar("vip5@teste.com")
        self.cliente.put("/api/v1/carteira/alvos", json={"alvos": {"acao": 100}})
        resposta = self.cliente.get("/api/v1/carteira/rebalanceamento?aporte=1000")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.json()["detail"]["erro"], "carteira_vazia")


if __name__ == "__main__":
    unittest.main(verbosity=2)
