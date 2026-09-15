"""Relatório em PDF: o módulo puro (modules/relatorio.py, testado com
dicionários sintéticos — sem banco, sem rede) e a rota que monta esses
dicionários a partir da carteira real e devolve o PDF pronto.

    python -m unittest test_relatorio -v
"""
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from modules import contas, relatorio


def _banco_limpo():
    contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_teste.db")
    contas._iniciado = False
    contas.iniciar()
    from routers import conta as rota_conta
    rota_conta._tentativas.clear()


def _dados_minimos():
    """O menor `dados` válido — carteira vazia, tudo mais ausente. Prova
    que `montar` não quebra na ausência total de conteúdo, só descreve."""
    return {
        "cliente_email": "cliente@teste.com",
        "gerado_em": "15/09/2026 12:00 UTC",
        "filosofia": None,
        "composicao": {
            "acoes": [], "renda_fixa": [], "fundos": [],
            "custo_total_acoes": 0.0, "valor_atual_renda_fixa": 0.0,
            "valor_atual_fundos": 0.0, "patrimonio_total": 0.0,
        },
        "diagnostico": None,
        "backtest": None,
        "estresse": None,
        "projecao": None,
    }


class TestMontarPdfPuro(unittest.TestCase):
    """`montar` é função pura: dicionário sintético entra, bytes de PDF
    saem. Não abre banco, não faz rede — testável isolado do resto."""

    def test_dados_minimos_gera_pdf_valido(self):
        pdf = relatorio.montar(_dados_minimos())
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 500)

    def test_composicao_vazia_diz_que_nao_ha_posicao(self):
        # Não testa o conteúdo do PDF byte a byte (frágil); testa que a
        # função de seção, chamada isolada, devolve o texto esperado.
        estilos = relatorio._estilos()
        historia = relatorio._secao_composicao(_dados_minimos()["composicao"], estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        self.assertIn("Nenhuma posição cadastrada", textos)

    def test_diagnostico_ausente_explica_por_que(self):
        estilos = relatorio._estilos()
        historia = relatorio._secao_diagnostico(None, None, estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        self.assertIn("nenhuma posição dessa classe", textos.lower())

    def test_backtest_ausente_explica_por_que(self):
        estilos = relatorio._estilos()
        historia = relatorio._secao_backtest(None, estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        self.assertIn("histórico suficiente", textos.lower())

    def test_estresse_sem_eventos_explica_por_que(self):
        estilos = relatorio._estilos()
        historia = relatorio._secao_estresse({"eventos": [], "motivo": "Motivo de teste."},
                                             estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        self.assertIn("Motivo de teste.", textos)

    def test_projecao_ausente_nunca_inventa_premissa(self):
        estilos = relatorio._estilos()
        historia = relatorio._secao_projecao(None, estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        self.assertIn("nenhuma premissa", textos.lower())

    def test_projecao_presente_mostra_premissas_e_gap(self):
        estilos = relatorio._estilos()
        projecao_dados = {
            "taxa_anual_pct": 10.0, "aporte_mensal": 500.0, "horizonte_anos": 10,
            "valor_final": 150000.0, "total_aportado": 60000.0,
            "rendimento_total": 84000.0, "renda_mensal_sustentavel_final": 1200.0,
            "serie": [{"ano": 0, "valor": 6050.0}, {"ano": 10, "valor": 150000.0}],
            "gaps": [{"tipo": "renda_mensal", "rotulo": "Renda passiva",
                      "meta": 3000.0, "projetado": 1200.0, "gap": 1800.0}],
        }
        historia = relatorio._secao_projecao(projecao_dados, estilos)
        textos = " ".join(getattr(p, "text", "") for p in historia)
        # Percentual segue a convenção já usada no projeto inteiro (ponto
        # decimal, ex. "10.00%" — só a moeda usa vírgula, via _brl).
        self.assertIn("10.00", textos)  # taxa
        self.assertIn("faltam", textos.lower())

    def test_pdf_completo_com_todas_as_secoes_preenchidas(self):
        dados = _dados_minimos()
        dados["filosofia"] = {"chave": "barsi", "rotulo": "Barsi"}
        dados["composicao"]["acoes"] = [
            {"ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0,
             "custo_total": 3000.0, "peso_pct": 100.0}]
        dados["composicao"]["custo_total_acoes"] = 3000.0
        dados["composicao"]["patrimonio_total"] = 3000.0
        dados["diagnostico"] = {
            "posicoes": [{"ticker": "PETR4", "custo_total": 3000.0,
                          "diagnostico": {"estado": "conforme", "rotulo": "Conforme",
                                         "resumo": "OK."}}],
            "resumo": {"conforme": 1}, "rotulos": {"conforme": "Conforme"}}
        dados["backtest"] = {
            "serie": [{"data": "2025-09-15", "indice": 100.0},
                     {"data": "2026-09-15", "indice": 110.0}],
            "retorno_periodo_pct": 10.0, "cobertura_pct": 100.0, "meses": 12,
            "sem_historico": []}
        dados["estresse"] = {"eventos": [
            {"nome": "Crise financeira de 2008", "drawdown_pct": -30.0,
             "cobertura_pct": 100.0, "pior_papel": {"ticker": "PETR4", "drawdown_pct": -30.0}}]}
        dados["projecao"] = {
            "taxa_anual_pct": 10.0, "aporte_mensal": 0.0, "horizonte_anos": 1,
            "valor_final": 3300.0, "total_aportado": 0.0, "rendimento_total": 300.0,
            "renda_mensal_sustentavel_final": 26.25,
            "serie": [{"ano": 0, "valor": 3000.0}, {"ano": 1, "valor": 3300.0}],
            "gaps": []}
        pdf = relatorio.montar(dados)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 1000)


class TestRotaRelatorio(unittest.TestCase):
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

    def test_exige_sessao(self):
        self.cliente.cookies.clear()
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={})
        self.assertEqual(resposta.status_code, 401)

    def test_exige_assinatura(self):
        self._entrar("rel-free@teste.com", premium=False)
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={})
        self.assertEqual(resposta.status_code, 402)

    def test_parametros_lista_os_cinco_eventos(self):
        self._entrar("rel-vip1@teste.com")
        corpo = self.cliente.get("/api/v1/carteira/relatorio/parametros").json()
        self.assertEqual(len(corpo["eventos_estresse"]), 5)

    def test_carteira_vazia_sem_aporte_devolve_422(self):
        self._entrar("rel-vazio@teste.com")
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={})
        self.assertEqual(resposta.status_code, 422)

    def test_so_com_aporte_planejado_ja_gera_relatorio(self):
        """Sem nenhuma posição, mas com aporte mensal na projeção: ainda há
        o que relatar (a projeção pura), então a rota não recusa."""
        self._entrar("rel-aporte@teste.com")
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={
            "projecao": {"taxa_anual_pct": 10.0, "aporte_mensal": 500.0,
                        "horizonte_anos": 5}})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.headers["content-type"], "application/pdf")
        self.assertTrue(resposta.content.startswith(b"%PDF"))

    def test_com_posicao_gera_pdf_com_composicao(self):
        self._entrar("rel-posicao@teste.com")
        self.cliente.post("/api/v1/carteira/item", json={
            "ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0})
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={})
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.content.startswith(b"%PDF"))

    def test_projecao_com_taxa_invalida_devolve_422(self):
        self._entrar("rel-taxa@teste.com")
        self.cliente.post("/api/v1/carteira/item", json={
            "ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0})
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={
            "projecao": {"taxa_anual_pct": 999.0, "aporte_mensal": 0.0,
                        "horizonte_anos": 5}})
        self.assertEqual(resposta.status_code, 422)

    def test_eventos_estresse_invalidos_sao_ignorados_sem_quebrar(self):
        self._entrar("rel-evento@teste.com")
        self.cliente.post("/api/v1/carteira/item", json={
            "ticker": "PETR4", "quantidade": 100, "preco_medio": 30.0})
        resposta = self.cliente.post("/api/v1/carteira/relatorio", json={
            "eventos_estresse": ["chave_que_nao_existe"]})
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(resposta.content.startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
