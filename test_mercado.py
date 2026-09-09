"""Testes dos painéis de contexto: identidade, commodities, notícias e placar.

Rodam sem rede: requests e yfinance são substituídos por stubs.
"""
import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

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
from modules import identidade, mercado, noticias  # noqa: E402


class TestIdentidade(unittest.TestCase):
    def test_papel_conhecido_tem_logo_e_setor(self):
        d = identidade.identidade("PETR4")
        self.assertEqual(d["raiz"], "PETR")
        self.assertEqual(d["setor"], "Petróleo")
        self.assertIn("petrobras.com.br", d["logo"])
        self.assertEqual(d["monograma"], "PE")

    def test_papel_desconhecido_cai_no_monograma(self):
        """Domínio inventado mostraria a marca da empresa errada ao lado do
        papel. Preferimos monograma, que é sempre correto."""
        d = identidade.identidade("XPTO3")
        self.assertIsNone(d["logo"])
        self.assertEqual(d["monograma"], "XP")
        self.assertEqual(d["setor"], "Outros")
        self.assertTrue(d["cor"].startswith("#"))

    def test_classes_de_um_mesmo_papel_compartilham_identidade(self):
        for par in (("PETR3", "PETR4"), ("ITUB3", "ITUB4"), ("GGBR3", "GGBR4")):
            a, b = (identidade.identidade(t) for t in par)
            self.assertEqual(a["logo"], b["logo"])
            self.assertEqual(a["setor"], b["setor"])

    def test_alias_do_yahoo_aponta_para_a_mesma_empresa(self):
        """AXIA3 (código corrente do B3) e ELET3 (símbolo legado) são a mesma
        companhia; o tile não pode mudar conforme o código usado."""
        self.assertEqual(identidade.identidade("AXIA3")["logo"],
                         identidade.identidade("ELET3")["logo"])
        self.assertEqual(identidade.identidade("EMBJ3")["logo"],
                         identidade.identidade("EMBR3")["logo"])

    def test_entrada_invalida_nao_levanta(self):
        for entrada in (None, "", "  ", "12", "PETROBRAS"):
            d = identidade.identidade(entrada)
            self.assertIn("cor", d)
            self.assertIsNone(d["logo"])

    def test_todo_setor_tem_cor(self):
        setores = {setor for _, setor in identidade.EMPRESAS.values()}
        for setor in setores:
            self.assertIn(setor, identidade.CORES_SETOR, setor)

    def test_cobertura_do_universo(self):
        cob = identidade.cobertura(["PETR4", "VALE3", "XPTO3"])
        self.assertEqual(cob["total"], 3)
        self.assertEqual(cob["com_logo"], 2)
        self.assertEqual(cob["sem_logo"], ["XPTO3"])


def _df_close(simbolos, fechamentos):
    """DataFrame no formato do yf.download com vários símbolos."""
    idx = pd.date_range("2026-09-01", periods=len(next(iter(fechamentos.values()))), freq="D")
    colunas = pd.MultiIndex.from_product([["Close"], simbolos])
    dados = pd.DataFrame({("Close", s): fechamentos[s] for s in simbolos}, index=idx)
    return dados.reindex(columns=colunas)


class TestMercado(unittest.TestCase):
    def setUp(self):
        mercado.limpar_cache()
        self.original = mercado.yf.download

    def tearDown(self):
        mercado.yf.download = self.original
        mercado.limpar_cache()

    def test_variacao_do_dia(self):
        simbolos = ["BZ=F", "^BVSP"]
        mercado.yf.download = lambda *a, **k: _df_close(
            simbolos, {"BZ=F": [80.0, 82.0], "^BVSP": [130000.0, 128700.0]})
        itens = {i["simbolo"]: i for i in mercado.coletar(forcar=True)}
        self.assertAlmostEqual(itens["BZ=F"]["variacao"], 2.5)
        self.assertAlmostEqual(itens["^BVSP"]["variacao"], -1.0)
        self.assertEqual(itens["BZ=F"]["grupo"], "Energia")

    def test_um_fechamento_so_nao_vira_variacao_zero(self):
        """Zero por cento é uma afirmação. Sem dois fechamentos, não afirmamos."""
        simbolos = ["BZ=F"]
        mercado.yf.download = lambda *a, **k: _df_close(simbolos, {"BZ=F": [80.0, float("nan")]})
        self.assertEqual(mercado.coletar(forcar=True), [])

    def test_falha_de_rede_devolve_o_cache(self):
        simbolos = ["BZ=F"]
        mercado.yf.download = lambda *a, **k: _df_close(simbolos, {"BZ=F": [80.0, 82.0]})
        primeiro = mercado.coletar(forcar=True)
        self.assertTrue(primeiro)

        def explode(*a, **k):
            raise RuntimeError("sem rede")

        mercado.yf.download = explode
        self.assertEqual(mercado.coletar(forcar=True), primeiro)

    def test_agrupamento_respeita_a_ordem_da_tela(self):
        itens = [{"simbolo": "GC=F", "nome": "Ouro", "grupo": "Metais", "unidade": "", "preco": 1, "variacao": 0.1},
                 {"simbolo": "BZ=F", "nome": "Brent", "grupo": "Energia", "unidade": "", "preco": 1, "variacao": 0.1}]
        grupos = mercado.por_grupo(itens)
        self.assertEqual(list(grupos), ["Energia", "Metais"])

    def test_todo_instrumento_tem_grupo_conhecido(self):
        for _, _, grupo, _ in mercado.INSTRUMENTOS:
            self.assertIn(grupo, mercado.GRUPOS)


class TestNoticias(unittest.TestCase):
    RSS = """<?xml version="1.0"?><rss><channel>
      <item><title>Copom mantém a Selic em 14%</title>
            <link>https://exemplo.com/a</link>
            <pubDate>Mon, 08 Sep 2026 18:30:00 -0300</pubDate></item>
      <item><title>Petr&#243;leo sobe com corte da OPEP</title>
            <link>https://exemplo.com/b</link>
            <pubDate>Mon, 08 Sep 2026 12:00:00 -0300</pubDate></item>
    </channel></rss>"""

    class _Resposta:
        def __init__(self, corpo, status=200):
            self.content = corpo.encode("utf-8")
            self.status_code = status

    def setUp(self):
        noticias.limpar_cache()
        self.original = noticias.requests.get

    def tearDown(self):
        noticias.requests.get = self.original
        noticias.limpar_cache()

    def test_le_manchete_link_e_horario(self):
        noticias.requests.get = lambda *a, **k: self._Resposta(self.RSS)
        itens, diag = noticias.ler_fonte("x", "Veículo", ("http://x",), "Macro")
        self.assertTrue(diag["ok"])
        self.assertEqual(len(itens), 2)
        self.assertEqual(itens[0]["titulo"], "Copom mantém a Selic em 14%")
        self.assertEqual(itens[0]["link"], "https://exemplo.com/a")
        self.assertTrue(itens[0]["quando"].startswith("2026-09-08"))

    def test_nao_guarda_o_texto_da_materia(self):
        """O painel é índice, não republicação: só manchete, veículo e link."""
        noticias.requests.get = lambda *a, **k: self._Resposta(self.RSS)
        itens, _ = noticias.ler_fonte("x", "Veículo", ("http://x",), "Macro")
        for item in itens:
            self.assertEqual(set(item), {"titulo", "link", "veiculo", "categoria", "quando"})

    def test_fonte_fora_do_ar_vira_diagnostico_e_nao_excecao(self):
        def explode(*a, **k):
            raise RuntimeError("timeout")

        noticias.requests.get = explode
        itens, diag = noticias.ler_fonte("x", "Veículo", ("http://x",), "Macro")
        self.assertEqual(itens, [])
        self.assertFalse(diag["ok"])
        self.assertIn("RuntimeError", diag["motivo"])

    def test_http_de_erro_e_reportado_com_o_endereco(self):
        """O motivo carrega QUAL endereço falhou: com candidatos múltiplos,
        'HTTP 404' sozinho não diz qual deles morreu."""
        noticias.requests.get = lambda *a, **k: self._Resposta("", status=404)
        _, diag = noticias.ler_fonte("x", "Veículo", ("http://x",), "Macro")
        self.assertEqual(diag["motivo"], "http://x: HTTP 404")

    def test_cai_para_o_endereco_seguinte(self):
        """O defeito real: BCB devolvendo HTML, IBGE com 403 e Notícias
        Agrícolas com 404 derrubaram três painéis porque cada fonte tinha um
        endereço só."""
        chamadas = []

        def responder(url, *a, **k):
            chamadas.append(url)
            if url.endswith("/velho"):
                return self._Resposta("<html>não é feed</html>", status=200)
            if url.endswith("/bloqueado"):
                return self._Resposta("", status=403)
            return self._Resposta(self.RSS)

        noticias.requests.get = responder
        itens, diag = noticias.ler_fonte(
            "x", "Veículo", ("http://a/velho", "http://b/bloqueado", "http://c/bom"), "Macro")
        self.assertTrue(diag["ok"])
        self.assertEqual(diag["url"], "http://c/bom")
        self.assertEqual(len(itens), 2)
        self.assertEqual(len(chamadas), 3)

    def test_para_no_primeiro_que_funciona(self):
        """Endereço bom no começo não pode disparar chamada aos demais."""
        chamadas = []

        def responder(url, *a, **k):
            chamadas.append(url)
            return self._Resposta(self.RSS)

        noticias.requests.get = responder
        _, diag = noticias.ler_fonte("x", "V", ("http://a", "http://b"), "Macro")
        self.assertEqual(chamadas, ["http://a"])
        self.assertEqual(diag["url"], "http://a")

    def test_enderecos_verificados_continuam_no_lugar(self):
        """Trava os endereços que eu conferi ao vivo em 09/09/2026.

        Os do BCB e do IBGE eu tinha INFERIDO antes, e os dois devolveram erro
        (400 e 403). Estes vieram de abrir a página de feeds no navegador e ler
        o href. Se alguém trocar por um palpite, este teste avisa.
        """
        por_chave = {c: u for c, _, u, _ in noticias.FONTES}
        self.assertIn("https://www.bcb.gov.br/api/feed/sitebcb/sitefeeds/comunicadoscopom",
                      por_chave["copom_com"])
        self.assertIn("https://www.bcb.gov.br/api/feed/sitebcb/sitefeeds/atascopom",
                      por_chave["copom_ata"])
        self.assertIn("https://agenciadenoticias.ibge.gov.br/agencia-rss",
                      por_chave["ibge"])

    def test_copom_comunicado_e_ata_sao_fontes_separadas(self):
        """Juntas, o teto de itens por fonte faria a ata sumir justamente na
        semana da decisão."""
        chaves = {c for c, _, _, _ in noticias.FONTES}
        self.assertIn("copom_com", chaves)
        self.assertIn("copom_ata", chaves)

    def test_toda_categoria_tem_mais_de_uma_fonte(self):
        """Uma fonte por categoria significa painel vazio quando ela cai."""
        contagem = {}
        for _, _, _, categoria in noticias.FONTES:
            contagem[categoria] = contagem.get(categoria, 0) + 1
        for categoria in noticias.CATEGORIAS:
            self.assertGreaterEqual(contagem.get(categoria, 0), 2, categoria)

    def test_xml_quebrado_nao_derruba(self):
        noticias.requests.get = lambda *a, **k: self._Resposta("<rss><item>")
        itens, diag = noticias.ler_fonte("x", "Veículo", ("http://x",), "Macro")
        self.assertEqual(itens, [])
        self.assertFalse(diag["ok"])

    def test_ordena_por_horario_e_conta_as_fontes(self):
        noticias.requests.get = lambda *a, **k: self._Resposta(self.RSS)
        payload = noticias.coletar(forcar=True)
        horarios = [m["quando"] for m in payload["manchetes"]]
        self.assertEqual(horarios, sorted(horarios, reverse=True))
        self.assertEqual(payload["fontes_ok"], payload["fontes_total"])

    def test_coleta_vazia_nao_apaga_a_anterior(self):
        """Uma queda geral de rede não pode limpar a tela do que já foi lido."""
        noticias.requests.get = lambda *a, **k: self._Resposta(self.RSS)
        primeiro = noticias.coletar(forcar=True)
        self.assertTrue(primeiro["manchetes"])

        noticias.requests.get = lambda *a, **k: self._Resposta("", status=500)
        segundo = noticias.coletar(forcar=True)
        self.assertEqual(len(segundo["manchetes"]), len(primeiro["manchetes"]))
        self.assertIn("aviso", segundo)

    def test_toda_fonte_tem_categoria_conhecida(self):
        for _, _, _, categoria in noticias.FONTES:
            self.assertIn(categoria, noticias.CATEGORIAS)

    def test_manchete_com_html_e_limpa(self):
        rss = ('<?xml version="1.0"?><rss><channel><item>'
               '<title>Bolsa &amp; d&#243;lar &lt;b&gt;hoje&lt;/b&gt;</title>'
               '<link>http://x</link></item></channel></rss>')
        noticias.requests.get = lambda *a, **k: self._Resposta(rss)
        itens, _ = noticias.ler_fonte("x", "V", ("http://x",), "Macro")
        self.assertEqual(itens[0]["titulo"], "Bolsa & dólar hoje")


if __name__ == "__main__":
    unittest.main()


class TestPlacarDoPregao(unittest.TestCase):
    """As maiores altas e baixas saem da mesma carga de preços do scanner —
    nenhuma requisição nova. O que se testa aqui é o recorte e a honestidade
    do rótulo."""

    def setUp(self):
        from routers import mercado as router_mercado
        self.router = router_mercado
        self.original = router_mercado.executar_scanner

    def tearDown(self):
        self.router.executar_scanner = self.original

    def _scanner(self, variacoes):
        return lambda forcar=False: {
            "oportunidades": [
                {"ticker": t, "nome": t, "preco": 10.0, "variacao_dia": v,
                 "identidade": identidade.identidade(t)}
                for t, v in variacoes.items()],
            "origem_composicao": "b3",
        }

    def test_ordena_altas_e_baixas(self):
        self.router.executar_scanner = self._scanner(
            {"PETR4": 3.1, "VALE3": -2.2, "ITUB4": 1.4, "WEGE3": -5.0})
        d = self.router.get_placar()
        self.assertEqual([i["ticker"] for i in d["altas"]], ["PETR4", "ITUB4"])
        self.assertEqual([i["ticker"] for i in d["baixas"]], ["WEGE3", "VALE3"])

    def test_papel_sem_variacao_fica_de_fora_dos_dois_lados(self):
        self.router.executar_scanner = self._scanner({"PETR4": 0.0, "VALE3": 2.0})
        d = self.router.get_placar()
        self.assertEqual([i["ticker"] for i in d["altas"]], ["VALE3"])
        self.assertEqual(d["baixas"], [])

    def test_papel_sem_dado_de_variacao_e_ignorado(self):
        self.router.executar_scanner = lambda forcar=False: {
            "oportunidades": [{"ticker": "PETR4", "preco": 10.0, "variacao_dia": None},
                              {"ticker": "VALE3", "preco": 10.0, "variacao_dia": 1.0}],
            "origem_composicao": "b3"}
        d = self.router.get_placar()
        self.assertEqual(d["universo"], 1)

    def test_cada_lado_e_limitado(self):
        self.router.executar_scanner = self._scanner(
            {f"AAA{i}3": float(i) for i in range(1, 30)})
        d = self.router.get_placar()
        self.assertEqual(len(d["altas"]), self.router.TAMANHO_PLACAR)

    def test_o_aviso_nao_chama_o_recorte_de_b3_inteira(self):
        """Chamar 100 papéis de 'maiores altas da B3' seria mentira pequena e
        desnecessária — e num terminal de assessoria mentira pequena é a que
        passa despercebida."""
        self.router.executar_scanner = self._scanner({"PETR4": 1.0})
        aviso = self.router.get_placar()["aviso"]
        self.assertIn("radar", aviso.lower())
        self.assertIn("não a B3 inteira", aviso)

    def test_a_linha_carrega_a_identidade_do_papel(self):
        self.router.executar_scanner = self._scanner({"PETR4": 1.0})
        alta = self.router.get_placar()["altas"][0]
        self.assertEqual(alta["identidade"]["raiz"], "PETR")
