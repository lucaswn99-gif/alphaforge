"""Testes de regressão do scanner quantamental.

Rodam sem rede: yfinance e requests são substituídos por stubs antes do import.
    python -m unittest test_equity -v
"""
import json
import os
import sys
import types
import unittest

import numpy as np
import pandas as pd

# --- stubs (nenhuma chamada de rede nos testes) -----------------------------
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
from modules import composicao_ibov  # noqa: E402
from routers import equity  # noqa: E402

IBOV_FAKE = ["VALE3", "PETR4", "AXIA3", "EMBJ3", "MOTV3", "ITUB4", "BBAS3"]


def _stub_composicao(codigos=None, origem="b3", idade=0):
    return lambda forcar=False: (list(codigos or IBOV_FAKE), origem, idade)


class TestComposicaoIbov(unittest.TestCase):
    def setUp(self):
        self.cache_original = composicao_ibov.CACHE_PATH
        composicao_ibov.CACHE_PATH = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "_cache_ibov_teste.json"
        )
        self.get_original = composicao_ibov.requests.get

    def tearDown(self):
        if os.path.exists(composicao_ibov.CACHE_PATH):
            os.remove(composicao_ibov.CACHE_PATH)
        composicao_ibov.CACHE_PATH = self.cache_original
        composicao_ibov.requests.get = self.get_original

    def _resposta(self, codigos, status=200):
        payload = {"results": [{"cod": c, "asset": c} for c in codigos]}

        class R:
            status_code = status

            def json(self_inner):
                return payload

        return lambda *a, **k: R()

    def test_le_do_b3_e_grava_cache(self):
        codigos_b3 = [f"AAA{i}3" for i in range(60)]
        composicao_ibov.requests.get = self._resposta(codigos_b3)
        codigos, origem, _ = composicao_ibov.obter_composicao(forcar=True)
        self.assertEqual(origem, "b3")
        self.assertEqual(len(codigos), 60)
        self.assertTrue(os.path.exists(composicao_ibov.CACHE_PATH))

    def test_rede_falha_usa_cache(self):
        codigos_b3 = [f"BBB{i}3" for i in range(60)]
        composicao_ibov.requests.get = self._resposta(codigos_b3)
        composicao_ibov.obter_composicao(forcar=True)

        def explode(*a, **k):
            raise RuntimeError("sem rede")

        composicao_ibov.requests.get = explode
        codigos, origem, _ = composicao_ibov.obter_composicao(forcar=True)
        self.assertEqual(origem, "cache")
        self.assertEqual(len(codigos), 60)

    def test_sem_rede_e_sem_cache_cai_no_fallback(self):
        def explode(*a, **k):
            raise RuntimeError("sem rede")

        composicao_ibov.requests.get = explode
        codigos, origem, _ = composicao_ibov.obter_composicao(forcar=True)
        self.assertEqual(origem, "fallback")
        self.assertEqual(codigos, composicao_ibov.IBOV_FALLBACK)

    def test_resposta_truncada_nao_vira_composicao(self):
        """Uma página com 3 papéis não pode substituir a carteira inteira."""
        composicao_ibov.requests.get = self._resposta(["VALE3", "PETR4", "ITUB4"])
        codigos, origem, _ = composicao_ibov.obter_composicao(forcar=True)
        self.assertEqual(origem, "fallback")

    def test_fallback_embutido_e_plausivel(self):
        self.assertGreaterEqual(len(composicao_ibov.IBOV_FALLBACK), composicao_ibov.MINIMO_PLAUSIVEL)
        self.assertEqual(len(composicao_ibov.IBOV_FALLBACK), len(set(composicao_ibov.IBOV_FALLBACK)))

    def test_url_carrega_os_parametros_em_base64(self):
        url = composicao_ibov._montar_url()
        codificado = url.rsplit("/", 1)[-1]
        import base64
        self.assertEqual(json.loads(base64.b64decode(codificado)), composicao_ibov.PARAMETROS)


class TestUniverso(unittest.TestCase):
    def setUp(self):
        self.original = equity.composicao_ibov.obter_composicao
        equity.composicao_ibov.obter_composicao = _stub_composicao()

    def tearDown(self):
        equity.composicao_ibov.obter_composicao = self.original

    def test_universo_soma_indice_e_extras(self):
        codigos, meta = equity.montar_universo()
        self.assertEqual(meta["origem_composicao"], "b3")
        self.assertEqual(meta["papeis_no_indice"], len(IBOV_FAKE))
        for codigo in IBOV_FAKE:
            self.assertIn(codigo, codigos)
        for codigo in equity.ACOES_FORA_DO_INDICE:
            self.assertIn(codigo, codigos)
        self.assertEqual(len(codigos), len(set(codigos)))

    def test_extras_nao_repetem_o_indice(self):
        """Se o B3 promover um papel dos extras, ele não pode duplicar."""
        equity.composicao_ibov.obter_composicao = _stub_composicao(IBOV_FAKE + ["TUPY3"])
        codigos, _ = equity.montar_universo()
        self.assertEqual(codigos.count("TUPY3"), 1)

    def test_dedup_por_simbolo_yahoo(self):
        """AXIA3 e ELET3 resolvem para o mesmo símbolo: uma linha só."""
        equity.composicao_ibov.obter_composicao = _stub_composicao(["AXIA3", "ELET3", "VALE3"])
        mapa = equity.resolver_universo()
        simbolos = list(mapa.keys())
        self.assertEqual(len(simbolos), len(set(simbolos)))
        self.assertEqual(simbolos.count("ELET3.SA"), 1)
        self.assertEqual(mapa["ELET3.SA"], "AXIA3")

    def test_candidatos_em_ordem(self):
        self.assertEqual(equity.candidatos_yahoo("AXIA3"), ["ELET3.SA", "AXIA3.SA"])
        self.assertEqual(equity.candidatos_yahoo("EMBJ3"), ["EMBR3.SA", "EMBJ3.SA"])
        self.assertEqual(equity.candidatos_yahoo("MOTV3"), ["MOTV3.SA", "CCRO3.SA"])
        self.assertEqual(equity.candidatos_yahoo("VALE3"), ["VALE3.SA"])

    def test_embj3_e_o_codigo_corrente(self):
        """EMBJ3 está na carteira do B3; EMBR3 é o símbolo legado do Yahoo."""
        self.assertIn("EMBJ3", composicao_ibov.IBOV_FALLBACK)
        self.assertNotIn("EMBR3", composicao_ibov.IBOV_FALLBACK)
        self.assertEqual(equity.TICKER_ALIASES["EMBJ3"], "EMBR3")


class TestDividendYield(unittest.TestCase):
    def test_fonte_primaria_taxa_sobre_preco(self):
        info = {"dividendRate": 3.40, "currentPrice": 40.0, "dividendYield": 0.5}
        self.assertAlmostEqual(equity.normalizar_dy(info), 8.5, places=6)

    def test_yield_baixo_nao_infla(self):
        """Regressão do bug: DY de 0,5% virava 50% e ganhava o bônus."""
        info = {"dividendYield": 0.5}  # convenção atual: 0,5%
        dy = equity.normalizar_dy(info)
        self.assertLess(dy, 1.0)
        self.assertLess(dy, 6.0, "não pode disparar o bônus de dividendos")

    def test_trailing_fracao_decimal(self):
        info = {"trailingAnnualDividendYield": 0.085}
        self.assertAlmostEqual(equity.normalizar_dy(info), 8.5, places=6)

    def test_valor_absurdo_e_reescalado(self):
        info = {"dividendYield": 850.0}
        self.assertAlmostEqual(equity.normalizar_dy(info), 8.5, places=6)

    def test_ausente_e_none_e_nao_zero(self):
        """Sem dado != "não paga dividendo". O motor precisa distinguir."""
        self.assertIsNone(equity.normalizar_dy({}))
        self.assertIsNone(equity.normalizar_dy({"dividendYield": None}))
        self.assertEqual(equity.normalizar_dy({"dividendYield": 0}), 0.0)


class TestRoe(unittest.TestCase):
    """Fallback de ROE — mérito dos commits que vieram do GitHub, com o
    denominador ausente tratado e teto de plausibilidade."""

    def test_campo_direto_quando_existe(self):
        self.assertAlmostEqual(equity.calcular_roe({"returnOnEquity": 0.185}), 18.5, places=6)

    def test_deriva_de_lucro_sobre_vpa_x_acoes(self):
        info = {"netIncomeToCommon": 30e9, "bookValue": 40.0, "sharesOutstanding": 5e9}
        self.assertAlmostEqual(equity.calcular_roe(info), 15.0, places=6)

    def test_deriva_de_valor_de_mercado_sobre_pvp(self):
        info = {"netIncomeToCommon": 20e9, "marketCap": 200e9, "priceToBook": 2.0}
        self.assertAlmostEqual(equity.calcular_roe(info), 20.0, places=6)

    def test_patrimonio_ausente_nao_vira_roe_astronomico(self):
        """Com denominador 1, a derivação ingênua dava ROE na casa dos trilhões
        e ainda ganhava o bônus de alta rentabilidade."""
        self.assertIsNone(equity.calcular_roe({"netIncomeToCommon": 35e9}))

    def test_sem_dado_nenhum_e_none(self):
        self.assertIsNone(equity.calcular_roe({}))
        self.assertIsNone(equity.calcular_roe({"returnOnEquity": 0}))

    def test_roe_ausente_nao_e_lido_como_prejuizo(self):
        """O caso PETR4: ROE ausente marcava a empresa como em prejuízo."""
        base = dict(pl=8.0, pvp=1.2, dy=7.0, margem_liq=15.0,
                    tendencia_grafica="ALTA", rsi_val=45.0)
        _, veredito, _, alertas = equity.calcular_score_quantamental(roe=None, **base)
        self.assertNotEqual(veredito, "VENDA / ALTO RISCO")
        self.assertTrue(any("Sem dado na fonte" in a and "ROE" in a for a in alertas))

    def test_roe_zero_de_verdade_ainda_e_prejuizo(self):
        base = dict(pl=8.0, pvp=1.2, dy=7.0, margem_liq=15.0,
                    tendencia_grafica="ALTA", rsi_val=45.0)
        _, veredito, _, _ = equity.calcular_score_quantamental(roe=0.0, **base)
        self.assertEqual(veredito, "VENDA / ALTO RISCO")

    def test_roe_alto_pontua_mais_que_roe_nao_apurado(self):
        base = dict(pl=8.0, pvp=1.2, dy=6.0, margem_liq=15.0,
                    tendencia_grafica="ALTA", rsi_val=50.0)
        sem_roe, _, _, _ = equity.calcular_score_quantamental(roe=None, **base)
        com_roe, _, _, _ = equity.calcular_score_quantamental(roe=35.0, **base)
        roe_fraco, _, _, _ = equity.calcular_score_quantamental(roe=6.0, **base)
        self.assertGreater(com_roe, sem_roe)
        self.assertGreater(sem_roe, roe_fraco, "ROE baixo apurado é pior que ROE ausente")


class TestRSI(unittest.TestCase):
    def _wilder_referencia(self, valores, periodo=14):
        """Implementação independente, direto da definição de Wilder."""
        deltas = np.diff(valores)
        ganhos = np.where(deltas > 0, deltas, 0.0)
        perdas = np.where(deltas < 0, -deltas, 0.0)
        mg = ganhos[:periodo].mean()
        mp = perdas[:periodo].mean()
        for i in range(periodo, len(deltas)):
            mg = (mg * (periodo - 1) + ganhos[i]) / periodo
            mp = (mp * (periodo - 1) + perdas[i]) / periodo
        if mp == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + mg / mp)

    def test_bate_com_definicao_de_wilder(self):
        rng = np.random.default_rng(42)
        precos = 100 + np.cumsum(rng.normal(0, 1.2, 260))
        serie = pd.Series(precos, index=pd.date_range("2024-01-01", periods=260, freq="D"))
        self.assertAlmostEqual(
            equity.calcular_rsi_wilder(serie), round(self._wilder_referencia(precos), 1), places=1
        )

    def test_alta_continua_satura_em_100(self):
        serie = pd.Series(np.arange(100, 200, dtype=float))
        self.assertEqual(equity.calcular_rsi_wilder(serie), 100.0)

    def test_serie_curta_devolve_neutro(self):
        self.assertEqual(equity.calcular_rsi_wilder(pd.Series([1.0, 2.0, 3.0])), 50.0)

    def test_divergencia_da_media_simples(self):
        """Confirma que o resultado mudou de fato em relação à versão antiga."""
        rng = np.random.default_rng(7)
        precos = 100 + np.cumsum(rng.normal(0.1, 2.0, 200))
        serie = pd.Series(precos)
        delta = serie.diff()
        g = delta.where(delta > 0, 0).rolling(14).mean()
        p = (-delta.where(delta < 0, 0)).rolling(14).mean()
        antigo = round(float((100 - 100 / (1 + g / p)).iloc[-1]), 1)
        self.assertNotEqual(equity.calcular_rsi_wilder(serie), antigo)


class TestSerieAnualEDestruicao(unittest.TestCase):
    def _serie(self, mapa_ano_precos):
        idx, vals = [], []
        for ano, precos in mapa_ano_precos.items():
            dias = pd.date_range(f"{ano}-01-02", periods=len(precos), freq="D")
            idx.extend(dias)
            vals.extend(precos)
        return pd.Series(vals, index=pd.DatetimeIndex(idx))

    def test_retorno_anual(self):
        serie = self._serie({2024: [100.0, 120.0, 110.0]})
        linha = equity.serie_anual(serie)[0]
        self.assertEqual(linha["ano"], 2024)
        self.assertEqual(linha["fechamento"], 110.0)
        self.assertEqual(linha["maxima"], 120.0)
        self.assertEqual(linha["retorno_ano_pct"], 10.0)

    def test_value_trap_detectado(self):
        serie = self._serie({
            2023: [100.0, 60.0],   # -40%
            2024: [60.0, 36.0],    # -40%
            2025: [36.0, 22.0],    # -38,9%
        })
        self.assertTrue(equity.houve_destruicao_de_capital(equity.serie_anual(serie)))

    def test_menos_de_tres_anos_nao_acusa(self):
        serie = self._serie({2024: [100.0, 40.0], 2025: [40.0, 15.0]})
        self.assertFalse(equity.houve_destruicao_de_capital(equity.serie_anual(serie)))


class TestMotorUnico(unittest.TestCase):
    ENTRADAS = dict(pl=8.0, pvp=1.2, roe=22.0, dy=7.0, margem_liq=15.0,
                    tendencia_grafica="ALTA", rsi_val=45.0)

    def test_value_trap_derruba_o_veredito(self):
        _, sem, _, _ = equity.calcular_score_quantamental(**self.ENTRADAS, destruicao_historica=False)
        score, com, _, alertas = equity.calcular_score_quantamental(**self.ENTRADAS, destruicao_historica=True)
        self.assertIn("COMPRA", sem)
        self.assertEqual(com, "VENDA / ALTO RISCO")
        self.assertLessEqual(score, 35)
        self.assertTrue(any("Value Trap" in a for a in alertas))

    def test_dy_inflado_inflava_o_score(self):
        """O bug de DY somava pontos indevidos. Hoje, além de somar menos, um
        yield implausível ainda dispara alerta."""
        base = dict(pl=30.0, pvp=3.0, roe=10.0, margem_liq=5.0,
                    tendencia_grafica="BAIXA", rsi_val=50.0, dy=0.5)
        s_correto, _, _, _ = equity.calcular_score_quantamental(**base)
        base_bug = dict(base); base_bug["dy"] = 50.0  # 0,5% lido como 50%
        s_bug, _, _, alertas = equity.calcular_score_quantamental(**base_bug)
        self.assertGreater(s_bug, s_correto)
        self.assertTrue(any("raramente é recorrente" in a for a in alertas))


class TestFatiarPrecos(unittest.TestCase):
    def _df_multi(self):
        idx = pd.date_range("2024-01-01", periods=5, freq="D")
        cols = pd.MultiIndex.from_product([["VALE3.SA", "PETR4.SA"], ["Close", "Volume"]])
        dados = np.arange(20, dtype=float).reshape(5, 4)
        df = pd.DataFrame(dados, index=idx, columns=cols)
        df[("VALE3.SA", "Volume")] = [1.0, np.nan, 3.0, 4.0, 5.0]  # NaN só no volume
        return df

    def test_nan_em_volume_nao_derruba_o_pregao(self):
        sub = equity.fatiar_precos(self._df_multi(), "VALE3.SA")
        self.assertEqual(len(sub), 5, "dropna() antigo derrubaria a linha inteira")

    def test_nan_em_close_derruba(self):
        df = self._df_multi()
        df[("VALE3.SA", "Close")] = [1.0, np.nan, 3.0, 4.0, 5.0]
        self.assertEqual(len(equity.fatiar_precos(df, "VALE3.SA")), 4)

    def test_ticker_ausente(self):
        self.assertIsNone(equity.fatiar_precos(self._df_multi(), "XXXX3.SA"))


class TestColetaResiliente(unittest.TestCase):
    def test_retry_e_sucesso_na_segunda(self):
        chamadas = {"n": 0}

        class Falha:
            def __init__(self, *a, **k):
                chamadas["n"] += 1
                if chamadas["n"] == 1:
                    raise RuntimeError("429 Too Many Requests")
                self.info = {"shortName": "VALE", "trailingPE": 6.0, "returnOnEquity": 0.2}

        orig = equity.yf.Ticker
        equity.yf.Ticker = Falha
        try:
            dados, motivo = equity.extrair_fundamentos("VALE3.SA", tentativas=2)
        finally:
            equity.yf.Ticker = orig
        self.assertIsNone(motivo)
        self.assertEqual(dados["nome"], "VALE")
        self.assertAlmostEqual(dados["roe"], 20.0)

    def test_payload_sem_symbol_e_aceito(self):
        """Regressão: exigir a chave 'symbol' descartava papéis válidos."""
        self.assertTrue(equity._info_tem_conteudo({"trailingPE": 9.0}))
        self.assertFalse(equity._info_tem_conteudo({}))
        self.assertFalse(equity._info_tem_conteudo(None))

    def test_falha_reporta_motivo(self):
        class SempreFalha:
            def __init__(self, *a, **k):
                raise RuntimeError("boom")

        orig = equity.yf.Ticker
        equity.yf.Ticker = SempreFalha
        try:
            dados, motivo = equity.extrair_fundamentos("XXXX3.SA", tentativas=1)
        finally:
            equity.yf.Ticker = orig
        self.assertIsNone(dados)
        self.assertEqual(motivo, "RuntimeError")


def _df_precos(simbolos, dias=700, semente=1):
    idx = pd.date_range("2023-01-02", periods=dias, freq="B")
    rng = np.random.default_rng(semente)
    colunas = {}
    for simbolo in simbolos:
        base = 100 + np.cumsum(rng.normal(0.05, 1.5, dias))
        for coluna, valores in (("Close", base), ("Open", base), ("High", base * 1.01),
                                ("Low", base * 0.99), ("Volume", np.full(dias, 1e6))):
            colunas[(simbolo, coluna)] = valores
    df = pd.DataFrame(colunas, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class TestDuasPassadas(unittest.TestCase):
    """MOTV3 só existe no Yahoo como CCRO3.SA: a 2ª passada tem que salvá-lo."""

    def setUp(self):
        self.orig_comp = equity.composicao_ibov.obter_composicao
        self.orig_download = equity.yf.download
        self.orig_ticker = equity.yf.Ticker
        self.orig_tentativas = equity.SCANNER_TENTATIVAS
        equity.SCANNER_TENTATIVAS = 1
        equity.ACOES_FORA_DO_INDICE_backup = list(equity.ACOES_FORA_DO_INDICE)
        equity.ACOES_FORA_DO_INDICE.clear()
        equity.composicao_ibov.obter_composicao = _stub_composicao(["VALE3", "MOTV3", "XXXX3"])

        # O Yahoo conhece VALE3.SA e CCRO3.SA; MOTV3.SA e XXXX3.SA não existem.
        conhecidos = {"VALE3.SA", "CCRO3.SA"}

        def download(simbolos, *a, **k):
            validos = [s for s in simbolos if s in conhecidos]
            return _df_precos(validos) if validos else pd.DataFrame()

        class Ticker:
            def __init__(self, simbolo, *a, **k):
                if simbolo not in conhecidos:
                    raise RuntimeError("404 Not Found")
                self.info = {"shortName": simbolo, "trailingPE": 8.0, "priceToBook": 1.2,
                             "returnOnEquity": 0.18, "profitMargins": 0.1,
                             "dividendRate": 2.0, "currentPrice": 40.0}

        equity.yf.download = download
        equity.yf.Ticker = Ticker
        equity._cache_scanner["payload"] = None
        equity._cache_scanner["carimbo"] = 0.0

    def tearDown(self):
        equity.composicao_ibov.obter_composicao = self.orig_comp
        equity.yf.download = self.orig_download
        equity.yf.Ticker = self.orig_ticker
        equity.SCANNER_TENTATIVAS = self.orig_tentativas
        equity.ACOES_FORA_DO_INDICE.extend(equity.ACOES_FORA_DO_INDICE_backup)
        equity._cache_scanner["payload"] = None
        equity._cache_scanner["carimbo"] = 0.0

    def test_alternativo_resgata_o_papel_renomeado(self):
        resposta = equity.executar_scanner(forcar=True)
        tickers = {linha["ticker"]: linha["simbolo_yahoo"] for linha in resposta["oportunidades"]}
        self.assertEqual(tickers.get("MOTV3"), "CCRO3.SA")
        self.assertEqual(tickers.get("VALE3"), "VALE3.SA")
        self.assertEqual(resposta["total"], 2)

    def test_papel_inexistente_vira_falha_explicita(self):
        resposta = equity.executar_scanner(forcar=True)
        falhas = {f["ticker"] for f in resposta["falhas"]}
        self.assertEqual(falhas, {"XXXX3"})
        self.assertNotIn("MOTV3", falhas, "não pode reportar falha de papel resolvido depois")

    def test_metadados_da_composicao_na_resposta(self):
        resposta = equity.executar_scanner(forcar=True)
        self.assertEqual(resposta["origem_composicao"], "b3")
        self.assertEqual(resposta["papeis_no_indice"], 3)
        self.assertEqual(resposta["solicitados"], 3)


class TestFundamentosOpcionais(unittest.TestCase):
    """O caso que esvaziava a tabela: rate limit no .info derrubava os 95
    papéis, mesmo com a série de preço tendo vindo inteira."""

    SEM_CVM = {"pl": None, "pvp": None, "roe": None, "margem_liq": None,
               "exercicio": None, "disponivel": False}
    COM_CVM = {"pl": 6.0, "pvp": 1.0, "roe": 18.0, "margem_liq": 12.0,
               "exercicio": 2025, "disponivel": True}

    def setUp(self):
        self.orig_comp = equity.composicao_ibov.obter_composicao
        self.orig_download = equity.yf.download
        self.orig_ticker = equity.yf.Ticker
        self.orig_tentativas = equity.SCANNER_TENTATIVAS
        self.orig_cvm = equity.fundamentos_cvm.multiplos_do_ticker
        # Estes testes são sobre o Yahoo cair. A base da CVM entra ou não por
        # decisão explícita — antes ela entrava conforme o arquivo .db existisse
        # na máquina, e o mesmo teste passava aqui e falhava na do Lucas.
        equity.fundamentos_cvm.multiplos_do_ticker = lambda t, **k: dict(self.SEM_CVM)
        self.extras = list(equity.ACOES_FORA_DO_INDICE)
        equity.SCANNER_TENTATIVAS = 1
        equity.ACOES_FORA_DO_INDICE.clear()
        equity.composicao_ibov.obter_composicao = _stub_composicao(["VALE3", "PETR4", "ITUB4"])
        equity.yf.download = lambda simbolos, *a, **k: _df_precos(list(simbolos))
        equity._cache_scanner["payload"] = None
        equity._cache_scanner["carimbo"] = 0.0

    def tearDown(self):
        equity.composicao_ibov.obter_composicao = self.orig_comp
        equity.yf.download = self.orig_download
        equity.yf.Ticker = self.orig_ticker
        equity.SCANNER_TENTATIVAS = self.orig_tentativas
        equity.fundamentos_cvm.multiplos_do_ticker = self.orig_cvm
        equity.ACOES_FORA_DO_INDICE.extend(self.extras)
        equity._cache_scanner["payload"] = None
        equity._cache_scanner["carimbo"] = 0.0

    def test_rate_limit_total_ainda_devolve_a_tabela(self):
        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        equity.yf.Ticker = Bloqueado
        resposta = equity.executar_scanner(forcar=True)

        self.assertEqual(resposta["total"], 3, "a tabela não pode vir vazia com preço disponível")
        self.assertEqual(resposta["com_fundamentos"], 0)
        self.assertEqual(resposta["falhas"], [])
        self.assertEqual(len(resposta["sem_fundamentos"]), 3)
        for linha in resposta["oportunidades"]:
            self.assertFalse(linha["fundamentos_disponiveis"])
            self.assertIsNone(linha["roe"])
            self.assertIsNone(linha["pl"])
            self.assertIsNotNone(linha["preco"])
            self.assertIn(linha["tendencia_grafica"], ("ALTA", "BAIXA"))

    def test_sem_fundamentos_nao_vira_veredito_de_prejuizo(self):
        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        equity.yf.Ticker = Bloqueado
        resposta = equity.executar_scanner(forcar=True)
        for linha in resposta["oportunidades"]:
            self.assertEqual(linha["veredito"], "SEM DADOS FUNDAMENTALISTAS")
            self.assertLessEqual(linha["score_geral"], 55)

    def test_mistura_de_papeis_com_e_sem_fundamentos(self):
        class Parcial:
            def __init__(self, simbolo, *a, **k):
                if simbolo == "VALE3.SA":
                    raise RuntimeError("YFRateLimitError")
                self.info = {"shortName": simbolo, "trailingPE": 7.0, "priceToBook": 1.1,
                             "returnOnEquity": 0.2, "profitMargins": 0.15,
                             "dividendRate": 3.0, "currentPrice": 50.0}

        equity.yf.Ticker = Parcial
        resposta = equity.executar_scanner(forcar=True)
        self.assertEqual(resposta["total"], 3)
        self.assertEqual(resposta["com_fundamentos"], 2)
        self.assertEqual([f["ticker"] for f in resposta["sem_fundamentos"]], ["VALE3"])

    def test_com_a_base_da_cvm_o_yahoo_fora_do_ar_nao_esvazia_os_multiplos(self):
        """A garantia atual, e o motivo de a versão anterior deste teste ter
        virado do avesso: no Render o quoteSummary não responde nunca, e é a
        CVM que sustenta a tabela. Se isto quebrar, o scanner em produção volta
        a ficar sem múltiplos."""
        equity.fundamentos_cvm.multiplos_do_ticker = lambda t, **k: dict(self.COM_CVM)

        class Bloqueado:
            def __init__(self, *a, **k):
                raise RuntimeError("YFRateLimitError")

        equity.yf.Ticker = Bloqueado
        resposta = equity.executar_scanner(forcar=True)

        self.assertEqual(resposta["total"], 3)
        self.assertEqual(resposta["com_fundamentos"], 3)
        for linha in resposta["oportunidades"]:
            self.assertAlmostEqual(linha["roe"], 18.0)
            self.assertEqual(linha["exercicio_cvm"], 2025)
            self.assertIn("CVM 2025", linha["origem_fundamentos"])
            self.assertNotIn("SEM DADOS", linha["veredito"])


class TestMotorComIndicadoresAusentes(unittest.TestCase):
    def test_tudo_ausente_fica_neutro_e_avisa(self):
        score, veredito, positivos, alertas = equity.calcular_score_quantamental(
            pl=None, pvp=None, roe=None, dy=None, margem_liq=None,
            tendencia_grafica="ALTA", rsi_val=50.0,
        )
        self.assertEqual(veredito, "SEM DADOS FUNDAMENTALISTAS")
        self.assertLessEqual(score, 55, "preço puro não pode alcançar faixa de COMPRA")
        self.assertTrue(any("Sem dado na fonte" in a for a in alertas))

    def test_multiplo_bom_pontua_mais_que_multiplo_caro(self):
        """Asserção de ordem, não de delta exato: assim o teste sobrevive a
        recalibração das faixas, que é o ponto de elas serem configuráveis."""
        base = dict(pvp=2.0, roe=10.0, dy=1.0, margem_liq=5.0,
                    tendencia_grafica="BAIXA", rsi_val=50.0)
        pl_barato, _, _, _ = equity.calcular_score_quantamental(pl=4.0, **base)
        pl_medio, _, _, _ = equity.calcular_score_quantamental(pl=13.0, **base)
        pl_caro, _, _, _ = equity.calcular_score_quantamental(pl=40.0, **base)
        self.assertGreater(pl_barato, pl_medio)
        self.assertGreater(pl_medio, pl_caro)

    def test_faixas_discriminam_dentro_da_zona_boa(self):
        """O bug que empatava os dez primeiros em 98: P/L 5 e P/L 11 recebiam
        os mesmos pontos, e ROE 15,5% empatava com ROE 68,5%."""
        base = dict(pvp=1.5, dy=7.0, margem_liq=15.0,
                    tendencia_grafica="ALTA", rsi_val=50.0)
        muito_barato, _, _, _ = equity.calcular_score_quantamental(pl=5.0, roe=16.0, **base)
        quase_caro, _, _, _ = equity.calcular_score_quantamental(pl=11.0, roe=16.0, **base)
        self.assertGreater(muito_barato, quase_caro, "P/L 5 tem que valer mais que P/L 11")

        roe_alto, _, _, _ = equity.calcular_score_quantamental(pl=9.0, roe=68.5, **base)
        roe_baixo, _, _, _ = equity.calcular_score_quantamental(pl=9.0, roe=15.5, **base)
        self.assertGreater(roe_alto, roe_baixo, "ROE 68,5% tem que valer mais que 15,5%")

    def test_nenhum_papel_plausivel_encosta_no_teto(self):
        """Score que satura vira empate, e empate não ordena."""
        excelente, _, _, _ = equity.calcular_score_quantamental(
            pl=3.0, pvp=0.5, roe=70.0, dy=11.0, margem_liq=35.0,
            tendencia_grafica="ALTA", rsi_val=25.0)
        self.assertLess(excelente, 100)
        self.assertGreaterEqual(excelente, equity.CORTE_COMPRA_FORTE)

    def test_cobertura_parcial_e_normalizada(self):
        """3 de 5 indicadores são pontuados sobre o máximo que esses três
        permitem — o papel não é punido por não ter os outros dois."""
        completo, _, _, _ = equity.calcular_score_quantamental(
            pl=5.0, pvp=0.8, roe=30.0, dy=8.0, margem_liq=20.0,
            tendencia_grafica="ALTA", rsi_val=50.0)
        parcial, _, _, _ = equity.calcular_score_quantamental(
            pl=5.0, pvp=0.8, roe=30.0, dy=None, margem_liq=None,
            tendencia_grafica="ALTA", rsi_val=50.0)
        self.assertGreater(parcial, 55, "cobertura parcial boa não pode virar nota baixa")
        self.assertLess(abs(completo - parcial), 15)

    def test_cobertura_rala_nao_vira_recomendacao(self):
        """Um DY sozinho não sustenta COMPRA: o motor exige 3 dos 5."""
        score, veredito, _, _ = equity.calcular_score_quantamental(
            pl=None, pvp=None, roe=None, dy=9.0, margem_liq=None,
            tendencia_grafica="ALTA", rsi_val=30.0,
        )
        self.assertTrue(veredito.startswith("DADOS PARCIAIS"))
        self.assertIn("1/5", veredito)
        self.assertLessEqual(score, 55)

    def test_tres_indicadores_ja_permitem_veredito(self):
        _, veredito, _, _ = equity.calcular_score_quantamental(
            pl=8.0, pvp=1.0, roe=20.0, dy=None, margem_liq=None,
            tendencia_grafica="ALTA", rsi_val=45.0,
        )
        self.assertIn("COMPRA", veredito)

    def test_prejuizo_vence_a_cobertura_rala(self):
        """Margem negativa é resultado apurado: o veto vale mesmo com 1 de 5."""
        _, veredito, _, _ = equity.calcular_score_quantamental(
            pl=None, pvp=None, roe=None, dy=None, margem_liq=-12.0,
            tendencia_grafica="ALTA", rsi_val=50.0,
        )
        self.assertEqual(veredito, "VENDA / ALTO RISCO")

    def test_margem_ausente_nao_e_prejuizo(self):
        _, veredito, _, _ = equity.calcular_score_quantamental(
            pl=8.0, pvp=1.0, roe=None, dy=None, margem_liq=None,
            tendencia_grafica="ALTA", rsi_val=50.0,
        )
        self.assertNotEqual(veredito, "VENDA / ALTO RISCO")


class TestCarimboDeColeta(unittest.TestCase):
    def test_campos_do_carimbo(self):
        carimbo = equity.carimbo_de_coleta()
        self.assertEqual(carimbo["atraso_fonte_minutos"], 15)
        self.assertIn("Yahoo", carimbo["fonte_cotacao"])
        self.assertRegex(carimbo["coletado_em_legivel"], r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}$")
        from datetime import datetime
        datetime.fromisoformat(carimbo["coletado_em"])  # não pode levantar

    def test_fuso_e_de_sao_paulo(self):
        from datetime import datetime, timezone
        carimbo = equity.carimbo_de_coleta()
        momento = datetime.fromisoformat(carimbo["coletado_em"])
        self.assertIsNotNone(momento.tzinfo)
        agora = datetime.now(timezone.utc)
        self.assertLess(abs((momento - agora).total_seconds()), 120)

    def test_cache_preserva_o_horario_da_coleta_original(self):
        """O carimbo tem que ser o da coleta, não o do momento da resposta —
        senão um resultado de 14 minutos atrás se apresenta como novo."""
        import time as _t
        payload = {"total": 1, "solicitados": 1, "falhas": [], "oportunidades": [],
                   "coletado_em_legivel": "01/01/2026 09:30",
                   "coletado_em": "2026-01-01T09:30:00-03:00",
                   "fonte_cotacao": equity.FONTE_COTACAO, "atraso_fonte_minutos": 15}
        equity._cache_scanner["payload"] = payload
        equity._cache_scanner["carimbo"] = _t.time() - 600
        try:
            resp = equity.executar_scanner(forcar=False)
            self.assertTrue(resp["cache"])
            self.assertEqual(resp["coletado_em_legivel"], "01/01/2026 09:30")
            self.assertGreaterEqual(resp["idade_segundos"], 599)
        finally:
            equity._cache_scanner["payload"] = None
            equity._cache_scanner["carimbo"] = 0.0


class TestCacheScanner(unittest.TestCase):
    def test_segunda_chamada_usa_cache(self):
        import time as _t
        equity._cache_scanner["payload"] = {"total": 3, "solicitados": 3, "falhas": [], "oportunidades": []}
        equity._cache_scanner["carimbo"] = _t.time()
        try:
            resp = equity.executar_scanner(forcar=False)
            self.assertTrue(resp["cache"])
            self.assertEqual(resp["total"], 3)
        finally:
            equity._cache_scanner["payload"] = None
            equity._cache_scanner["carimbo"] = 0.0

    def test_erro_de_rede_nao_estoura(self):
        orig_comp = equity.composicao_ibov.obter_composicao
        orig_dl = equity.yf.download
        equity.composicao_ibov.obter_composicao = _stub_composicao(["VALE3"], origem="fallback")
        equity.yf.download = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sem rede"))
        try:
            resp = equity.executar_scanner(forcar=True)
            self.assertEqual(resp["total"], 0)
            self.assertEqual(resp["oportunidades"], [])
            self.assertTrue(resp["falhas"], "a falha de rede tem que aparecer no relatório")
        finally:
            equity.composicao_ibov.obter_composicao = orig_comp
            equity.yf.download = orig_dl
            equity._cache_scanner["payload"] = None
            equity._cache_scanner["carimbo"] = 0.0


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFundamentosPorDemonstrativo(unittest.TestCase):
    """O caso do print: preço e SMA50 vieram, `.info` não. Balanço e DRE usam
    outro endpoint do Yahoo e permitem reconstruir os múltiplos."""

    PRECO = 79.52

    def _ativo(self, balanco=None, dre=None, proventos=None, info=None, explode_info=False):
        colunas = pd.to_datetime(["2025-12-31", "2024-12-31"])

        def frame(linhas):
            if linhas is None:
                return pd.DataFrame()
            return pd.DataFrame({colunas[0]: [v[0] for v in linhas.values()],
                                 colunas[1]: [v[1] for v in linhas.values()]},
                                index=list(linhas))

        class Ativo:
            dividends = proventos if proventos is not None else pd.Series(dtype=float)

            def __init__(self_inner):
                if explode_info:
                    raise RuntimeError("nao usar")

            @property
            def info(self_inner):
                if explode_info:
                    raise RuntimeError("YFRateLimitError")
                return info or {}

            balance_sheet = frame(balanco)
            income_stmt = frame(dre)

        return Ativo()

    def test_reconstroi_roe_margem_pl_e_pvp(self):
        ativo = self._ativo(
            balanco={"Stockholders Equity": [200e9, 190e9], "Share Issued": [4.3e9, 4.3e9]},
            dre={"Net Income": [40e9, 35e9], "Total Revenue": [200e9, 190e9]},
        )
        d = equity.fundamentos_por_demonstrativo(ativo, self.PRECO)
        self.assertAlmostEqual(d["roe"], 20.0, places=4)          # 40/200
        self.assertAlmostEqual(d["margem_liq"], 20.0, places=4)   # 40/200
        self.assertAlmostEqual(d["pvp"], (79.52 * 4.3e9) / 200e9, places=6)
        self.assertAlmostEqual(d["pl"], 79.52 / (40e9 / 4.3e9), places=6)

    def test_rotulo_alternativo_e_aceito(self):
        """O Yahoo varia o nome da linha entre empresas."""
        ativo = self._ativo(
            balanco={"Common Stock Equity": [100e9, 90e9]},
            dre={"Net Income Common Stockholders": [15e9, 12e9]},
        )
        self.assertAlmostEqual(equity.fundamentos_por_demonstrativo(ativo, self.PRECO)["roe"], 15.0)

    def test_dy_soma_so_os_proventos_dos_ultimos_12_meses(self):
        hoje = pd.Timestamp.now().normalize()
        datas = pd.to_datetime([hoje - pd.Timedelta(days=d) for d in (900, 400, 200, 30)])
        proventos = pd.Series([1.0, 1.5, 2.0, 2.5], index=datas)
        ativo = self._ativo(proventos=proventos)
        # Janela de 12 meses a partir de hoje: 2,0 + 2,5 = 4,5
        self.assertAlmostEqual(equity.fundamentos_por_demonstrativo(ativo, 100.0)["dy"], 4.5, places=6)

    def test_empresa_que_parou_de_pagar_tem_dy_zero(self):
        """Ancorar a janela no último provento inflava o DY de quem parou."""
        antigos = pd.to_datetime([pd.Timestamp.now().normalize() - pd.Timedelta(days=d)
                                  for d in (900, 800)])
        ativo = self._ativo(proventos=pd.Series([3.0, 3.0], index=antigos))
        self.assertEqual(equity.fundamentos_por_demonstrativo(ativo, 100.0)["dy"], 0.0)

    def test_sem_serie_de_proventos_e_none_e_nao_zero(self):
        self.assertIsNone(equity.fundamentos_por_demonstrativo(self._ativo(), 100.0)["dy"])

    def test_sem_demonstrativo_devolve_tudo_none(self):
        d = equity.fundamentos_por_demonstrativo(self._ativo(), self.PRECO)
        for chave in ("pl", "pvp", "roe", "margem_liq", "dy"):
            self.assertIsNone(d[chave])

    def test_patrimonio_negativo_nao_produz_roe(self):
        ativo = self._ativo(balanco={"Stockholders Equity": [-5e9, -4e9]},
                            dre={"Net Income": [1e9, 1e9]})
        self.assertIsNone(equity.fundamentos_por_demonstrativo(ativo, self.PRECO)["roe"])

    def test_prejuizo_nao_vira_pl_negativo(self):
        """P/L com lucro negativo não tem leitura útil: fica None."""
        ativo = self._ativo(balanco={"Stockholders Equity": [100e9, 90e9], "Share Issued": [1e9, 1e9]},
                            dre={"Net Income": [-8e9, -5e9], "Total Revenue": [50e9, 48e9]})
        d = equity.fundamentos_por_demonstrativo(ativo, self.PRECO)
        self.assertIsNone(d["pl"])
        self.assertAlmostEqual(d["roe"], -8.0, places=4)   # prejuízo real aparece
        self.assertAlmostEqual(d["margem_liq"], -16.0, places=4)

    def test_roe_implausivel_e_descartado(self):
        ativo = self._ativo(balanco={"Stockholders Equity": [1.0, 1.0]},
                            dre={"Net Income": [50e9, 50e9]})
        self.assertIsNone(equity.fundamentos_por_demonstrativo(ativo, self.PRECO)["roe"])


class TestCascataUnica(unittest.TestCase):
    """O scanner e a consulta detalhada têm que ler a mesma coisa.

    Regressão do defeito relatado em 08/09/2026: PETR4 saía COMPRA na tabela
    (P/L 5.63, ROE 26.5%, do balanço da CVM) e VENDA na consulta detalhada
    (P/L 31.6, P/VP 8.20, do `.info` do Yahoo), no mesmo preço e no mesmo
    minuto. Cada rota tinha a sua própria cascata de fontes.
    """

    CVM_PETR4 = {"pl": 5.63, "pvp": 1.10, "roe": 26.5, "margem_liq": 22.0,
                 "origem": "cvm", "exercicio": 2025, "cnpj": "33000167000101",
                 "denominacao": "PETROBRAS", "disponivel": True}
    YAHOO_PETR4 = {"pl": 31.6, "pvp": 8.20, "roe": 26.0, "margem_liq": 22.0,
                   "dy": 7.6, "nome": "PETROBRAS PN", "setor": "Energy"}

    def setUp(self):
        self._original = equity.fundamentos_cvm.multiplos_do_ticker

    def tearDown(self):
        equity.fundamentos_cvm.multiplos_do_ticker = self._original

    def _stub_cvm(self, payload):
        equity.fundamentos_cvm.multiplos_do_ticker = lambda t, **k: dict(payload)

    def test_balanco_vence_o_yahoo(self):
        self._stub_cvm(self.CVM_PETR4)
        r = equity.resolver_fundamentos("PETR4", 48.09, info_yahoo=self.YAHOO_PETR4)
        self.assertAlmostEqual(r["valores"]["pl"], 5.63)
        self.assertAlmostEqual(r["valores"]["pvp"], 1.10)
        self.assertEqual(r["exercicio_cvm"], 2025)
        self.assertIn("CVM 2025", r["origens"])

    def test_divergencia_grande_vira_alerta(self):
        self._stub_cvm(self.CVM_PETR4)
        r = equity.resolver_fundamentos("PETR4", 48.09, info_yahoo=self.YAHOO_PETR4)
        campos = {d["campo"] for d in r["divergencias"]}
        self.assertEqual(campos, {"P/L", "P/VP"})   # ROE e margem batem
        for item in r["divergencias"]:
            self.assertLess(item["balanco"], item["yahoo"])

    def test_yahoo_preenche_o_que_a_cvm_nao_tem(self):
        """A DFP não publica proventos: o DY continua vindo do Yahoo."""
        self._stub_cvm(self.CVM_PETR4)
        r = equity.resolver_fundamentos("PETR4", 48.09, info_yahoo=self.YAHOO_PETR4)
        self.assertAlmostEqual(r["valores"]["dy"], 7.6)
        self.assertIn("quoteSummary", r["origens"])
        self.assertEqual(r["valores"]["setor"], "Energy")

    def test_sem_yahoo_o_resultado_e_o_mesmo(self):
        """O ponto do conserto: o Render (sem quoteSummary) e o navegador
        (com quoteSummary) precisam chegar ao mesmo veredito."""
        self._stub_cvm(self.CVM_PETR4)
        com = equity.resolver_fundamentos("PETR4", 48.09, info_yahoo=self.YAHOO_PETR4)
        sem = equity.resolver_fundamentos("PETR4", 48.09, info_yahoo=None)
        for campo in ("pl", "pvp", "roe", "margem_liq"):
            self.assertEqual(com["valores"][campo], sem["valores"][campo])

        # O DY não vem da DFP; nas duas rotas ele sai de provento (quoteSummary
        # no navegador, série de preço no Render). Fixando o DY, o veredito só
        # pode diferir se os múltiplos de balanço diferirem — e não diferem.
        def _veredito(valores):
            return equity.calcular_score_quantamental(
                pl=valores["pl"], pvp=valores["pvp"], roe=valores["roe"],
                dy=7.6, margem_liq=valores["margem_liq"],
                tendencia_grafica="ALTA", rsi_val=73.4, destruicao_historica=False,
            )[1]
        self.assertEqual(_veredito(com["valores"]), _veredito(sem["valores"]))
        self.assertIn("COMPRA", _veredito(com["valores"]))

    def test_sem_cvm_o_yahoo_assume(self):
        self._stub_cvm({"disponivel": False, "pl": None, "pvp": None,
                        "roe": None, "margem_liq": None, "exercicio": None})
        r = equity.resolver_fundamentos("XPTO3", 10.0, info_yahoo=self.YAHOO_PETR4)
        self.assertAlmostEqual(r["valores"]["pl"], 31.6)
        self.assertEqual(r["divergencias"], [])
        self.assertEqual(r["origens"], ["quoteSummary"])

    def test_sem_fonte_alguma_devolve_none(self):
        self._stub_cvm({"disponivel": False, "pl": None, "pvp": None,
                        "roe": None, "margem_liq": None, "exercicio": None})
        r = equity.resolver_fundamentos("XPTO3", 10.0)
        for campo in equity.CAMPOS_FUNDAMENTAIS:
            self.assertIsNone(r["valores"][campo])
        self.assertEqual(r["origens"], [])


class TestVereditoEmExercicioAtipico(unittest.TestCase):
    """VALE3, 2025: o motor lia o balanço corretamente e ainda assim concluía
    errado. Impairment de R$ 25,1 bi sobre EBIT de R$ 31,9 bi derruba o lucro,
    infla o P/L e faz o score cair — mas isso descreve o evento, não o negócio.
    """

    ATIPICO = {"contaminado": True, "perdas": 25.147e9, "proporcao_ebit": 0.787}
    NORMAL = {"contaminado": False, "perdas": 0.5e9, "proporcao_ebit": 0.016}

    # Perfil do papel no ano contaminado: P/L alto e ROE baixo por causa do
    # lucro deprimido, mas margem e dividendos ainda de pé.
    ENTRADAS = dict(pl=30.7, pvp=1.7, roe=6.25, dy=7.9, margem_liq=5.5,
                    tendencia_grafica="ALTA", rsi_val=60.9)

    def test_sem_a_marcacao_o_motor_manda_vender(self):
        _, veredito, _, _ = equity.calcular_score_quantamental(**self.ENTRADAS)
        self.assertIn(veredito, ("VENDA", "NEUTRO"))

    def test_com_a_marcacao_vira_pedido_de_leitura_humana(self):
        _, veredito, _, alertas = equity.calcular_score_quantamental(
            **self.ENTRADAS, exercicio_atipico=self.ATIPICO)
        self.assertEqual(veredito, "REVISAR — EXERCÍCIO ATÍPICO")
        self.assertTrue(any("atípico" in a for a in alertas))
        self.assertTrue(any("79%" in a for a in alertas))

    def test_nao_vira_compra(self):
        """O alerta suspende a afirmação de venda; não inventa convicção de
        compra sobre um ano que ninguém leu."""
        score, veredito, _, _ = equity.calcular_score_quantamental(
            **self.ENTRADAS, exercicio_atipico=self.ATIPICO)
        self.assertNotIn("COMPRA", veredito)
        self.assertLess(score, equity.CORTE_COMPRA)

    def test_prejuizo_continua_mandando(self):
        """Prejuízo não é evento isolado: a trava de risco vence a marcação."""
        entradas = dict(self.ENTRADAS); entradas["roe"] = -8.0
        _, veredito, _, _ = equity.calcular_score_quantamental(
            **entradas, exercicio_atipico=self.ATIPICO)
        self.assertEqual(veredito, "VENDA / ALTO RISCO")

    def test_destruicao_de_capital_continua_mandando(self):
        _, veredito, _, _ = equity.calcular_score_quantamental(
            **self.ENTRADAS, destruicao_historica=True, exercicio_atipico=self.ATIPICO)
        self.assertEqual(veredito, "VENDA / ALTO RISCO")

    def test_exercicio_normal_nao_muda_nada(self):
        antes = equity.calcular_score_quantamental(**self.ENTRADAS)
        depois = equity.calcular_score_quantamental(
            **self.ENTRADAS, exercicio_atipico=self.NORMAL)
        self.assertEqual(antes[0], depois[0])
        self.assertEqual(antes[1], depois[1])

    def test_papel_bom_em_ano_atipico_nao_perde_a_compra(self):
        """A marcação só suspende VENDA e NEUTRO. Quem já pontuava alto segue
        com o veredito que o score sustenta."""
        bom = dict(pl=6.0, pvp=1.0, roe=22.0, dy=8.0, margem_liq=18.0,
                   tendencia_grafica="ALTA", rsi_val=52.0)
        _, veredito, _, alertas = equity.calcular_score_quantamental(
            **bom, exercicio_atipico=self.ATIPICO)
        self.assertIn("COMPRA", veredito)
        self.assertTrue(any("atípico" in a for a in alertas))
