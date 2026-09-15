"""Pilar 4: sensibilidade a juro e queda em evento de cauda.

Os dois motores são função pura, então tudo aqui é aritmética verificável à
mão. O risco desta camada não é errar uma divisão — é produzir um número que
parece previsão e é sensibilidade, ou exagerar a queda de uma carteira por
somar quedas que não aconteceram no mesmo dia.

    python -m unittest test_estresse -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import estresse  # noqa: E402


def _balanco(divida_curto=0.0, divida_longo=0.0, caixa=0.0, ebit=None, ano=2025):
    return {"divida_curto_prazo": divida_curto, "divida_longo_prazo": divida_longo,
            "caixa": caixa, "ebit": ebit, "ano": ano}


def _posicao(ticker, valor=1000.0, classe="acao"):
    return {"ticker": ticker, "classe": classe, "valor_atual": valor,
            "custo_total": valor}


class TestDividaLiquida(unittest.TestCase):
    def test_soma_curto_e_longo_e_desconta_caixa(self):
        self.assertEqual(
            estresse.divida_liquida(_balanco(100.0, 400.0, caixa=150.0)), 350.0)

    def test_sem_conta_de_divida_e_nao_apurado_nao_zero(self):
        """Ausência de dívida publicada é desconhecimento; dívida zero é
        informação. Devolver 0.0 aqui transformaria um em outro."""
        self.assertIsNone(estresse.divida_liquida({"caixa": 100.0, "ebit": 50.0}))

    def test_uma_das_duas_contas_basta(self):
        self.assertEqual(estresse.divida_liquida({"divida_longo_prazo": 500.0}), 500.0)

    def test_caixa_maior_que_divida_da_negativo(self):
        self.assertEqual(
            estresse.divida_liquida(_balanco(0.0, 100.0, caixa=400.0)), -300.0)

    def test_balanco_vazio(self):
        self.assertIsNone(estresse.divida_liquida({}))
        self.assertIsNone(estresse.divida_liquida(None))


class TestSensibilidadeSelic(unittest.TestCase):
    """Dívida líquida 1.000, EBIT 200. A 10% a despesa é 100 e a cobertura 2x;
    a 20% a despesa é 200 e a cobertura cai para 1x."""

    POSICOES = [_posicao("ALAV3")]
    BALANCOS = {"ALAV3": _balanco(200.0, 800.0, caixa=0.0, ebit=200.0)}

    def _rodar(self, taxa_nova, taxa_atual=10.0):
        return estresse.sensibilidade_selic(
            self.POSICOES, self.BALANCOS, taxa_atual, taxa_nova)

    def test_despesa_e_cobertura_na_taxa_atual(self):
        linha = self._rodar(10.0)["posicoes"][0]
        self.assertEqual(linha["divida_liquida"], 1000.0)
        self.assertEqual(linha["despesa_atual"], 100.0)
        self.assertEqual(linha["cobertura_atual"], 2.0)

    def test_juro_maior_derruba_a_cobertura(self):
        linha = self._rodar(20.0)["posicoes"][0]
        self.assertEqual(linha["despesa_nova"], 200.0)
        self.assertEqual(linha["cobertura_nova"], 1.0)
        self.assertEqual(linha["variacao_despesa"], 100.0)

    def test_cobertura_abaixo_de_um_e_critica(self):
        """Abaixo de 1x o resultado operacional não paga o serviço da dívida."""
        saida = self._rodar(25.0)
        linha = saida["posicoes"][0]
        self.assertEqual(linha["estado"], "critico")
        self.assertIn("não paga o serviço", linha["motivo"])
        self.assertEqual(saida["criticas"], 1)
        self.assertEqual(saida["pct_critico"], 100.0)

    def test_folga_grande_nao_e_alerta(self):
        saida = self._rodar(2.0)
        self.assertEqual(saida["posicoes"][0]["estado"], "folgado")
        self.assertEqual(saida["pct_critico"], 0.0)

    def test_faixa_intermediaria_e_apertado(self):
        linha = self._rodar(15.0)["posicoes"][0]      # cobertura 1,33x
        self.assertEqual(linha["estado"], "apertado")

    def test_caixa_liquido_inverte_o_sinal(self):
        """Empresa com mais caixa que dívida GANHA com juro alto. Tratar como
        risco zero perderia metade da informação."""
        saida = estresse.sensibilidade_selic(
            [_posicao("CAIX3")],
            {"CAIX3": _balanco(0.0, 100.0, caixa=900.0, ebit=50.0)}, 10.0, 25.0)
        linha = saida["posicoes"][0]
        self.assertTrue(linha["caixa_liquido"])
        self.assertEqual(linha["estado"], "beneficiado")
        self.assertIn("receita financeira", linha["motivo"])
        self.assertEqual(saida["pct_beneficiado"], 100.0)

    def test_sem_ebit_e_nao_apurado(self):
        saida = estresse.sensibilidade_selic(
            [_posicao("SEM3")], {"SEM3": _balanco(500.0, 500.0, ebit=None)},
            10.0, 20.0)
        linha = saida["posicoes"][0]
        self.assertEqual(linha["estado"], "nao_apurado")
        self.assertIsNone(linha["cobertura_nova"])
        self.assertEqual(saida["pct_apurado"], 0.0)

    def test_sem_balanco_nenhum_e_nao_apurado(self):
        saida = estresse.sensibilidade_selic([_posicao("XPTO3")], {}, 10.0, 20.0)
        self.assertEqual(saida["posicoes"][0]["estado"], "nao_apurado")

    def test_fii_e_etf_ficam_de_fora(self):
        """Dívida de FII não está no balanço de companhia, e ETF não tem."""
        saida = estresse.sensibilidade_selic(
            [_posicao("HGLG11", classe="fii"), _posicao("BOVA11", classe="etf"),
             _posicao("ALAV3")],
            self.BALANCOS, 10.0, 20.0)
        self.assertEqual([l["ticker"] for l in saida["posicoes"]], ["ALAV3"])

    def test_peso_critico_e_sobre_valor_nao_sobre_contagem(self):
        """Uma posição crítica valendo 90% da carteira e três folgadas valendo
        10% não é 'uma de quatro' — é 90% do dinheiro."""
        saida = estresse.sensibilidade_selic(
            [_posicao("ALAV3", valor=9000.0), _posicao("BOA3", valor=1000.0)],
            {"ALAV3": _balanco(200.0, 800.0, ebit=200.0),
             "BOA3": _balanco(10.0, 10.0, ebit=500.0)},
            10.0, 25.0)
        self.assertEqual(saida["criticas"], 1)
        self.assertEqual(saida["pct_critico"], 90.0)

    def test_a_ordem_poe_o_critico_primeiro(self):
        saida = estresse.sensibilidade_selic(
            [_posicao("BOA3", valor=5000.0), _posicao("ALAV3", valor=1000.0)],
            {"ALAV3": _balanco(200.0, 800.0, ebit=200.0),
             "BOA3": _balanco(10.0, 10.0, ebit=500.0)},
            10.0, 25.0)
        self.assertEqual(saida["posicoes"][0]["ticker"], "ALAV3")

    def test_o_limite_do_metodo_viaja_junto(self):
        """O número é sensibilidade, não previsão, e a tela precisa dizer isso
        sem depender de alguém lembrar de escrever."""
        saida = self._rodar(20.0)
        self.assertIn("LIMITE SUPERIOR", saida["limite"])
        self.assertIn("indexador", saida["limite"])

    def test_carteira_sem_acao_nao_estoura(self):
        saida = estresse.sensibilidade_selic(
            [_posicao("HGLG11", classe="fii")], {}, 10.0, 20.0)
        self.assertEqual(saida["posicoes"], [])
        self.assertEqual(saida["pct_critico"], 0.0)


class TestDrawdownMaximo(unittest.TestCase):
    def test_queda_de_topo_a_fundo(self):
        self.assertEqual(estresse.drawdown_maximo([100, 120, 60, 90]), -50.0)

    def test_topo_posterior_conta(self):
        """O topo é o máximo ATÉ o momento, não o primeiro valor: quem subiu e
        depois caiu perdeu do topo novo."""
        self.assertEqual(estresse.drawdown_maximo([100, 200, 100]), -50.0)

    def test_serie_so_de_alta_nao_tem_queda(self):
        self.assertEqual(estresse.drawdown_maximo([10, 20, 30]), 0.0)

    def test_serie_curta_demais(self):
        self.assertIsNone(estresse.drawdown_maximo([100]))
        self.assertIsNone(estresse.drawdown_maximo([]))
        self.assertIsNone(estresse.drawdown_maximo(None))

    def test_none_no_meio_da_serie_e_ignorado(self):
        self.assertEqual(estresse.drawdown_maximo([100, None, 50]), -50.0)


class TestEstresseHistorico(unittest.TestCase):
    def test_a_queda_e_da_carteira_nao_a_media_das_quedas(self):
        """Dois papéis com 50% cada: um cai 50% no meio e recupera, o outro cai
        50% no fim. Cada um teve -50%, mas a carteira nunca esteve 50% abaixo
        do topo — somar as quedas individuais inventaria uma perda que não
        aconteceu."""
        series = {"AAA3": [100, 50, 100], "BBB3": [100, 100, 50]}
        pesos = {"AAA3": 1000.0, "BBB3": 1000.0}
        saida = estresse.estresse_historico(series, pesos)
        self.assertEqual(saida["drawdown_pct"], -25.0)
        self.assertEqual(estresse.drawdown_maximo(series["AAA3"]), -50.0)

    def test_pesos_diferentes_mudam_a_queda(self):
        series = {"GRANDE3": [100, 50], "PEQUENO3": [100, 100]}
        saida = estresse.estresse_historico(
            series, {"GRANDE3": 9000.0, "PEQUENO3": 1000.0})
        self.assertEqual(saida["drawdown_pct"], -45.0)

    def test_aponta_o_pior_papel(self):
        saida = estresse.estresse_historico(
            {"AAA3": [100, 90], "BBB3": [100, 40]},
            {"AAA3": 1000.0, "BBB3": 1000.0})
        self.assertEqual(saida["pior_papel"]["ticker"], "BBB3")
        self.assertEqual(saida["pior_papel"]["drawdown_pct"], -60.0)

    def test_papel_sem_historico_fica_de_fora_e_e_declarado(self):
        """Empresa que listou em 2021 não tem 2008. Incluí-la como se tivesse
        ficado parada diluiria a queda."""
        saida = estresse.estresse_historico(
            {"VELHO3": [100, 50], "NOVO3": []},
            {"VELHO3": 5000.0, "NOVO3": 5000.0})
        self.assertEqual(saida["drawdown_pct"], -50.0)
        self.assertEqual(saida["sem_historico"], ["NOVO3"])
        self.assertEqual(saida["cobertura_pct"], 50.0)
        self.assertEqual(saida["com_historico"], 1)

    def test_nenhum_papel_com_historico_nao_inventa_numero(self):
        saida = estresse.estresse_historico({"NOVO3": []}, {"NOVO3": 1000.0})
        self.assertIsNone(saida["drawdown_pct"])
        self.assertEqual(saida["cobertura_pct"], 0.0)
        self.assertIn("histórico", saida["motivo"])

    def test_series_de_tamanhos_diferentes_usam_o_menor(self):
        """Preencher a série curta repetindo o último valor criaria um platô
        que não existiu."""
        saida = estresse.estresse_historico(
            {"LONGO3": [100, 80, 60, 40], "CURTO3": [100, 80]},
            {"LONGO3": 1000.0, "CURTO3": 1000.0})
        self.assertEqual(saida["pregoes"], 2)
        self.assertEqual(saida["drawdown_pct"], -20.0)

    def test_cobertura_total_quando_todos_tem_historico(self):
        saida = estresse.estresse_historico(
            {"AAA3": [100, 80], "BBB3": [100, 90]},
            {"AAA3": 1000.0, "BBB3": 1000.0})
        self.assertEqual(saida["cobertura_pct"], 100.0)
        self.assertEqual(saida["sem_historico"], [])

    def test_carteira_vazia_nao_estoura(self):
        saida = estresse.estresse_historico({}, {})
        self.assertIsNone(saida["drawdown_pct"])


class TestCatalogoDeEventos(unittest.TestCase):
    def test_todo_evento_tem_janela_e_resumo(self):
        for evento in estresse.EVENTOS:
            self.assertTrue(evento["inicio"] < evento["fim"], evento["chave"])
            self.assertTrue(evento["nome"] and evento["resumo"], evento["chave"])

    def test_as_chaves_sao_unicas(self):
        chaves = [e["chave"] for e in estresse.EVENTOS]
        self.assertEqual(len(chaves), len(set(chaves)))



# ---------------------------------------------------------------------- rotas

import tempfile                                        # noqa: E402

from fastapi.testclient import TestClient              # noqa: E402

from modules import contas                             # noqa: E402


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class TestRotasDoEstresse(unittest.TestCase):
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

    ROTAS = ("/api/v1/carteira/estresse/selic",
             "/api/v1/carteira/estresse/historico")

    def test_exigem_sessao(self):
        self.cliente.cookies.clear()
        for rota in self.ROTAS:
            self.assertEqual(self.cliente.get(rota).status_code, 401, rota)

    def test_exigem_assinatura(self):
        """Uma rota esquecida entrega a camada de portfólio a quem não assina,
        e nenhum teste de interface pegaria isso."""
        self._entrar("free@teste.com", premium=False)
        for rota in self.ROTAS:
            self.assertEqual(self.cliente.get(rota).status_code, 402, rota)

    def test_carteira_vazia_recusa_com_motivo(self):
        self._entrar("vip1@teste.com")
        for rota in self.ROTAS:
            resposta = self.cliente.get(rota)
            self.assertEqual(resposta.status_code, 422, rota)
            self.assertEqual(resposta.json()["detail"]["erro"], "carteira_vazia")

    def test_o_limite_do_metodo_chega_na_resposta(self):
        """O número é sensibilidade, não previsão, e quem consome a API por
        fora da nossa tela precisa receber essa ressalva junto."""
        self._entrar("vip2@teste.com")
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 100,
                                "preco_medio": 30.0})
        corpo = self.cliente.get(
            "/api/v1/carteira/estresse/selic?taxa=20").json()
        self.assertIn("LIMITE SUPERIOR", corpo["limite"])
        self.assertEqual(corpo["taxa_nova"], 20.0)
        self.assertIn("selic_meta", corpo)

    def test_sem_taxa_usa_a_selic_vigente(self):
        self._entrar("vip3@teste.com")
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 100,
                                "preco_medio": 30.0})
        corpo = self.cliente.get("/api/v1/carteira/estresse/selic").json()
        self.assertEqual(corpo["taxa_atual"], corpo["taxa_nova"])

if __name__ == "__main__":
    unittest.main(verbosity=2)
