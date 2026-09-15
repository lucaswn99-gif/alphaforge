"""Pilar 5: radar de cinco eixos, por papel e por carteira.

O risco desta camada é sutil e não dá erro: uma nota 0-100 parece informação
mesmo quando foi calculada sobre um critério só, ou com a unidade trocada. Por
isso quase todo teste aqui é sobre COBERTURA e UNIDADE, e não sobre a
aritmética da faixa.

    python -m unittest test_radar -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import radar  # noqa: E402


BALANCO_BOM = {
    "patrimonio_liquido": 10e9, "lucro_liquido": 2e9, "receita_liquida": 10e9,
    "ebit": 3e9, "ativo_total": 20e9, "ativo_circulante": 5e9,
    "passivo_circulante": 2e9, "passivo_nao_circulante": 6e9,
    "divida_curto_prazo": 1e9, "divida_longo_prazo": 4e9, "caixa": 2e9,
    "lucros_acumulados": 3e9,
}


class TestUnidadeDaMargem(unittest.TestCase):
    """A armadilha que motivou este bloco: `quant.margem_de_seguranca` devolve
    PORCENTAGEM e `filosofias._avaliar_graham` devolve FRAÇÃO. Alimentar a
    faixa com a unidade errada divide a nota por cem sem erro nenhum."""

    def test_fracao_de_graham_e_convertida_para_porcentagem(self):
        # 0,30 é 30% de margem, e tem que pontuar como 30 — não como 0,30.
        alta, _ = radar.eixo_valor({"margem_seguranca": 0.30,
                                    "produto_pl_pvp": 14.0, "pvp": 1.1})
        baixa, _ = radar.eixo_valor({"margem_seguranca": 0.003,
                                     "produto_pl_pvp": 14.0, "pvp": 1.1})
        self.assertGreater(alta, baixa)

    def test_margem_negativa_nao_pontua(self):
        """Negociando acima do Número de Graham não é desconto."""
        cara, _ = radar.eixo_valor({"margem_seguranca": -0.20,
                                    "produto_pl_pvp": 14.0, "pvp": 1.1})
        barata, _ = radar.eixo_valor({"margem_seguranca": 0.40,
                                      "produto_pl_pvp": 14.0, "pvp": 1.1})
        self.assertLess(cara, barata)


class TestUnidadeDoMomentum(unittest.TestCase):
    """Mesma classe de armadilha: `MotorMomentum` devolve momentum e
    volatilidade como FRAÇÃO, e as faixas são em porcentagem."""

    def test_fracao_do_motor_e_convertida(self):
        # 0,35 é 35% de alta em doze meses, e tem que pontuar como tal.
        forte, _ = radar.eixo_momentum({"momentum_12m_1m": 0.35, "sharpe": 1.4,
                                        "volatilidade_anual": 0.22})
        fraco, _ = radar.eixo_momentum({"momentum_12m_1m": 0.0035, "sharpe": 1.4,
                                        "volatilidade_anual": 0.22})
        self.assertGreater(forte, fraco)

    def test_queda_profunda_nao_pontua_em_momentum(self):
        caindo, _ = radar.eixo_momentum({"momentum_12m_1m": -0.30, "sharpe": -0.5,
                                         "volatilidade_anual": 0.55})
        subindo, _ = radar.eixo_momentum({"momentum_12m_1m": 0.30, "sharpe": 1.5,
                                          "volatilidade_anual": 0.20})
        self.assertLess(caindo, subindo)

    def test_volatilidade_menor_pontua_mais(self):
        """Aqui a volatilidade mede risco, não oportunidade."""
        calmo, _ = radar.eixo_momentum({"momentum_12m_1m": 0.20,
                                        "volatilidade_anual": 0.15})
        agitado, _ = radar.eixo_momentum({"momentum_12m_1m": 0.20,
                                          "volatilidade_anual": 0.60})
        self.assertGreater(calmo, agitado)

    def test_sem_avaliacao_volta_none(self):
        self.assertEqual(radar.eixo_momentum(None), (None, 0))
        self.assertEqual(radar.eixo_momentum({}), (None, 0))

    def test_momentum_nao_apurado_nao_zera_o_eixo(self):
        """O motor devolve todos os campos None quando falta histórico. Isso é
        ausência, não momentum ruim."""
        nota, medidos = radar.eixo_momentum(
            {"momentum_12m_1m": None, "sharpe": None, "volatilidade_anual": None,
             "veredito": "nao_apurado"})
        self.assertIsNone(nota)
        self.assertEqual(medidos, 0)


class TestCoberturaDosEixos(unittest.TestCase):
    def test_eixo_sem_nenhum_criterio_volta_none_nao_zero(self):
        """None e zero dizem coisas opostas: 'não medi' e 'medi, é péssimo'."""
        nota, medidos = radar.eixo_valor(None)
        self.assertIsNone(nota)
        self.assertEqual(medidos, 0)
        self.assertIsNone(radar.eixo_qualidade({})[0])

    def test_um_criterio_medido_ja_produz_nota(self):
        """Normalizar pelo total teórico puniria o papel por um dado que a
        fonte não trouxe; normalizar pela cobertura, não."""
        nota, medidos = radar.eixo_valor({"pvp": 0.6})
        self.assertIsNotNone(nota)
        self.assertEqual(medidos, 1)

    def test_a_nota_nao_cai_so_porque_faltou_um_criterio(self):
        completo, _ = radar.eixo_valor({"margem_seguranca": 0.40,
                                        "produto_pl_pvp": 8.0, "pvp": 0.6})
        parcial, _ = radar.eixo_valor({"margem_seguranca": 0.40,
                                       "produto_pl_pvp": 8.0})
        self.assertAlmostEqual(completo, parcial, delta=12.0)

    def test_quantos_criterios_entraram_viaja_junto(self):
        papel = radar.radar_do_papel("X3", balanco=BALANCO_BOM)
        self.assertEqual(papel["criterios_medidos"]["qualidade"], 3)


class TestEixoQualidade(unittest.TestCase):
    def test_empresa_rentavel_pontua_mais_que_a_medianamente(self):
        fraca = dict(BALANCO_BOM, lucro_liquido=1e8)      # ROE 1%, margem 1%
        self.assertGreater(radar.eixo_qualidade(BALANCO_BOM)[0],
                           radar.eixo_qualidade(fraca)[0])

    def test_prejuizo_nao_pontua_em_qualidade(self):
        prejuizo = dict(BALANCO_BOM, lucro_liquido=-2e9)
        nota, _ = radar.eixo_qualidade(prejuizo)
        self.assertIsNotNone(nota, "o eixo existe, mas a nota é baixa")
        self.assertLess(nota, radar.eixo_qualidade(BALANCO_BOM)[0])

    def test_patrimonio_zerado_nao_estoura(self):
        sem_pl = dict(BALANCO_BOM, patrimonio_liquido=0.0)
        radar.eixo_qualidade(sem_pl)


class TestEixoSeguranca(unittest.TestCase):
    def test_constancia_pesa(self):
        com_prejuizo = radar.eixo_seguranca(BALANCO_BOM, exercicios_com_lucro=1,
                                            exercicios_apurados=3)[0]
        sempre_lucro = radar.eixo_seguranca(BALANCO_BOM, exercicios_com_lucro=3,
                                            exercicios_apurados=3)[0]
        self.assertGreater(sempre_lucro, com_prejuizo)

    def test_sem_historico_a_constancia_nao_entra_na_conta(self):
        """E não entra como zero: entra como ausência, que é outra coisa."""
        sem, medidos_sem = radar.eixo_seguranca(BALANCO_BOM)
        com, medidos_com = radar.eixo_seguranca(BALANCO_BOM,
                                                exercicios_com_lucro=3,
                                                exercicios_apurados=3)
        self.assertEqual(medidos_sem, 3)
        self.assertEqual(medidos_com, 4)
        self.assertIsNotNone(sem)

    def test_alavancagem_alta_derruba_a_seguranca(self):
        alavancada = dict(BALANCO_BOM, divida_longo_prazo=40e9, caixa=0.0)
        self.assertLess(radar.eixo_seguranca(alavancada)[0],
                        radar.eixo_seguranca(BALANCO_BOM)[0])

    def test_liquidez_corrente_abaixo_de_um_nao_pontua(self):
        apertada = dict(BALANCO_BOM, ativo_circulante=1e9, passivo_circulante=3e9)
        self.assertLess(radar.eixo_seguranca(apertada)[0],
                        radar.eixo_seguranca(BALANCO_BOM)[0])

    def test_balanco_vazio_nao_estoura(self):
        nota, medidos = radar.eixo_seguranca({})
        self.assertIsNone(nota)
        self.assertEqual(medidos, 0)


class TestRadarDoPapel(unittest.TestCase):
    def test_os_cinco_eixos_existem_sempre(self):
        papel = radar.radar_do_papel("VAZIO3")
        self.assertEqual(set(papel["eixos"]), set(radar.EIXOS))
        self.assertEqual(papel["eixos_nao_apurados"], sorted(radar.EIXOS))

    def test_eixos_apurados_e_nao_apurados_sao_complementares(self):
        papel = radar.radar_do_papel("X3", balanco=BALANCO_BOM)
        self.assertEqual(
            set(papel["eixos_apurados"]) | set(papel["eixos_nao_apurados"]),
            set(radar.EIXOS))
        self.assertFalse(
            set(papel["eixos_apurados"]) & set(papel["eixos_nao_apurados"]))

    def test_sem_bazin_o_eixo_de_proventos_fica_nao_apurado(self):
        papel = radar.radar_do_papel("X3", balanco=BALANCO_BOM)
        self.assertIsNone(papel["eixos"]["proventos"])
        self.assertIn("proventos", papel["eixos_nao_apurados"])

    def test_com_bazin_o_eixo_de_proventos_sai(self):
        papel = radar.radar_do_papel(
            "X3", balanco=BALANCO_BOM,
            bazin={"margem_seguranca": 25.0, "dy_12m": 8.0, "payout": 55.0,
                   "dl_ebit": 1.0})
        self.assertIsNotNone(papel["eixos"]["proventos"])

    def test_toda_nota_cabe_na_escala(self):
        papel = radar.radar_do_papel(
            "X3", graham={"margem_seguranca": 5.0, "produto_pl_pvp": 1.0, "pvp": 0.1},
            balanco=BALANCO_BOM, exercicios_com_lucro=3, exercicios_apurados=3)
        for eixo, nota in papel["eixos"].items():
            if nota is not None:
                self.assertGreaterEqual(nota, 0.0, eixo)
                self.assertLessEqual(nota, 100.0, eixo)


class TestRadarDaCarteira(unittest.TestCase):
    def _radar(self, ticker, **eixos):
        base = {e: None for e in radar.EIXOS}
        base.update(eixos)
        return {"ticker": ticker, "eixos": base}

    def test_media_ponderada_por_valor(self):
        saida = radar.radar_da_carteira(
            [self._radar("GRANDE3", valor=100.0), self._radar("PEQUENO3", valor=0.0)],
            {"GRANDE3": 9000.0, "PEQUENO3": 1000.0})
        self.assertEqual(saida["eixos"]["valor"], 90.0)

    def test_papel_sem_nota_fica_fora_da_media_e_nao_vira_zero(self):
        """Tratar ausência como zero puxaria a média para baixo por falta de
        dado — o erro que este projeto não comete."""
        saida = radar.radar_da_carteira(
            [self._radar("COM3", valor=80.0), self._radar("SEM3")],
            {"COM3": 1000.0, "SEM3": 1000.0})
        self.assertEqual(saida["eixos"]["valor"], 80.0)

    def test_a_cobertura_denuncia_a_media_parcial(self):
        """Se só metade do peso tem nota, a nota descreve metade da carteira —
        e o número sozinho mentiria por omissão."""
        saida = radar.radar_da_carteira(
            [self._radar("COM3", valor=80.0), self._radar("SEM3")],
            {"COM3": 1000.0, "SEM3": 1000.0})
        self.assertEqual(saida["cobertura_pct"]["valor"], 50.0)
        self.assertEqual(saida["cobertura_pct"]["momentum"], 0.0)

    def test_aponta_o_eixo_mais_forte_e_o_mais_fraco(self):
        saida = radar.radar_da_carteira(
            [self._radar("X3", valor=90.0, qualidade=40.0, seguranca=70.0)],
            {"X3": 1000.0})
        self.assertEqual(saida["eixo_mais_forte"], "valor")
        self.assertEqual(saida["eixo_mais_fraco"], "qualidade")

    def test_eixo_nao_apurado_nao_disputa_o_mais_fraco(self):
        """None não é a nota mais baixa — é a falta de nota."""
        saida = radar.radar_da_carteira(
            [self._radar("X3", valor=90.0, qualidade=40.0)], {"X3": 1000.0})
        self.assertEqual(saida["eixo_mais_fraco"], "qualidade")
        self.assertIsNone(saida["eixos"]["momentum"])

    def test_media_geral_ignora_eixo_ausente(self):
        saida = radar.radar_da_carteira(
            [self._radar("X3", valor=80.0, qualidade=60.0)], {"X3": 1000.0})
        self.assertEqual(saida["media_geral"], 70.0)

    def test_carteira_vazia_nao_estoura(self):
        saida = radar.radar_da_carteira([], {})
        self.assertIsNone(saida["media_geral"])
        self.assertIsNone(saida["eixo_mais_forte"])
        self.assertEqual(saida["papeis"], 0)

    def test_peso_zero_nao_entra(self):
        saida = radar.radar_da_carteira(
            [self._radar("ZERO3", valor=100.0), self._radar("REAL3", valor=50.0)],
            {"ZERO3": 0.0, "REAL3": 1000.0})
        self.assertEqual(saida["eixos"]["valor"], 50.0)

    def test_rotulos_e_perguntas_cobrem_os_cinco_eixos(self):
        saida = radar.radar_da_carteira([], {})
        for eixo in radar.EIXOS:
            self.assertIn(eixo, saida["rotulos"])
            self.assertIn(eixo, saida["perguntas"])


# ---------------------------------------------------------------------- rota

import tempfile                                        # noqa: E402

from fastapi.testclient import TestClient              # noqa: E402

from modules import contas                             # noqa: E402


class TestRotaDoRadar(unittest.TestCase):
    def setUp(self):
        contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
        contas._iniciado = False
        contas.iniciar()
        from routers import conta as rota_conta
        rota_conta._tentativas.clear()
        import api
        self.cliente = TestClient(api.app)

    def _entrar(self, email, premium=True):
        self.cliente.post("/conta/registrar",
                          json={"email": email, "senha": "senha-boa-123"})
        usuario = contas.autenticar(email, "senha-boa-123")
        if premium:
            contas.definir_plano(usuario["id"], "premium")
        return usuario

    def test_exige_sessao(self):
        self.cliente.cookies.clear()
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/radar").status_code, 401)

    def test_exige_assinatura(self):
        self._entrar("free@teste.com", premium=False)
        self.assertEqual(
            self.cliente.get("/api/v1/carteira/radar").status_code, 402)

    def test_carteira_sem_acao_recusa_e_explica(self):
        """FII não tem ROE nem produto P/L x P/VP, e ETF e cesta de indice.
        Dar nota a eles produziria numero com cara de medida e conteudo de
        ruido."""
        self._entrar("vip@teste.com")
        self.cliente.post("/api/v1/carteira/item",
                          json={"ticker": "HGLG11", "quantidade": 10,
                                "preco_medio": 150.0})
        resposta = self.cliente.get("/api/v1/carteira/radar")
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(resposta.json()["detail"]["erro"], "sem_acoes")
        self.assertIn("ROE", resposta.json()["detail"]["motivo"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
