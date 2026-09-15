"""Filosofia declarada: roteamento do diagnóstico e exceção por papel.

O que está sob teste aqui é a ESCOLHA DA RÉGUA, não a aritmética de cada
metodologia — Barsi e Graham têm cobertura em test_filosofias.py, Bazin em
test_quant.py. O risco desta camada é outro, e é o que motivou a mudança:
medir toda ação por Graham produz "atenção" e "desconformidade" para quem tem
mandato de renda, e isso é ruído com cara de veredito.

    python -m unittest test_mandato -v
"""
import os
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import contas, diagnostico, mandato  # noqa: E402


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


class MotorFalso:
    """Devolve avaliações prontas. O que importa é QUAL avaliador foi chamado."""

    def __init__(self):
        self.chamadas = []

    def setor_besst(self, ticker):
        return {"TAEE11": "energia", "BBAS3": "bancos"}.get(ticker)

    def _avaliar_graham(self, ticker, aplicar_momentum=True):
        self.chamadas.append(("graham", ticker))
        return {"ticker": ticker, "aprovado": True, "motivos": [],
                "alertas_qualidade": [], "fora_do_escopo": False,
                "numero_graham": 30.0, "margem_seguranca": 0.2,
                "criterios_medidos": 6}

    def _avaliar_barsi(self, ticker, setor, aplicar_momentum=True):
        self.chamadas.append(("barsi", ticker))
        return {"ticker": ticker, "aprovado": True, "motivos": [],
                "preco_teto": 45.0, "margem_seguranca": 0.25,
                "yield_sobre_preco": 7.5, "payout": 60.0,
                "tendencia_dpa": {"classificacao": "crescente"}}

    def _avaliar_bazin(self, ticker):
        self.chamadas.append(("bazin", ticker))
        return {"ticker": ticker, "preco_teto": 40.0, "margem_seguranca": 0.2,
                "dy_12m": 7.0, "payout": 55.0, "dl_ebit": 1.2,
                "criterios": {"dy_suficiente": True, "payout_saudavel": True,
                              "alavancagem_ok": True, "abaixo_do_teto": True},
                "criterios_nao_apurados": [], "aprovado": True}

    def _balanco_cvm(self, ticker):
        self.chamadas.append(("balanco", ticker))
        return {"patrimonio_liquido": 100.0, "lucro_liquido": 28.0,
                "receita_liquida": 200.0, "divida_curto_prazo": 10.0,
                "divida_longo_prazo": 70.0, "caixa": 12.0, "ebit": 11.0}


def _posicao(ticker, filosofia=None):
    return {"ticker": ticker, "classe": "acao", "preco_medio": 30.0,
            "custo_total": 3000.0, "filosofia": filosofia}


class TestResolucaoDaFilosofia(unittest.TestCase):
    def test_a_do_papel_vence_a_da_carteira(self):
        self.assertEqual(mandato.resolver("barsi", "graham"), "graham")

    def test_sem_excecao_herda_a_da_carteira(self):
        self.assertEqual(mandato.resolver("barsi", None), "barsi")

    def test_sem_nenhuma_das_duas_nao_ha_filosofia(self):
        """Sem padrão, de propósito: escolher a lente pela qual a carteira de
        alguém é julgada é decisão de quem investe."""
        self.assertIsNone(mandato.resolver(None, None))

    def test_nome_invalido_e_recusado(self):
        with self.assertRaises(mandato.ErroMandato) as erro:
            mandato.validar("warren")
        self.assertIn("barsi", str(erro.exception))

    def test_nome_normaliza_caixa(self):
        self.assertEqual(mandato.validar("  GRAHAM "), "graham")


class TestRoteamentoDoDiagnostico(unittest.TestCase):
    def test_carteira_de_renda_e_medida_por_barsi(self):
        motor = MotorFalso()
        saida = diagnostico.diagnosticar(motor, [_posicao("TAEE11")],
                                         filosofia_carteira="barsi")
        self.assertEqual(motor.chamadas, [("barsi", "TAEE11")])
        linha = saida["posicoes"][0]["diagnostico"]
        self.assertEqual(linha["estado"], diagnostico.CONFORME)
        self.assertIn("Barsi", linha["metodo"])
        self.assertEqual(linha["preco_teto"], 45.0)

    def test_a_mesma_carteira_por_bazin_chama_outro_avaliador(self):
        motor = MotorFalso()
        diagnostico.diagnosticar(motor, [_posicao("TAEE11")],
                                 filosofia_carteira="bazin")
        self.assertEqual(motor.chamadas, [("bazin", "TAEE11")])

    def test_excecao_por_papel_muda_so_aquele_papel(self):
        """O caso real: mandato de renda com uma posição de valor dentro."""
        motor = MotorFalso()
        diagnostico.diagnosticar(
            motor, [_posicao("TAEE11"), _posicao("PETR4", filosofia="graham")],
            filosofia_carteira="barsi")
        self.assertEqual(sorted(motor.chamadas),
                         [("barsi", "TAEE11"), ("graham", "PETR4")])

    def test_sem_filosofia_a_acao_nao_e_medida(self):
        """Nada de medir por Graham 'porque é o padrão' — era isso que produzia
        desconformidade para quem tem mandato de renda."""
        motor = MotorFalso()
        saida = diagnostico.diagnosticar(motor, [_posicao("TAEE11")])
        self.assertEqual(motor.chamadas, [])
        linha = saida["posicoes"][0]["diagnostico"]
        self.assertEqual(linha["estado"], diagnostico.NAO_APURADO)
        self.assertIn("Escolha a filosofia", linha["resumo"])

    def test_nenhuma_mostra_dado_cru_sem_veredito(self):
        """"Nenhuma" é escolha deliberada, não pendência: nem chama Barsi/
        Bazin/Graham para veredito, e o resumo carrega o número, não um
        aprovado/reprovado."""
        motor = MotorFalso()
        saida = diagnostico.diagnosticar(motor, [_posicao("TAEE11")],
                                         filosofia_carteira="nenhuma")
        self.assertEqual(sorted(motor.chamadas), [("balanco", "TAEE11"),
                                                   ("graham", "TAEE11")])
        linha = saida["posicoes"][0]["diagnostico"]
        self.assertEqual(linha["estado"], diagnostico.SEM_FILOSOFIA)
        self.assertIn("ROE", linha["resumo"])
        self.assertNotIn("aprovado", linha["resumo"].lower())
        self.assertNotIn("reprovado", linha["resumo"].lower())

    def test_barsi_fora_do_besst_e_nao_apurado_nao_reprovacao(self):
        """Barsi é uma tese sobre setores perenes. Aplicá-la a uma varejista
        não produz reprovação — produz uma pergunta que o método não faz."""
        motor = MotorFalso()
        saida = diagnostico.diagnosticar(motor, [_posicao("MGLU3")],
                                         filosofia_carteira="barsi")
        self.assertEqual(motor.chamadas, [], "nem chega a avaliar")
        linha = saida["posicoes"][0]["diagnostico"]
        self.assertEqual(linha["estado"], diagnostico.NAO_APURADO)
        self.assertIn("BESST", linha["resumo"])

    def test_fii_nao_muda_com_a_filosofia_da_carteira(self):
        """A filosofia escolhe a régua das AÇÕES. FII continua medido por
        desconto patrimonial, que é o gatilho de entrada do ativo."""
        motor = MotorFalso()
        saida = diagnostico.diagnosticar(
            motor, [{"ticker": "HGLG11", "classe": "fii", "preco_medio": 150.0,
                     "custo_total": 1500.0, "filosofia": None}],
            filosofia_carteira="barsi")
        self.assertEqual(motor.chamadas, [])
        self.assertIn("P/VP", saida["posicoes"][0]["diagnostico"]["metodo"])


class TestVereditoDeBarsi(unittest.TestCase):
    def test_aprovado_e_conforme(self):
        estado, resumo, _ = diagnostico._veredito_barsi(
            {"aprovado": True, "motivos": [], "preco_teto": 45.0})
        self.assertEqual(estado, diagnostico.CONFORME)
        self.assertIn("45.00", resumo)

    def test_margem_insuficiente_e_atencao_nao_desconformidade(self):
        """Está caro pede esperar, não rever a tese."""
        estado, _, _ = diagnostico._veredito_barsi(
            {"aprovado": False, "motivos": ["Margem de segurança de 2.0% — mínimo 20%."]})
        self.assertEqual(estado, diagnostico.ATENCAO)

    def test_prejuizo_e_desconformidade(self):
        estado, resumo, _ = diagnostico._veredito_barsi(
            {"aprovado": False, "motivos": ["Prejuízo em pelo menos um dos 3 exercícios."]})
        self.assertEqual(estado, diagnostico.DESCONFORME)
        self.assertIn("Prejuízo", resumo)

    def test_alavancagem_alta_e_desconformidade(self):
        estado, _, _ = diagnostico._veredito_barsi(
            {"aprovado": False, "motivos": ["Dívida líquida/EBIT de 6.0x — teto 3.0x."]})
        self.assertEqual(estado, diagnostico.DESCONFORME)

    def test_qualidade_vence_preco_quando_os_dois_reprovam(self):
        estado, resumo, _ = diagnostico._veredito_barsi(
            {"aprovado": False, "motivos": ["Margem de segurança de 1.0% — mínimo 20%.",
                                            "Payout de 95% fora da faixa 30–80%."]})
        self.assertEqual(estado, diagnostico.DESCONFORME)
        self.assertIn("Payout", resumo)


class TestVereditoDeBazin(unittest.TestCase):
    def _linha(self, **criterios):
        base = {"dy_suficiente": True, "payout_saudavel": True,
                "alavancagem_ok": True, "abaixo_do_teto": True}
        base.update(criterios)
        faltantes = [k for k, v in base.items() if v is None]
        return {"criterios": base, "criterios_nao_apurados": faltantes,
                "aprovado": not faltantes and all(base.values()),
                "preco_teto": 40.0}

    def test_tudo_cumprido_e_conforme(self):
        estado, resumo, _ = diagnostico._veredito_bazin(self._linha())
        self.assertEqual(estado, diagnostico.CONFORME)
        self.assertIn("40.00", resumo)

    def test_acima_do_teto_e_atencao(self):
        """Preço é preço: pede esperar, não reciclar."""
        estado, resumo, _ = diagnostico._veredito_bazin(
            self._linha(abaixo_do_teto=False))
        self.assertEqual(estado, diagnostico.ATENCAO)
        self.assertIn("teto", resumo)

    def test_yield_baixo_e_atencao(self):
        estado, _, _ = diagnostico._veredito_bazin(self._linha(dy_suficiente=False))
        self.assertEqual(estado, diagnostico.ATENCAO)

    def test_payout_fora_da_faixa_e_desconformidade(self):
        """Payout de 95% é a assinatura do provento que vai cair — o buraco que
        o Bazin puro tem e que esta implementação fecha."""
        estado, resumo, _ = diagnostico._veredito_bazin(
            self._linha(payout_saudavel=False))
        self.assertEqual(estado, diagnostico.DESCONFORME)
        self.assertIn("Payout", resumo)

    def test_alavancagem_estourada_e_desconformidade(self):
        estado, _, _ = diagnostico._veredito_bazin(self._linha(alavancagem_ok=False))
        self.assertEqual(estado, diagnostico.DESCONFORME)

    def test_qualidade_vence_preco(self):
        estado, resumo, _ = diagnostico._veredito_bazin(
            self._linha(abaixo_do_teto=False, alavancagem_ok=False))
        self.assertEqual(estado, diagnostico.DESCONFORME)
        self.assertIn("Dívida", resumo)

    def test_criterio_sem_dado_nao_vira_aprovacao_nem_reprovacao(self):
        estado, resumo, _ = diagnostico._veredito_bazin(
            self._linha(payout_saudavel=None))
        self.assertEqual(estado, diagnostico.NAO_APURADO)
        self.assertIn("Payout", resumo)

    def test_sem_preco_e_nao_apurado(self):
        estado, _, _ = diagnostico._veredito_bazin(None)
        self.assertEqual(estado, diagnostico.NAO_APURADO)


class TestPersistenciaDoMandato(unittest.TestCase):
    def setUp(self):
        _banco_limpo()
        usuario, _ = contas.criar_usuario("mandato@teste.com", "senha-boa-123")
        self.usuario = usuario["id"]

    def test_comeca_sem_filosofia(self):
        self.assertIsNone(mandato.obter(self.usuario))

    def test_grava_e_le(self):
        mandato.definir(self.usuario, "bazin")
        self.assertEqual(mandato.obter(self.usuario), "bazin")

    def test_redefinir_substitui(self):
        mandato.definir(self.usuario, "bazin")
        mandato.definir(self.usuario, "barsi")
        self.assertEqual(mandato.obter(self.usuario), "barsi")

    def test_nao_vaza_entre_usuarios(self):
        outro, _ = contas.criar_usuario("outro@teste.com", "senha-boa-123")
        mandato.definir(self.usuario, "graham")
        self.assertIsNone(mandato.obter(outro["id"]))

    def test_a_coluna_filosofia_existe_na_carteira(self):
        """Migração: `CREATE TABLE IF NOT EXISTS` não altera tabela existente,
        e a base de produção já tem `carteiras` com posições reais dentro."""
        with contas._conectar() as cx:
            colunas = {linha[1] for linha in cx.execute("PRAGMA table_info(carteiras)")}
        self.assertIn("filosofia", colunas)

    def test_excecao_do_papel_vai_e_volta(self):
        from modules import carteira
        carteira.adicionar(self.usuario, "PETR4", 100, 30.0)
        self.assertTrue(mandato.definir_do_papel(self.usuario, "PETR4", "graham"))
        linha = carteira.listar(self.usuario)["posicoes"][0]
        self.assertEqual(linha["filosofia"], "graham")

    def test_limpar_a_excecao_volta_a_herdar(self):
        from modules import carteira
        carteira.adicionar(self.usuario, "PETR4", 100, 30.0)
        mandato.definir_do_papel(self.usuario, "PETR4", "graham")
        mandato.definir_do_papel(self.usuario, "PETR4", "herdar")
        linha = carteira.listar(self.usuario)["posicoes"][0]
        self.assertIsNone(linha["filosofia"])

    def test_excecao_em_papel_que_nao_existe_devolve_falso(self):
        self.assertFalse(mandato.definir_do_papel(self.usuario, "ZZZZ3", "graham"))

    def test_aporte_nao_apaga_a_excecao(self):
        """Somar um aporte não pode desfazer uma escolha do investidor."""
        from modules import carteira
        carteira.adicionar(self.usuario, "PETR4", 100, 30.0)
        mandato.definir_do_papel(self.usuario, "PETR4", "graham")
        carteira.adicionar(self.usuario, "PETR4", 50, 40.0)
        linha = carteira.listar(self.usuario)["posicoes"][0]
        self.assertEqual(linha["filosofia"], "graham")


class TestRotasDoMandato(unittest.TestCase):
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

    ROTAS = (("get", "/api/v1/carteira/filosofia", None),
             ("put", "/api/v1/carteira/filosofia", {"filosofia": "barsi"}),
             ("put", "/api/v1/carteira/item/PETR4/filosofia", {"filosofia": "graham"}))

    def _chamar(self, metodo, rota, corpo):
        funcao = getattr(self.cliente, metodo)
        return funcao(rota, json=corpo) if corpo is not None else funcao(rota)

    def test_exigem_sessao(self):
        self.cliente.cookies.clear()
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 401, rota)

    def test_exigem_assinatura(self):
        self._entrar("free@teste.com", premium=False)
        for metodo, rota, corpo in self.ROTAS:
            self.assertEqual(self._chamar(metodo, rota, corpo).status_code, 402, rota)

    def test_lista_as_quatro_opcoes_com_resumo(self):
        """As três teses mais "nenhuma" — uma escolha deliberada de não ser
        julgado por nenhuma delas, não a ausência de escolha."""
        self._entrar("vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/filosofia").json()
        self.assertFalse(corpo["definida"])
        self.assertEqual([o["chave"] for o in corpo["opcoes"]],
                         ["barsi", "bazin", "graham", "nenhuma"])
        self.assertTrue(all(o["resumo"] for o in corpo["opcoes"]))

    def test_define_e_le_de_volta(self):
        self._entrar("vip2@teste.com")
        gravar = self.cliente.put("/api/v1/carteira/filosofia",
                                  json={"filosofia": "bazin"})
        self.assertEqual(gravar.status_code, 200)
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/filosofia").json()["filosofia"], "bazin")

    def test_nenhuma_e_uma_escolha_valida_pela_rota(self):
        """Gravar "nenhuma" não é erro nem equivale a não ter escolhido —
        `definida` continua True e a leitura devolve "nenhuma" de volta."""
        self._entrar("vip2b@teste.com")
        gravar = self.cliente.put("/api/v1/carteira/filosofia",
                                  json={"filosofia": "nenhuma"})
        self.assertEqual(gravar.status_code, 200)
        corpo = self.cliente.get("/api/v1/carteira/filosofia").json()
        self.assertTrue(corpo["definida"])
        self.assertEqual(corpo["filosofia"], "nenhuma")

    def test_filosofia_invalida_volta_422_com_motivo(self):
        self._entrar("vip3@teste.com")
        resposta = self.cliente.put("/api/v1/carteira/filosofia",
                                    json={"filosofia": "warren"})
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("barsi", resposta.json()["detail"]["motivo"])

    def test_excecao_em_papel_inexistente_volta_404(self):
        self._entrar("vip4@teste.com")
        resposta = self.cliente.put("/api/v1/carteira/item/ZZZZ3/filosofia",
                                    json={"filosofia": "graham"})
        self.assertEqual(resposta.status_code, 404)

    def test_excecao_por_papel_pela_rota(self):
        self._entrar("vip5@teste.com")
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "PETR4", "quantidade": 100,
                                "preco_medio": 30.0})
        resposta = self.cliente.put("/api/v1/carteira/item/petr4/filosofia",
                                    json={"filosofia": "graham"})
        self.assertEqual(resposta.status_code, 200)
        posicoes = self.cliente.get("/api/v1/carteira").json()["posicoes"]
        self.assertEqual(posicoes[0]["filosofia"], "graham")


if __name__ == "__main__":
    unittest.main(verbosity=2)
