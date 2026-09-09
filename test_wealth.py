"""Testes do radar de fundos e do otimizador de carteira.

    python -m unittest test_wealth -v
"""
import os
import sys
import types
import unittest

import numpy as np
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
from modules import taxas  # noqa: E402
from routers import wealth  # noqa: E402


def _df_precos(simbolos, dias=120, semente=3, tendencia=0.0):
    idx = pd.date_range("2025-06-02", periods=dias, freq="B")
    rng = np.random.default_rng(semente)
    colunas = {}
    for n, simbolo in enumerate(simbolos):
        base = 100 + np.cumsum(rng.normal(tendencia, 0.8, dias)) + n
        for coluna, valores in (("Close", base), ("Open", base), ("High", base * 1.01),
                                ("Low", base * 0.99), ("Volume", np.full(dias, 1e6))):
            colunas[(simbolo, coluna)] = valores
    df = pd.DataFrame(colunas, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class TestDividendYieldNoRadar(unittest.TestCase):
    """O bug que ordenava a tabela: DY de 0,5% virava 50% e ia para o topo."""

    SEM_INFORME = {"pvp": None, "vp_por_cota": None, "competencia": None,
                   "cnpj": None, "disponivel": False, "origem": "cvm-informe"}
    COM_INFORME = {"pvp": 0.92, "vp_por_cota": 108.0, "competencia": "2026-07-01",
                   "cnpj": "11728688000147", "disponivel": True,
                   "origem": "cvm-informe"}

    def setUp(self):
        self.orig_download = wealth.yf.download
        self.orig_ticker = wealth.yf.Ticker
        self.orig_informe = wealth.fundamentos_fii.pvp_do_fii
        # Estes testes são sobre o Yahoo cair. O informe da CVM entra por
        # decisão explícita — antes ele entrava conforme o fundos_cvm.db
        # existisse na máquina, e o mesmo teste passava aqui e falhava lá.
        wealth.fundamentos_fii.pvp_do_fii = lambda t, p, **k: dict(self.SEM_INFORME)

    def tearDown(self):
        wealth.yf.download = self.orig_download
        wealth.yf.Ticker = self.orig_ticker
        wealth.fundamentos_fii.pvp_do_fii = self.orig_informe

    def test_dy_baixo_nao_infla_e_nao_lidera_o_ranking(self):
        wealth.yf.download = lambda simbolos, *a, **k: _df_precos(list(simbolos))

        # HGLG11 paga pouco; BTLG11 paga bem. O ranking tem que refletir isso.
        rendimentos = {"HGLG11.SA": 0.5, "BTLG11.SA": 11.0}

        class Ticker:
            def __init__(self, simbolo, *a, **k):
                self.info = {"shortName": simbolo, "priceToBook": 0.95,
                             "dividendYield": rendimentos.get(simbolo, 8.0)}

        wealth.yf.Ticker = Ticker
        resposta = wealth.radar_fundos()
        por_ticker = {linha["ticker"]: linha for linha in resposta["tijolo"]}

        self.assertLess(por_ticker["HGLG11"]["dy"], 1.0, "0,5% não pode virar 50%")
        self.assertAlmostEqual(por_ticker["BTLG11"]["dy"], 11.0, places=1)
        self.assertNotEqual(resposta["tijolo"][0]["ticker"], "HGLG11")

    def test_fii_sem_multiplos_entra_sem_recomendacao(self):
        wealth.yf.download = lambda simbolos, *a, **k: _df_precos(list(simbolos))

        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        wealth.yf.Ticker = Bloqueado
        resposta = wealth.radar_fundos()
        self.assertTrue(resposta["tijolo"], "preço existe, a tabela não pode vir vazia")
        self.assertEqual(resposta["com_fundamentos"], 0)
        for linha in resposta["tijolo"]:
            self.assertEqual(linha["recomendacao"], "SEM DADOS")
            self.assertIsNone(linha["dy"])
            self.assertIsNotNone(linha["preco"])

    def test_com_o_informe_da_cvm_o_fii_tem_pvp_mesmo_com_o_yahoo_fora(self):
        """A garantia atual: metade dos FIIs vinha sem P/VP porque só o Yahoo
        alimentava esse campo, e ele é bloqueado no Render. O informe mensal
        sustenta a coluna sozinho."""
        wealth.yf.download = lambda simbolos, *a, **k: _df_precos(list(simbolos))
        wealth.fundamentos_fii.pvp_do_fii = lambda t, p, **k: dict(self.COM_INFORME)

        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        wealth.yf.Ticker = Bloqueado
        resposta = wealth.radar_fundos()
        self.assertTrue(resposta["tijolo"])
        for linha in resposta["tijolo"]:
            self.assertAlmostEqual(linha["pvp"], 0.92)
            self.assertEqual(linha["competencia_vp"], "2026-07-01")
            self.assertNotEqual(linha["recomendacao"], "SEM DADOS")

    def test_ordenacao_poe_dy_ausente_por_ultimo(self):
        linhas = wealth._montar_fiis(
            ["A11", "B11", "C11"],
            _df_precos(["A11.SA", "B11.SA", "C11.SA"]),
            {"A11.SA": {"pvp": 0.9, "dy": 3.0, "nome": "A"},
             "C11.SA": {"pvp": 0.9, "dy": 9.0, "nome": "C"}},
        )
        self.assertEqual([l["ticker"] for l in linhas], ["C11", "A11", "B11"])

    def test_rotulo_de_fii_segue_o_pvp(self):
        self.assertEqual(wealth._recomendacao_fii(0.85)[0], "DESCONTO")
        self.assertEqual(wealth._recomendacao_fii(1.02)[0], "NEUTRO")
        self.assertEqual(wealth._recomendacao_fii(1.30)[0], "ÁGIO")
        self.assertIsNone(wealth._recomendacao_fii(None)[0])


class TestExtrairFechamentos(unittest.TestCase):
    """O yfinance muda o layout das colunas conforme o group_by e a versão.
    O otimizador assumia um só — e quebrava calado no outro."""

    IDX = pd.date_range("2025-01-01", periods=5, freq="B")
    SIMBOLOS = ["PETR4.SA", "VALE3.SA"]

    def test_layout_campo_ticker(self):
        cols = pd.MultiIndex.from_product([["Close", "Volume"], self.SIMBOLOS])
        df = pd.DataFrame(np.arange(20.0).reshape(5, 4), index=self.IDX, columns=cols)
        saida = wealth.extrair_fechamentos(df, self.SIMBOLOS)
        self.assertEqual(list(saida.columns), self.SIMBOLOS)

    def test_layout_ticker_campo(self):
        cols = pd.MultiIndex.from_product([self.SIMBOLOS, ["Close", "Volume"]])
        df = pd.DataFrame(np.arange(20.0).reshape(5, 4), index=self.IDX, columns=cols)
        saida = wealth.extrair_fechamentos(df, self.SIMBOLOS)
        self.assertEqual(list(saida.columns), self.SIMBOLOS)

    def test_ticker_unico(self):
        df = pd.DataFrame({"Close": np.arange(5.0), "Volume": np.arange(5.0)}, index=self.IDX)
        saida = wealth.extrair_fechamentos(df, ["PETR4.SA"])
        self.assertEqual(list(saida.columns), ["PETR4.SA"])

    def test_vazio_devolve_none(self):
        self.assertIsNone(wealth.extrair_fechamentos(pd.DataFrame(), self.SIMBOLOS))
        self.assertIsNone(wealth.extrair_fechamentos(None, self.SIMBOLOS))

    def test_otimizador_funciona_nos_dois_layouts(self):
        def faz_download(layout):
            def download(simbolos, *a, **k):
                idx = pd.date_range("2024-01-01", periods=400, freq="B")
                rng = np.random.default_rng(2)
                dados = {}
                for n, s in enumerate(simbolos):
                    serie = 100 + np.cumsum(rng.normal(0.05, 1.0 + n * 0.4, len(idx)))
                    chave = ("Close", s) if layout == "campo" else (s, "Close")
                    dados[chave] = serie
                df = pd.DataFrame(dados, index=idx)
                df.columns = pd.MultiIndex.from_tuples(df.columns)
                return df
            return download

        original = wealth.yf.download
        try:
            for layout in ("campo", "ticker"):
                wealth.yf.download = faz_download(layout)
                r = wealth.otimizar_markowitz("PETR4,VALE3", selic_aa=14.0, simulacoes=2000)
                self.assertNotIn("erro", r, f"layout {layout} falhou")
                self.assertEqual(len(r["alocacao_otima"]), 2)
        finally:
            wealth.yf.download = original


class TestOtimizador(unittest.TestCase):
    def setUp(self):
        self.orig_download = wealth.yf.download
        self.orig_selic = taxas.obter_selic_meta
        taxas.obter_selic_meta = lambda forcar=False: {
            "valor": 14.0, "data": "16/09/2026", "origem": "bcb"}

    def tearDown(self):
        wealth.yf.download = self.orig_download
        taxas.obter_selic_meta = self.orig_selic

    def _download_simples(self, dias=500):
        def download(simbolos, *a, **k):
            idx = pd.date_range("2024-01-01", periods=dias, freq="B")
            rng = np.random.default_rng(11)
            dados = {}
            for n, simbolo in enumerate(simbolos):
                dados[("Close", simbolo)] = 100 + np.cumsum(
                    rng.normal(0.05 + n * 0.02, 1.0 + n * 0.5, dias))
            df = pd.DataFrame(dados, index=idx)
            df.columns = pd.MultiIndex.from_tuples(df.columns)
            return df
        return download

    def test_pesos_somam_cem(self):
        wealth.yf.download = self._download_simples()
        r = wealth.otimizar_markowitz("PETR4,VALE3,ITUB4", simulacoes=2000)
        self.assertNotIn("erro", r)
        total = sum(item["peso_pct"] for item in r["alocacao_otima"])
        self.assertAlmostEqual(total, 100.0, places=1)

    def test_selic_vem_do_bcb_quando_nao_informada(self):
        wealth.yf.download = self._download_simples()
        r = wealth.otimizar_markowitz("PETR4,VALE3", simulacoes=2000)
        self.assertEqual(r["taxa_livre_risco_aa"], 14.0)
        self.assertEqual(r["origem_taxa_livre_risco"], "bcb")
        self.assertEqual(r["vigencia_taxa_livre_risco"], "16/09/2026")

    def test_parametro_sobrescreve_a_selic(self):
        wealth.yf.download = self._download_simples()
        r = wealth.otimizar_markowitz("PETR4,VALE3", selic_aa=9.5, simulacoes=2000)
        self.assertEqual(r["taxa_livre_risco_aa"], 9.5)
        self.assertEqual(r["origem_taxa_livre_risco"], "parametro")

    def test_taxa_livre_muda_o_sharpe(self):
        """Sharpe é sensível à taxa livre de risco — por isso ela não pode ser
        constante esquecida no código."""
        wealth.yf.download = self._download_simples()
        baixa = wealth.otimizar_markowitz("PETR4,VALE3", selic_aa=2.0, simulacoes=3000)
        alta = wealth.otimizar_markowitz("PETR4,VALE3", selic_aa=20.0, simulacoes=3000)
        self.assertGreater(baixa["sharpe_ratio"], alta["sharpe_ratio"])

    def test_menos_de_dois_ativos_e_erro(self):
        self.assertIn("erro", wealth.otimizar_markowitz("PETR4"))
        self.assertIn("erro", wealth.otimizar_markowitz("PETR4,PETR4"))

    def test_historico_curto_e_recusado(self):
        """Antes o ffill().dropna() aceitava qualquer interseção e a conta saía
        sobre um punhado de pregões."""
        wealth.yf.download = self._download_simples(dias=40)
        r = wealth.otimizar_markowitz("PETR4,VALE3", simulacoes=2000)
        self.assertIn("erro", r)
        self.assertIn("insuficiente", r["erro"])

    def test_download_vazio_nao_estoura(self):
        wealth.yf.download = lambda *a, **k: pd.DataFrame()
        r = wealth.otimizar_markowitz("PETR4,VALE3")
        self.assertIn("erro", r)

    def test_excecao_de_rede_vira_erro_tratado(self):
        wealth.yf.download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sem rede"))
        r = wealth.otimizar_markowitz("PETR4,VALE3")
        self.assertIn("erro", r)
        self.assertIn("RuntimeError", r["erro"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPvpPelaCvm(unittest.TestCase):
    """O caso da tela: metade dos FIIs sem P/VP do Yahoo e, por isso, sem
    recomendação. O Informe Mensal da CVM traz o valor patrimonial da cota."""

    def setUp(self):
        import sqlite3, tempfile, json as _json
        from modules import cadastro_fii, fundamentos_fii
        self.cadastro_fii, self.fundamentos_fii = cadastro_fii, fundamentos_fii
        self.pasta = tempfile.TemporaryDirectory()
        self.banco = os.path.join(self.pasta.name, "fundos.db")
        self.cadastro = os.path.join(self.pasta.name, "cadastro_fii.json")

        cadastro_fii.gravar({"HGLG": {"cnpj": "11728688000147", "nome": "CSHG LOG"},
                             "XPML": {"cnpj": "28757546000100", "nome": "XP MALLS"}},
                            self.cadastro)
        cadastro_fii.ARQUIVO = self.cadastro
        cadastro_fii.ARQUIVO_MANUAL = os.path.join(self.pasta.name, "manual.json")
        cadastro_fii.limpar_memoria()

        conexao = sqlite3.connect(self.banco)
        conexao.execute("""CREATE TABLE fundos (cnpj TEXT PRIMARY KEY, competencia TEXT,
                           vp_por_cota REAL, patrimonio_liquido REAL, cotas_emitidas REAL)""")
        conexao.executemany("INSERT INTO fundos VALUES (?,?,?,?,?)", [
            ("11728688000147", "2026-08-31", 158.24, 5.2e9, 32.9e6),   # HGLG
            ("28757546000100", "2026-08-31", 112.00, 3.1e9, 27.7e6),   # XPML
        ])
        conexao.commit(); conexao.close()
        fundamentos_fii.BANCO = self.banco
        fundamentos_fii.limpar_cache()

    def tearDown(self):
        self.pasta.cleanup()
        self.cadastro_fii.limpar_memoria()
        self.fundamentos_fii.limpar_cache()

    def test_pvp_sai_do_informe_mensal(self):
        r = self.fundamentos_fii.pvp_do_fii("HGLG11", preco=147.75)
        self.assertTrue(r["disponivel"])
        self.assertAlmostEqual(r["pvp"], 147.75 / 158.24, places=6)
        self.assertEqual(r["competencia"], "2026-08-31")
        self.assertAlmostEqual(r["vp_por_cota"], 158.24)

    def test_desconto_patrimonial_vira_rotulo_de_desconto(self):
        """HGLG a 147,75 com VP de 158,24 negocia a 0,93x — abaixo do
        patrimônio, que é o gatilho de entrada."""
        pvp = self.fundamentos_fii.pvp_do_fii("HGLG11", preco=147.75)["pvp"]
        recomendacao, _ = wealth._recomendacao_fii(pvp)
        self.assertEqual(recomendacao, "DESCONTO")

    def test_agio_vira_rotulo_de_agio(self):
        pvp = self.fundamentos_fii.pvp_do_fii("XPML11", preco=140.0)["pvp"]
        self.assertEqual(wealth._recomendacao_fii(pvp)[0], "ÁGIO")

    def test_fundo_fora_do_cadastro(self):
        r = self.fundamentos_fii.pvp_do_fii("ZZZZ11", preco=100.0)
        self.assertFalse(r["disponivel"])
        self.assertIsNone(r["pvp"])

    def test_pvp_implausivel_e_descartado(self):
        r = self.fundamentos_fii.pvp_do_fii("HGLG11", preco=99999.0)
        self.assertIsNone(r["pvp"], "P/VP de 600x é dado corrompido")
        self.assertTrue(r["disponivel"])

    def test_radar_usa_a_cvm_quando_o_yahoo_falha(self):
        orig_dl, orig_tk = wealth.yf.download, wealth.yf.Ticker

        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        wealth.yf.download = lambda simbolos, *a, **k: _df_precos(list(simbolos))
        wealth.yf.Ticker = Bloqueado
        try:
            linhas = wealth._montar_fiis(["HGLG11", "XPML11"],
                                         _df_precos(["HGLG11.SA", "XPML11.SA"]), {})
        finally:
            wealth.yf.download, wealth.yf.Ticker = orig_dl, orig_tk

        por_ticker = {l["ticker"]: l for l in linhas}
        self.assertIsNotNone(por_ticker["HGLG11"]["pvp"], "P/VP tem que vir da CVM")
        self.assertNotEqual(por_ticker["HGLG11"]["recomendacao"], "SEM DADOS")
        self.assertIn("CVM", por_ticker["HGLG11"]["origem_fundamentos"])
        self.assertEqual(por_ticker["HGLG11"]["ponto_entrada"], 158.24,
                         "o ponto de entrada é o valor patrimonial da cota")
