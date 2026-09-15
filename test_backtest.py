"""Backtest de 12 meses: pontos mensais, o truque da posição-espelho para
renda fixa, e a reconstrução da série combinada da carteira.

Tudo aqui é função pura, sem rede — quando um teste precisa de série de CDI
ela é injetada, nunca uma chamada real ao BCB.

    python -m unittest test_backtest -v
"""
import unittest
from datetime import date

from modules import backtest, renda_fixa


class TestPontosMensais(unittest.TestCase):
    def test_subtrai_meses_ajusta_dia_no_mes_mais_curto(self):
        self.assertEqual(backtest._subtrair_meses(date(2024, 3, 31), 1),
                         date(2024, 2, 29))  # 2024 é bissexto
        self.assertEqual(backtest._subtrair_meses(date(2023, 3, 31), 1),
                         date(2023, 2, 28))

    def test_subtrai_meses_cruza_o_ano(self):
        self.assertEqual(backtest._subtrair_meses(date(2024, 2, 15), 3),
                         date(2023, 11, 15))

    def test_pontos_mensais_treze_pontos_crescentes_terminando_hoje(self):
        hoje = date(2024, 6, 15)
        pontos = backtest.pontos_mensais(hoje, meses=12)
        self.assertEqual(len(pontos), 13)
        self.assertEqual(pontos[-1], hoje)
        self.assertEqual(pontos, sorted(pontos))
        self.assertEqual(pontos[0], date(2023, 6, 15))


class TestPrecoNaData(unittest.TestCase):
    def test_pega_o_ultimo_fechamento_antes_ou_igual_a_data(self):
        precos = [(date(2024, 1, 2), 10.0), (date(2024, 1, 5), 11.0),
                 (date(2024, 1, 10), 12.0)]
        self.assertEqual(backtest._preco_na_data(precos, date(2024, 1, 7)), 11.0)
        self.assertEqual(backtest._preco_na_data(precos, date(2024, 1, 2)), 10.0)

    def test_sem_fechamento_antes_da_data_devolve_none(self):
        precos = [(date(2024, 2, 1), 10.0)]
        self.assertIsNone(backtest._preco_na_data(precos, date(2024, 1, 1)))


class TestRelativoRendaFixa(unittest.TestCase):
    """O truque da posição-espelho: `marcar_na_curva` com data_aplicacao no
    início da janela devolve exatamente a razão valor(alvo)/valor(início) —
    ver a álgebra na docstring do módulo."""

    def test_pre_fixado_relativo_bate_com_a_formula_direta(self):
        inicio = date(2024, 1, 1)
        alvo = date(2024, 4, 1)
        posicao = {"data_aplicacao": date(2023, 6, 1),
                  "indexador": renda_fixa.PRE, "taxa": 12.0}
        relativo = backtest._relativo_renda_fixa(posicao, inicio, alvo, None, None)
        du = renda_fixa._dias_uteis_aprox(inicio, alvo)
        # `detalhes["fator"]` vem arredondado a 6 casas (ver docstring do
        # módulo) — a comparação usa a mesma precisão, não as 9 casas de um
        # float cru.
        esperado = round(1.12 ** (du / 252), 6)
        self.assertAlmostEqual(relativo, esperado, places=6)

    def test_posicao_aplicada_depois_do_inicio_da_janela_fica_sem_historico(self):
        inicio = date(2024, 1, 1)
        alvo = date(2024, 4, 1)
        posicao = {"data_aplicacao": date(2024, 2, 1),
                  "indexador": renda_fixa.PRE, "taxa": 12.0}
        self.assertIsNone(
            backtest._relativo_renda_fixa(posicao, inicio, alvo, None, None))

    def test_aplicada_exatamente_no_inicio_da_janela_conta(self):
        inicio = date(2024, 1, 1)
        posicao = {"data_aplicacao": inicio,
                  "indexador": renda_fixa.PRE, "taxa": 12.0}
        # No proprio inicio, relativo tem que ser exatamente 1.0.
        self.assertEqual(
            backtest._relativo_renda_fixa(posicao, inicio, inicio, None, None), 1.0)

    def test_pct_cdi_relativo_usa_so_a_serie_do_sub_periodo(self):
        """A série completa começa ANTES da aplicação real — só os pontos
        depois do início da janela e até o alvo podem entrar na conta, senão
        o "rendimento" incluiria dias que já foram descontados uma vez."""
        serie_completa = [
            (date(2023, 12, 15), 0.04),   # antes da janela: nao deve entrar
            (date(2024, 1, 5), 0.04),
            (date(2024, 2, 5), 0.05),
            (date(2024, 3, 5), 0.05),
            (date(2024, 3, 20), 0.05),    # depois do alvo: nao deve entrar
        ]

        def buscar_cdi(ini, fim):
            return [{"data": d.strftime("%d/%m/%Y"), "valor": v}
                   for d, v in serie_completa if ini < d <= fim]

        inicio = date(2024, 1, 1)
        alvo = date(2024, 3, 10)
        posicao = {"data_aplicacao": date(2023, 6, 1),
                  "indexador": renda_fixa.PCT_CDI, "taxa": 100.0}
        relativo = backtest._relativo_renda_fixa(posicao, inicio, alvo, buscar_cdi, None)
        fator_esperado = round(1.0004 * 1.0005 * 1.0005, 6)
        self.assertAlmostEqual(relativo, fator_esperado, places=6)


class TestRelativoFundo(unittest.TestCase):
    def test_relativo_e_cota_alvo_sobre_cota_inicio(self):
        cotas = {("F1", date(2024, 1, 1)): 100.0, ("F1", date(2024, 4, 1)): 110.0}
        buscar = lambda cnpj, data: cotas.get((cnpj, data))
        posicao = {"cnpj": "F1", "identificador": "Fundo X", "peso": 1000.0}
        relativo = backtest._relativo_fundo(posicao, date(2024, 1, 1), date(2024, 4, 1), buscar)
        self.assertAlmostEqual(relativo, 1.10, places=6)

    def test_sem_cota_no_inicio_devolve_none(self):
        buscar = lambda cnpj, data: None
        posicao = {"cnpj": "F1", "identificador": "Fundo X", "peso": 1000.0}
        self.assertIsNone(
            backtest._relativo_fundo(posicao, date(2024, 1, 1), date(2024, 4, 1), buscar))


class TestCarteira12Meses(unittest.TestCase):
    def test_so_acao_com_historico_completo(self):
        datas = [date(2024, 1, 1), date(2024, 4, 1)]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (date(2024, 4, 1), 36.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        resultado = backtest.carteira_12_meses(datas, acoes, precos, [])
        self.assertEqual(resultado["sem_historico"], [])
        self.assertEqual(resultado["cobertura_pct"], 100.0)
        self.assertAlmostEqual(resultado["retorno_periodo_pct"], 20.0, places=2)

    def test_acao_sem_historico_no_inicio_da_janela_fica_de_fora(self):
        datas = [date(2024, 1, 1), date(2024, 4, 1)]
        precos = {"NOVA3": [(date(2024, 2, 1), 10.0), (date(2024, 4, 1), 11.0)]}
        acoes = [{"ticker": "NOVA3", "peso": 500.0}]
        resultado = backtest.carteira_12_meses(datas, acoes, precos, [])
        self.assertEqual(resultado["incluidas"], 0)
        self.assertEqual(resultado["cobertura_pct"], 0.0)
        self.assertIsNotNone(resultado["motivo"])
        self.assertEqual(resultado["sem_historico"],
                         [{"tipo": "acao", "identificador": "NOVA3", "peso": 500.0}])

    def test_mistura_acao_e_renda_fixa_pre_fixada(self):
        inicio = date(2024, 1, 1)
        fim = date(2024, 4, 1)
        datas = [inicio, fim]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (fim, 33.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        rf = [{"identificador": "Banco X", "peso": 1000.0,
              "data_aplicacao": date(2023, 6, 1),
              "indexador": renda_fixa.PRE, "taxa": 10.0}]
        resultado = backtest.carteira_12_meses(datas, acoes, precos, rf)

        du = renda_fixa._dias_uteis_aprox(inicio, fim)
        # `carteira_12_meses` usa o `fator` de 6 casas de `marcar_na_curva`
        # (ver `_relativo_renda_fixa`), não o valor bruto de 1,10 ** (du/252).
        fator_rf = round(1.10 ** (du / 252), 6)
        indice_final_esperado = round((0.5 * 1.10 + 0.5 * fator_rf) * 100.0, 4)
        retorno_esperado = round((indice_final_esperado / 100.0 - 1) * 100.0, 2)

        self.assertEqual(resultado["cobertura_pct"], 100.0)
        self.assertEqual(resultado["serie"][-1]["indice"], indice_final_esperado)
        self.assertEqual(resultado["retorno_periodo_pct"], retorno_esperado)

    def test_renda_fixa_recente_fica_de_fora_sem_derrubar_a_acao(self):
        """A carteira ainda tem um número, feito só das posições com
        histórico completo — a renda fixa recente aparece em sem_historico,
        nunca vira um retorno inventado nem derruba o cálculo inteiro."""
        inicio = date(2024, 1, 1)
        fim = date(2024, 4, 1)
        datas = [inicio, fim]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (fim, 33.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        rf = [{"identificador": "CDB recente", "peso": 200.0,
              "data_aplicacao": date(2024, 2, 1),
              "indexador": renda_fixa.PRE, "taxa": 10.0}]
        resultado = backtest.carteira_12_meses(datas, acoes, precos, rf)
        self.assertEqual(resultado["incluidas"], 1)
        self.assertEqual(resultado["sem_historico"],
                         [{"tipo": "renda_fixa", "identificador": "CDB recente",
                           "peso": 200.0}])
        self.assertAlmostEqual(
            resultado["cobertura_pct"], 1000.0 / 1200.0 * 100.0, places=2)
        self.assertAlmostEqual(resultado["retorno_periodo_pct"], 10.0, places=2)

    def test_carteira_vazia_devolve_motivo_sem_quebrar(self):
        resultado = backtest.carteira_12_meses(
            [date(2024, 1, 1), date(2024, 4, 1)], [], {}, [])
        self.assertEqual(resultado["serie"], [])
        self.assertIsNone(resultado["retorno_periodo_pct"])
        self.assertIsNotNone(resultado["motivo"])

    def test_fundo_com_cota_desde_o_inicio_entra_no_indice(self):
        inicio = date(2024, 1, 1)
        fim = date(2024, 4, 1)
        datas = [inicio, fim]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (fim, 33.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        cotas = {("F1", inicio): 100.0, ("F1", fim): 120.0}
        fundos_entrada = [{"identificador": "Fundo X", "peso": 1000.0, "cnpj": "F1"}]
        resultado = backtest.carteira_12_meses(
            datas, acoes, precos, [], posicoes_fundos=fundos_entrada,
            buscar_cota=lambda cnpj, data: cotas.get((cnpj, data)))
        self.assertEqual(resultado["sem_historico"], [])
        self.assertEqual(resultado["cobertura_pct"], 100.0)
        # ação +10%, fundo +20%, pesos iguais -> índice final 115.
        self.assertEqual(resultado["serie"][-1]["indice"], 115.0)
        self.assertAlmostEqual(resultado["retorno_periodo_pct"], 15.0, places=2)

    def test_fundo_sem_cota_no_inicio_vai_para_sem_historico(self):
        inicio = date(2024, 1, 1)
        fim = date(2024, 4, 1)
        datas = [inicio, fim]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (fim, 33.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        fundos_entrada = [{"identificador": "Fundo Novo", "peso": 500.0, "cnpj": "F2"}]
        resultado = backtest.carteira_12_meses(
            datas, acoes, precos, [], posicoes_fundos=fundos_entrada,
            buscar_cota=lambda cnpj, data: None)
        self.assertEqual(resultado["incluidas"], 1)
        self.assertEqual(resultado["sem_historico"],
                         [{"tipo": "fundo", "identificador": "Fundo Novo", "peso": 500.0}])
        self.assertAlmostEqual(
            resultado["cobertura_pct"], 1000.0 / 1500.0 * 100.0, places=2)

    def test_chamada_antiga_sem_fundos_continua_funcionando(self):
        """Compatibilidade: quem chama sem `posicoes_fundos`/`buscar_cota`
        (assinatura de antes da Etapa B) não pode quebrar."""
        datas = [date(2024, 1, 1), date(2024, 4, 1)]
        precos = {"PETR4": [(date(2023, 12, 1), 30.0), (date(2024, 4, 1), 36.0)]}
        acoes = [{"ticker": "PETR4", "peso": 1000.0}]
        resultado = backtest.carteira_12_meses(datas, acoes, precos, [])
        self.assertEqual(resultado["cobertura_pct"], 100.0)


if __name__ == "__main__":
    unittest.main()
