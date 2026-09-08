"""Testes do caminho CVM: cadastro do B3, coletor da DFP e múltiplos.

O portal da CVM não é alcançável do ambiente onde estes testes rodam, então os
CSVs são sintéticos, montados no layout documentado da DFP (separador ';',
iso-8859-1, ORDEM_EXERC, ESCALA_MOEDA, CD_CONTA, VL_CONTA). O que eles provam é
o parsing e a aritmética — não que o arquivo real tenha esse formato.

    python -m unittest test_cvm -v
"""
import io
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atualizar_fundamentos_cvm as coletor  # noqa: E402
from modules import cadastro_b3, fundamentos_cvm  # noqa: E402

CABECALHO = ("CNPJ_CIA;DENOM_CIA;DT_FIM_EXERC;ORDEM_EXERC;ESCALA_MOEDA;"
             "CD_CONTA;VL_CONTA")


def _csv(linhas):
    return ("\n".join([CABECALHO] + linhas) + "\n").encode("iso-8859-1")


def _zip_dfp(ano, bpa, bpp, dre):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as arquivo:
        arquivo.writestr(f"dfp_cia_aberta_BPA_con_{ano}.csv", _csv(bpa))
        arquivo.writestr(f"dfp_cia_aberta_BPP_con_{ano}.csv", _csv(bpp))
        arquivo.writestr(f"dfp_cia_aberta_DRE_con_{ano}.csv", _csv(dre))
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


CNPJ_VALE = "33592510000154"


def _linhas_vale(ano=2025, escala="MIL"):
    """Números em MIL: patrimônio 200 bi, lucro 40 bi, receita 200 bi, LPA 9,3."""
    fim = f"{ano}-12-31"
    bpa = [
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};1;500000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};1.01;150000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};PENÚLTIMO;{escala};1;480000000",
    ]
    bpp = [
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};2.01;100000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};2.02;200000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};2.03;200000000",
    ]
    dre = [
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};3.01;200000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};3.05;60000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};3.06;-8000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};3.11;40000000",
        f"{CNPJ_VALE};VALE S.A.;{fim};ÚLTIMO;{escala};3.99.01.01;9.30",
    ]
    return bpa, bpp, dre


class TestCadastroB3(unittest.TestCase):
    def test_raiz_do_ticker(self):
        self.assertEqual(cadastro_b3.raiz_do_ticker("VALE3"), "VALE")
        self.assertEqual(cadastro_b3.raiz_do_ticker("bpac11"), "BPAC")
        self.assertEqual(cadastro_b3.raiz_do_ticker("KLBN11"), "KLBN")
        self.assertIsNone(cadastro_b3.raiz_do_ticker("^BVSP"))
        self.assertIsNone(cadastro_b3.raiz_do_ticker(""))

    def test_normalizar_cnpj(self):
        self.assertEqual(cadastro_b3.normalizar_cnpj("33.592.510/0001-54"), "33592510000154")
        self.assertEqual(cadastro_b3.normalizar_cnpj("39999619000197"), "39999619000197")
        self.assertIsNone(cadastro_b3.normalizar_cnpj("0"))
        self.assertIsNone(cadastro_b3.normalizar_cnpj(""))
        self.assertIsNone(cadastro_b3.normalizar_cnpj(None))

    def test_paginacao_monta_o_mapa(self):
        paginas = {
            1: {"page": {"totalPages": 2}, "results": [
                {"issuingCompany": "VALE", "cnpj": "33592510000154",
                 "companyName": "VALE S.A.", "tradingName": "VALE"},
                {"issuingCompany": "PETR", "cnpj": "33.000.167/0001-01",
                 "companyName": "PETROBRAS", "tradingName": "PETROBRAS"},
            ]},
            2: {"page": {"totalPages": 2}, "results": [
                {"issuingCompany": "ITUB", "cnpj": "60872504000123",
                 "companyName": "ITAU UNIBANCO", "tradingName": "ITAUUNIBANCO"},
                {"issuingCompany": "XXXX", "cnpj": "0",  # sem CNPJ: fica fora
                 "companyName": "BDR QUALQUER", "tradingName": "BDR"},
            ]},
        }
        original = coletor.requests.get if hasattr(coletor, "requests") else None
        chamadas = {"n": 0}

        def falso(numero):
            chamadas["n"] += 1
            return paginas.get(numero)

        real = cadastro_b3._pagina
        cadastro_b3._pagina = falso
        try:
            mapa = cadastro_b3.baixar_cadastro()
        finally:
            cadastro_b3._pagina = real
            if original:
                coletor.requests.get = original

        self.assertEqual(set(mapa), {"VALE", "PETR", "ITUB"})
        self.assertEqual(mapa["PETR"]["cnpj"], "33000167000101")
        self.assertNotIn("XXXX", mapa, "empresa sem CNPJ não entra no mapa")

    def test_cnpj_do_ticker_usa_o_arquivo(self):
        with tempfile.TemporaryDirectory() as pasta:
            caminho = os.path.join(pasta, "cadastro.json")
            cadastro_b3.gravar({"VALE": {"cnpj": CNPJ_VALE, "nome": "VALE S.A.",
                                         "nome_pregao": "VALE"}}, caminho)
            cadastro_b3.limpar_memoria()
            self.assertEqual(cadastro_b3.cnpj_do_ticker("VALE3", caminho), CNPJ_VALE)
            self.assertEqual(cadastro_b3.cnpj_do_ticker("VALE5", caminho), CNPJ_VALE)
            self.assertIsNone(cadastro_b3.cnpj_do_ticker("PETR4", caminho))
            cadastro_b3.limpar_memoria()


class TestColetorCvm(unittest.TestCase):
    def test_le_as_tres_demonstracoes(self):
        bpa, bpp, dre = _linhas_vale()
        arquivo = _zip_dfp(2025, bpa, bpp, dre)

        registros = {}
        for grupo, mapa in coletor.CONTAS.items():
            alternativas = coletor.CONTAS_ALTERNATIVAS if grupo == "DRE" else None
            parcial = coletor.ler_demonstrativo(
                arquivo, f"dfp_cia_aberta_{grupo}_con_2025.csv", mapa, alternativas)
            for chave, valores in parcial.items():
                registros.setdefault(chave, {}).update(valores)

        dados = registros[(CNPJ_VALE, 2025)]
        # ESCALA_MOEDA = MIL: os valores são multiplicados por mil
        self.assertEqual(dados["patrimonio_liquido"], 200_000_000 * 1000)
        self.assertEqual(dados["lucro_liquido"], 40_000_000 * 1000)
        self.assertEqual(dados["receita_liquida"], 200_000_000 * 1000)
        self.assertEqual(dados["ativo_total"], 500_000_000 * 1000)
        self.assertEqual(dados["passivo_circulante"], 100_000_000 * 1000)
        self.assertEqual(dados["passivo_nao_circulante"], 200_000_000 * 1000)
        self.assertEqual(dados["denom_cia"], "VALE S.A.")

    def test_lpa_nao_sofre_escala(self):
        """LPA é em reais por ação. Multiplicar por mil quebraria o P/L."""
        _, _, dre = _linhas_vale(escala="MIL")
        arquivo = _zip_dfp(2025, [], [], dre)
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_DRE_con_2025.csv",
            coletor.CONTAS["DRE"], coletor.CONTAS_ALTERNATIVAS)
        self.assertAlmostEqual(parcial[(CNPJ_VALE, 2025)]["lpa_on"], 9.30)

    def test_escala_unidade_nao_multiplica(self):
        bpa, bpp, dre = _linhas_vale(escala="UNIDADE")
        arquivo = _zip_dfp(2025, bpa, bpp, dre)
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_BPP_con_2025.csv", coletor.CONTAS["BPP"])
        self.assertEqual(parcial[(CNPJ_VALE, 2025)]["patrimonio_liquido"], 200_000_000)

    def test_penultimo_exercicio_e_ignorado(self):
        bpa, _, _ = _linhas_vale()
        arquivo = _zip_dfp(2025, bpa, [], [])
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_BPA_con_2025.csv", coletor.CONTAS["BPA"])
        # o PENÚLTIMO traz 480.000.000; só o ÚLTIMO pode entrar
        self.assertEqual(parcial[(CNPJ_VALE, 2025)]["ativo_total"], 500_000_000 * 1000)

    def test_conta_alternativa_nao_sobrescreve_a_principal(self):
        fim = "2025-12-31"
        dre = [
            f"{CNPJ_VALE};VALE;{fim};ÚLTIMO;MIL;3.11;40000000",
            f"{CNPJ_VALE};VALE;{fim};ÚLTIMO;MIL;3.13;37000000",
        ]
        arquivo = _zip_dfp(2025, [], [], dre)
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_DRE_con_2025.csv",
            coletor.CONTAS["DRE"], coletor.CONTAS_ALTERNATIVAS)
        self.assertEqual(parcial[(CNPJ_VALE, 2025)]["lucro_liquido"], 40_000_000 * 1000)

    def test_conta_alternativa_entra_quando_a_principal_falta(self):
        fim = "2025-12-31"
        dre = [f"{CNPJ_VALE};VALE;{fim};ÚLTIMO;MIL;3.13;37000000"]
        arquivo = _zip_dfp(2025, [], [], dre)
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_DRE_con_2025.csv",
            coletor.CONTAS["DRE"], coletor.CONTAS_ALTERNATIVAS)
        self.assertEqual(parcial[(CNPJ_VALE, 2025)]["lucro_liquido"], 37_000_000 * 1000)

    def test_csv_ausente_no_zip_nao_estoura(self):
        arquivo = _zip_dfp(2025, [], [], [])
        parcial = coletor.ler_demonstrativo(
            arquivo, "dfp_cia_aberta_BPA_con_2099.csv", coletor.CONTAS["BPA"])
        self.assertEqual(parcial, {})

    def test_gravacao_e_leitura_do_banco(self):
        with tempfile.TemporaryDirectory() as pasta:
            banco = os.path.join(pasta, "teste.db")
            coletor.gravar({(CNPJ_VALE, 2025): {"denom_cia": "VALE S.A.",
                                                "patrimonio_liquido": 2e11,
                                                "lucro_liquido": 4e10}}, banco)
            conexao = sqlite3.connect(banco)
            linha = conexao.execute(
                "SELECT patrimonio_liquido, lucro_liquido FROM fundamentos "
                "WHERE cnpj = ?", (CNPJ_VALE,)).fetchone()
            conexao.close()
            self.assertEqual(linha, (2e11, 4e10))


class TestMultiplosCvm(unittest.TestCase):
    PRECO = 79.02

    def setUp(self):
        self.pasta = tempfile.TemporaryDirectory()
        self.banco = os.path.join(self.pasta.name, "fundamentos.db")
        self.cadastro = os.path.join(self.pasta.name, "cadastro.json")
        cadastro_b3.gravar({"VALE": {"cnpj": CNPJ_VALE, "nome": "VALE S.A.",
                                     "nome_pregao": "VALE"}}, self.cadastro)
        cadastro_b3.limpar_memoria()
        fundamentos_cvm.limpar_cache()

    def tearDown(self):
        self.pasta.cleanup()
        cadastro_b3.limpar_memoria()
        fundamentos_cvm.limpar_cache()

    def _gravar(self, **campos):
        base = {"denom_cia": "VALE S.A.", "patrimonio_liquido": 2e11,
                "lucro_liquido": 4e10, "receita_liquida": 2e11, "lpa_on": 9.30}
        base.update(campos)
        coletor.gravar({(CNPJ_VALE, 2025): base}, self.banco)

    def test_calcula_os_quatro_multiplos(self):
        self._gravar()
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=self.PRECO, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertTrue(m["disponivel"])
        self.assertEqual(m["exercicio"], 2025)
        self.assertAlmostEqual(m["roe"], 20.0, places=6)        # 40 / 200
        self.assertAlmostEqual(m["margem_liq"], 20.0, places=6)  # 40 / 200
        self.assertAlmostEqual(m["pl"], 79.02 / 9.30, places=6)
        acoes = 4e10 / 9.30
        self.assertAlmostEqual(m["pvp"], 79.02 / (2e11 / acoes), places=6)

    def test_sem_preco_ainda_devolve_roe_e_margem(self):
        self._gravar()
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=None, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertAlmostEqual(m["roe"], 20.0, places=6)
        self.assertIsNone(m["pl"])
        self.assertIsNone(m["pvp"])

    def test_prejuizo_nao_gera_pl(self):
        """P/L com lucro negativo não tem leitura útil."""
        self._gravar(lucro_liquido=-8e9, lpa_on=-1.86)
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=self.PRECO, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertIsNone(m["pl"])
        self.assertAlmostEqual(m["roe"], -4.0, places=6)  # prejuízo aparece

    def test_patrimonio_negativo_nao_gera_roe(self):
        self._gravar(patrimonio_liquido=-5e9)
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=self.PRECO, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertIsNone(m["roe"])
        self.assertIsNone(m["pvp"])

    def test_ticker_fora_do_cadastro(self):
        self._gravar()
        m = fundamentos_cvm.multiplos_do_ticker(
            "PETR4", preco=self.PRECO, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertFalse(m["disponivel"])
        self.assertIsNone(m["roe"])

    def test_banco_ausente_nao_estoura(self):
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=self.PRECO,
            banco=os.path.join(self.pasta.name, "nao_existe.db"),
            caminho_cadastro=self.cadastro)
        self.assertFalse(m["disponivel"])
        self.assertFalse(fundamentos_cvm.base_disponivel(
            os.path.join(self.pasta.name, "nao_existe.db")))

    def test_exercicio_mais_recente_vence(self):
        coletor.gravar({
            (CNPJ_VALE, 2024): {"denom_cia": "VALE", "patrimonio_liquido": 1e11,
                                "lucro_liquido": 1e10, "receita_liquida": 1e11, "lpa_on": 2.0},
            (CNPJ_VALE, 2025): {"denom_cia": "VALE", "patrimonio_liquido": 2e11,
                                "lucro_liquido": 4e10, "receita_liquida": 2e11, "lpa_on": 9.30},
        }, self.banco)
        m = fundamentos_cvm.multiplos_do_ticker(
            "VALE3", preco=self.PRECO, banco=self.banco, caminho_cadastro=self.cadastro)
        self.assertEqual(m["exercicio"], 2025)
        self.assertAlmostEqual(m["roe"], 20.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
