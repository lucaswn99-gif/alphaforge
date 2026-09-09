"""Testes do módulo de opções: precificação, gregas, estruturas e recomendação.

Os valores de referência vêm de casos canônicos da literatura (Hull) e de
identidades que precisam valer sempre — paridade put-call, limites de
não-arbitragem, sinal das gregas. Onde há escolha de modelagem, o teste trava
a escolha e o docstring diz por quê.
"""
import math
import os
import sys
import types
import unittest

import pandas as pd

_yf = types.ModuleType("yfinance")
_yf.download = lambda *a, **k: pd.DataFrame()


class _Ticker:
    def __init__(self, *a, **k):
        self.info = {}

    def history(self, *a, **k):
        return pd.DataFrame()


_yf.Ticker = _Ticker
sys.modules.setdefault("yfinance", _yf)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules import estruturas, opcoes  # noqa: E402


class TestPrecificacao(unittest.TestCase):
    """Caso canônico: S=100, K=100, r=5%, vol=20%, T=1 ano."""

    BASE = dict(spot=100.0, strike=100.0, taxa=0.05, vol=0.20, prazo=1.0)

    def test_call_bate_com_o_valor_de_referencia(self):
        self.assertAlmostEqual(opcoes.preco("call", **self.BASE), 10.4506, places=4)

    def test_put_bate_com_o_valor_de_referencia(self):
        self.assertAlmostEqual(opcoes.preco("put", **self.BASE), 5.5735, places=4)

    def test_paridade_put_call(self):
        """C - P = S - K·e^(-rT). Identidade de não-arbitragem: se ela falhar,
        todo o resto está errado."""
        c = opcoes.preco("call", **self.BASE)
        p = opcoes.preco("put", **self.BASE)
        esperado = 100.0 - 100.0 * math.exp(-0.05 * 1.0)
        self.assertAlmostEqual(c - p, esperado, places=9)

    def test_paridade_vale_com_dividendo(self):
        base = dict(self.BASE, dividendo=0.03)
        c = opcoes.preco("call", **base)
        p = opcoes.preco("put", **base)
        esperado = 100.0 * math.exp(-0.03) - 100.0 * math.exp(-0.05)
        self.assertAlmostEqual(c - p, esperado, places=9)

    def test_no_vencimento_vale_o_intrinseco(self):
        """Prazo zero não pode dividir por zero — o limite é o intrínseco."""
        self.assertEqual(opcoes.preco("call", 110, 100, 0.05, 0.2, 0.0), 10.0)
        self.assertEqual(opcoes.preco("put", 110, 100, 0.05, 0.2, 0.0), 0.0)
        self.assertEqual(opcoes.preco("put", 90, 100, 0.05, 0.2, 0.0), 10.0)

    def test_premio_cresce_com_volatilidade(self):
        barata = opcoes.preco("call", 100, 100, 0.05, 0.15, 1.0)
        cara = opcoes.preco("call", 100, 100, 0.05, 0.45, 1.0)
        self.assertLess(barata, cara)

    def test_premio_cresce_com_prazo(self):
        curta = opcoes.preco("call", 100, 100, 0.05, 0.20, 0.25)
        longa = opcoes.preco("call", 100, 100, 0.05, 0.20, 2.0)
        self.assertLess(curta, longa)

    def test_parametro_ausente_devolve_none_e_nao_zero(self):
        """Zero é um preço. 'Não sei' não é zero."""
        self.assertIsNone(opcoes.preco("call", None, 100, 0.05, 0.2, 1.0))
        self.assertIsNone(opcoes.preco("call", 100, 100, 0.05, None, 1.0))
        self.assertIsNone(opcoes.preco("banana", 100, 100, 0.05, 0.2, 1.0))

    def test_prazo_em_dias_uteis_e_o_padrao(self):
        """B3 cota volatilidade em 252 dias úteis. Usar 365 com vol de 252
        subestima o prêmio — erro silencioso e grande."""
        self.assertAlmostEqual(opcoes.prazo_em_anos(252), 1.0)
        self.assertAlmostEqual(opcoes.prazo_em_anos(365, base="corridos"), 1.0)
        self.assertGreater(opcoes.preco("call", 100, 100, 0.05, 0.2, opcoes.prazo_em_anos(21)),
                           opcoes.preco("call", 100, 100, 0.05, 0.2,
                                        opcoes.prazo_em_anos(21, base="corridos")))


class TestGregas(unittest.TestCase):
    BASE = dict(spot=100.0, strike=100.0, taxa=0.05, vol=0.20, prazo=1.0)

    def test_sinais(self):
        c = opcoes.gregas("call", **self.BASE)
        p = opcoes.gregas("put", **self.BASE)
        self.assertGreater(c["delta"], 0)
        self.assertLess(p["delta"], 0)
        self.assertGreater(c["gama"], 0)
        self.assertGreater(c["vega"], 0)
        self.assertLess(c["theta"], 0)          # comprado perde com o tempo
        self.assertGreater(c["rho"], 0)
        self.assertLess(p["rho"], 0)

    def test_delta_de_call_menos_delta_de_put_e_um(self):
        """Com dividendo zero, Δc - Δp = 1. Outra identidade que não pode falhar."""
        c = opcoes.gregas("call", **self.BASE)["delta"]
        p = opcoes.gregas("put", **self.BASE)["delta"]
        self.assertAlmostEqual(c - p, 1.0, places=9)

    def test_gama_e_vega_sao_iguais_para_call_e_put(self):
        c = opcoes.gregas("call", **self.BASE)
        p = opcoes.gregas("put", **self.BASE)
        self.assertAlmostEqual(c["gama"], p["gama"], places=12)
        self.assertAlmostEqual(c["vega"], p["vega"], places=12)

    def test_delta_confere_com_a_derivada_numerica(self):
        """Prova independente: a fórmula fechada tem que bater com a diferença
        finita do próprio preço."""
        h = 1e-5
        acima = opcoes.preco("call", 100 + h, 100, 0.05, 0.20, 1.0)
        abaixo = opcoes.preco("call", 100 - h, 100, 0.05, 0.20, 1.0)
        self.assertAlmostEqual((acima - abaixo) / (2 * h),
                               opcoes.gregas("call", **self.BASE)["delta"], places=6)

    def test_vega_e_por_ponto_de_volatilidade(self):
        """Vega em unidade de mesa: por 1 PONTO de vol, não por 1,00."""
        vega = opcoes.gregas("call", **self.BASE)["vega"]
        base = opcoes.preco("call", 100, 100, 0.05, 0.20, 1.0)
        um_ponto = opcoes.preco("call", 100, 100, 0.05, 0.21, 1.0)
        self.assertAlmostEqual(vega, um_ponto - base, places=3)

    def test_theta_e_por_dia(self):
        """Theta em unidade de mesa: por DIA corrido, não por ano."""
        theta = opcoes.gregas("call", **self.BASE)["theta"]
        self.assertGreater(abs(theta), 0.001)
        self.assertLess(abs(theta), 0.5)


class TestVolatilidadeImplicita(unittest.TestCase):
    def test_recupera_a_volatilidade_usada(self):
        for vol in (0.10, 0.25, 0.60, 1.20):
            premio = opcoes.preco("call", 48, 50, 0.14, vol, 0.25)
            self.assertAlmostEqual(
                opcoes.volatilidade_implicita("call", premio, 48, 50, 0.14, 0.25),
                vol, places=5, msg=f"vol {vol}")

    def test_funciona_muito_fora_do_dinheiro(self):
        """É onde Newton sozinho diverge — o vega fica minúsculo. Sem a
        bisseção de reserva, a função devolveria lixo justamente aqui."""
        premio = opcoes.preco("call", 48, 80, 0.14, 0.40, 0.08)
        recuperada = opcoes.volatilidade_implicita("call", premio, 48, 80, 0.14, 0.08)
        self.assertAlmostEqual(recuperada, 0.40, places=3)

    def test_premio_abaixo_do_intrinseco_nao_tem_implicita(self):
        """Não é vol baixa — é erro de digitação, e devolver um número
        esconderia isso."""
        self.assertIsNone(opcoes.volatilidade_implicita("call", 0.50, 60, 50, 0.14, 0.25))

    def test_premio_acima_do_maximo_teorico_nao_tem_implicita(self):
        self.assertIsNone(opcoes.volatilidade_implicita("call", 999.0, 48, 50, 0.14, 0.25))


class TestStrikePorDelta(unittest.TestCase):
    """Montar por delta, e não por percentual do spot, é o que faz a mesma
    receita funcionar num papel de vol 20% e num de vol 60%."""

    def test_encontra_o_strike_do_delta_pedido(self):
        for alvo in (0.20, 0.35, 0.50, 0.70):
            k = opcoes.strike_por_delta("call", alvo, 48, 0.14, 0.35, 0.12)
            delta = opcoes.gregas("call", 48, k, 0.14, 0.35, 0.12)["delta"]
            self.assertAlmostEqual(delta, alvo, places=3, msg=f"alvo {alvo}")

    def test_put_devolve_delta_negativo_do_modulo_pedido(self):
        k = opcoes.strike_por_delta("put", 0.25, 48, 0.14, 0.35, 0.12)
        self.assertAlmostEqual(opcoes.gregas("put", 48, k, 0.14, 0.35, 0.12)["delta"],
                               -0.25, places=3)

    def test_delta_maior_significa_strike_menor_na_call(self):
        alto = opcoes.strike_por_delta("call", 0.70, 48, 0.14, 0.35, 0.12)
        baixo = opcoes.strike_por_delta("call", 0.20, 48, 0.14, 0.35, 0.12)
        self.assertLess(alto, baixo)

    def test_mais_volatilidade_afasta_o_strike_de_mesmo_delta(self):
        calmo = opcoes.strike_por_delta("call", 0.25, 48, 0.14, 0.20, 0.12)
        agitado = opcoes.strike_por_delta("call", 0.25, 48, 0.14, 0.60, 0.12)
        self.assertGreater(agitado, calmo)


class TestEstruturas(unittest.TestCase):
    """Cada estrutura tem geometria conhecida. Se o payoff não bater com ela,
    o erro está no motor, não no mercado."""

    SPOT, TAXA, VOL = 48.0, 0.14, 0.35
    PRAZO = opcoes.prazo_em_anos(30)

    def _avaliar(self, pernas, **extra):
        base = dict(spot=self.SPOT, taxa=self.TAXA, prazo=self.PRAZO,
                    vol_real=self.VOL, retorno_esperado_ativo=0.15)
        base.update(extra)
        return opcoes.avaliar_estrutura(pernas, **base)

    def test_trava_de_alta_tem_geometria_conhecida(self):
        """Custo 1,25; ganho máximo (52−48)−1,25; perda máxima o débito;
        breakeven no strike comprado mais o custo."""
        pernas = [{"tipo": "call", "posicao": "compra", "strike": 48, "premio": 2.10},
                  {"tipo": "call", "posicao": "venda", "strike": 52, "premio": 0.85}]
        r = self._avaliar(pernas)
        self.assertAlmostEqual(r["custo_liquido"], 1.25)
        self.assertAlmostEqual(r["lucro_maximo"], 2.75, places=2)
        self.assertAlmostEqual(r["perda_maxima"], -1.25, places=2)
        self.assertAlmostEqual(r["pontos_equilibrio"][0], 49.25, places=1)
        self.assertEqual(r["tipo_montagem"], "débito")
        self.assertGreater(r["gregas"]["delta"], 0)

    def test_venda_de_call_a_seco_tem_perda_ilimitada(self):
        """'Ilimitado' não é um número grande. Devolver 'R$ 8.400 de perda
        máxima' porque a grade parou ali seria a pior mentira desta tela."""
        r = self._avaliar([{"tipo": "call", "posicao": "venda", "strike": 50, "premio": 1.50}])
        self.assertTrue(r["perda_ilimitada"])
        self.assertIsNone(r["perda_maxima"])
        self.assertAlmostEqual(r["lucro_maximo"], 1.50, places=2)

    def test_compra_de_call_tem_ganho_ilimitado_e_perda_no_premio(self):
        r = self._avaliar([{"tipo": "call", "posicao": "compra", "strike": 50, "premio": 1.50}])
        self.assertTrue(r["lucro_ilimitado"])
        self.assertIsNone(r["lucro_maximo"])
        self.assertAlmostEqual(r["perda_maxima"], -1.50, places=2)

    def test_condor_de_ferro_e_credito_com_perda_limitada(self):
        pernas = estruturas.montar("condor_ferro", self.SPOT, self.TAXA, self.VOL, self.PRAZO)
        r = self._avaliar(pernas)
        self.assertEqual(r["tipo_montagem"], "crédito")
        self.assertFalse(r["perda_ilimitada"])
        self.assertFalse(r["lucro_ilimitado"])
        self.assertEqual(len(r["pontos_equilibrio"]), 2)

    def test_financiamento_exige_a_acao_e_limita_o_ganho(self):
        pernas = estruturas.montar("financiamento", self.SPOT, self.TAXA, self.VOL, self.PRAZO)
        self.assertTrue(any(p["tipo"] == "acao" for p in pernas))
        r = self._avaliar(pernas)
        self.assertFalse(r["lucro_ilimitado"])   # a call vendida põe teto

    def test_straddle_tem_dois_breakevens_e_delta_perto_de_zero(self):
        pernas = estruturas.montar("straddle", self.SPOT, self.TAXA, self.VOL, self.PRAZO)
        r = self._avaliar(pernas)
        self.assertEqual(len(r["pontos_equilibrio"]), 2)
        self.assertLess(abs(r["gregas"]["delta"]), 0.15)
        self.assertGreater(r["gregas"]["vega"], 0)    # comprado em volatilidade

    def test_perna_invalida_e_descartada_e_nao_quebra(self):
        r = self._avaliar([{"tipo": "call", "posicao": "compra", "premio": 1.0},   # sem strike
                           {"tipo": "call", "posicao": "compra", "strike": 50, "premio": 1.0}])
        self.assertEqual(len(r["pernas"]), 1)

    def test_sem_perna_valida_devolve_erro_explicito(self):
        self.assertIn("erro", self._avaliar([{"tipo": "banana", "premio": 1}]))

    def test_todo_catalogo_monta_e_avalia(self):
        for chave in estruturas.CATALOGO:
            pernas = estruturas.montar(chave, self.SPOT, self.TAXA, self.VOL, self.PRAZO)
            self.assertTrue(pernas, chave)
            r = self._avaliar(pernas)
            self.assertNotIn("erro", r, chave)
            self.assertIsNotNone(r["probabilidade_lucro"], chave)


class TestRetornoEsperado(unittest.TestCase):
    """A parte que quase toda ferramenta erra."""

    PRAZO = opcoes.prazo_em_anos(60)

    def test_sob_medida_neutra_a_risco_o_retorno_e_a_taxa_livre(self):
        """A demonstração de por que 'retorno esperado' com a taxa livre é
        vazio: descontado, o valor esperado do payoff é o próprio prêmio, para
        QUALQUER opção. Zero informação — e é exatamente o número que muitas
        telas anunciam como retorno esperado."""
        taxa, vol, spot, strike = 0.10, 0.30, 100.0, 105.0
        premio = opcoes.preco("call", spot, strike, taxa, vol, self.PRAZO)
        dist = opcoes.distribuicao_final(spot, taxa, vol, self.PRAZO)
        esperado = sum(max(s - strike, 0.0) * w for s, w in dist)
        self.assertAlmostEqual(esperado * math.exp(-taxa * self.PRAZO), premio, places=2)

    def test_expectativa_otimista_aumenta_o_retorno_da_call(self):
        pernas = [{"tipo": "call", "posicao": "compra", "strike": 50, "premio": 1.50}]
        comum = dict(spot=48.0, taxa=0.14, prazo=self.PRAZO, vol_real=0.35)
        pessimista = opcoes.avaliar_estrutura(pernas, retorno_esperado_ativo=-0.10, **comum)
        otimista = opcoes.avaliar_estrutura(pernas, retorno_esperado_ativo=0.40, **comum)
        self.assertLess(pessimista["resultado_esperado"], otimista["resultado_esperado"])
        self.assertLess(pessimista["probabilidade_lucro"], otimista["probabilidade_lucro"])

    def test_a_premissa_viaja_junto(self):
        """Número sem a premissa parece objetivo quando é opinião."""
        r = opcoes.avaliar_estrutura(
            [{"tipo": "call", "posicao": "compra", "strike": 50, "premio": 1.5}],
            spot=48.0, taxa=0.14, prazo=self.PRAZO, vol_real=0.35,
            retorno_esperado_ativo=0.20)
        p = r["premissas"]
        self.assertEqual(p["retorno_esperado_ativo"], 0.20)
        self.assertEqual(p["volatilidade_esperada"], 0.35)
        self.assertIn("sua opinião", p["aviso_distribuicao"])

    def test_e_deterministico(self):
        """Quadratura, não Monte Carlo: mesmo dado, mesmo número, sempre."""
        pernas = [{"tipo": "put", "posicao": "venda", "strike": 46, "premio": 1.2}]
        comum = dict(spot=48.0, taxa=0.14, prazo=self.PRAZO, vol_real=0.35,
                     retorno_esperado_ativo=0.10)
        a = opcoes.avaliar_estrutura(pernas, **comum)
        b = opcoes.avaliar_estrutura(pernas, **comum)
        self.assertEqual(a["resultado_esperado"], b["resultado_esperado"])
        self.assertEqual(a["probabilidade_lucro"], b["probabilidade_lucro"])

    def test_probabilidade_fica_entre_zero_e_cem(self):
        for chave in estruturas.CATALOGO:
            pernas = estruturas.montar(chave, 48.0, 0.14, 0.35, self.PRAZO)
            r = opcoes.avaliar_estrutura(pernas, 48.0, 0.14, self.PRAZO, 0.35, 0.15)
            self.assertTrue(0.0 <= r["probabilidade_lucro"] <= 100.0, chave)


class TestRecomendacao(unittest.TestCase):
    PRAZO = opcoes.prazo_em_anos(30)

    def _rec(self, **extra):
        base = dict(visao="lateral", objetivo="renda", spot=48.0, taxa=0.14,
                    vol_esperada=0.35, prazo=self.PRAZO)
        base.update(extra)
        return estruturas.recomendar(**base)

    def test_implicita_cara_favorece_quem_vende_premio(self):
        r = self._rec(vol_implicita=0.50, tem_acao=True)
        self.assertEqual(r["preco_volatilidade"], "cara")
        nomes = [s["chave"] for s in r["sugeridas"]]
        self.assertIn("condor_ferro", nomes)

    def test_implicita_barata_favorece_quem_compra_premio(self):
        r = self._rec(objetivo="direcional", vol_implicita=0.22)
        self.assertEqual(r["preco_volatilidade"], "barata")
        self.assertIn("straddle", [s["chave"] for s in r["sugeridas"]])

    def test_vender_premio_caro_rende_mais_que_vender_barato(self):
        """A vantagem inteira das estruturas de crédito: vender a implícita
        alta e realizar volatilidade menor. Precificar os prêmios com a
        volatilidade esperada, em vez da implícita, apagava esse efeito — foi
        um erro real desta implementação."""
        caro = self._rec(vol_implicita=0.50, tem_acao=True)
        barato = self._rec(vol_implicita=0.36, tem_acao=True)
        def condor(r):
            return next(s for s in r["sugeridas"] if s["chave"] == "condor_ferro")
        self.assertGreater(condor(caro)["avaliacao"]["retorno_esperado_pct"],
                           condor(barato)["avaliacao"]["retorno_esperado_pct"])

    def test_estrutura_que_exige_acao_e_rejeitada_com_motivo(self):
        """Recomendar financiamento a quem não tem o papel não é recomendação
        fraca — é impossível."""
        r = self._rec(tem_acao=False)
        rejeitadas = {x["chave"] for x in r["rejeitadas"]}
        self.assertIn("financiamento", rejeitadas)
        self.assertNotIn("financiamento", [s["chave"] for s in r["sugeridas"]])

    def test_com_acao_o_financiamento_aparece(self):
        r = self._rec(tem_acao=True)
        self.assertIn("financiamento", [s["chave"] for s in r["sugeridas"]])

    def test_visao_de_alta_sugere_estrutura_de_alta(self):
        r = self._rec(visao="alta_moderada", objetivo="direcional")
        self.assertEqual(r["sugeridas"][0]["chave"], "trava_alta")

    def test_toda_sugestao_traz_o_quando_nao_usar(self):
        """É a parte que quase nenhuma ferramenta escreve, e a que evita o
        prejuízo."""
        for sugerida in self._rec(tem_acao=True)["sugeridas"]:
            self.assertTrue(sugerida["quando_nao"], sugerida["chave"])
            self.assertTrue(sugerida["razoes"])

    def test_alerta_quando_a_estrutura_compra_premio_caro(self):
        r = self._rec(visao="lateral", objetivo="direcional", vol_implicita=0.60)
        todas = " ".join(" ".join(s["razoes"]) for s in r["sugeridas"])
        self.assertIn("CARA", todas)

    def test_visao_invalida_e_recusada(self):
        self.assertIn("erro", self._rec(visao="vai_subir_muito"))

    def test_classificacao_de_volatilidade(self):
        self.assertEqual(estruturas.classificar_volatilidade(0.50, 0.35)[0], "cara")
        self.assertEqual(estruturas.classificar_volatilidade(0.25, 0.35)[0], "barata")
        self.assertEqual(estruturas.classificar_volatilidade(0.36, 0.35)[0], "justa")
        self.assertEqual(estruturas.classificar_volatilidade(None, 0.35)[0], None)
