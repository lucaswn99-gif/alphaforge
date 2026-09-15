"""Cota diária dos fundos (Etapa B): o módulo de leitura (`modules/cota_cvm`)
e as peças puras do coletor (`atualizar_cota_fundos_cvm`) que não dependem
de rede — parsing, mapeamento de coluna, aritmética de mês.

    python -m unittest test_cota_cvm -v
"""
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timezone

import atualizar_cota_fundos_cvm as coletor
from modules import cota_cvm


def _banco_com_cotas(linhas):
    """`linhas`: [(cnpj, data_iso, valor_cota), ...]. Devolve o caminho de um
    banco temporário no mesmo esquema que o coletor grava."""
    caminho = os.path.join(tempfile.mkdtemp(), "cota_teste.db")
    conexao = sqlite3.connect(caminho)
    conexao.execute("""
        CREATE TABLE cotas (
            cnpj TEXT NOT NULL, data TEXT NOT NULL, valor_cota REAL NOT NULL,
            patrimonio_liquido REAL, numero_cotistas INTEGER,
            atualizado_em TEXT NOT NULL, PRIMARY KEY (cnpj, data)
        )
    """)
    agora = datetime.now(timezone.utc).isoformat()
    conexao.executemany(
        "INSERT INTO cotas (cnpj, data, valor_cota, atualizado_em) VALUES (?, ?, ?, ?)",
        [(cnpj, data_iso, valor, agora) for cnpj, data_iso, valor in linhas])
    conexao.commit()
    conexao.close()
    return caminho


class TestLeituraCotaCvm(unittest.TestCase):
    def test_banco_ausente_devolve_none_sem_quebrar(self):
        ausente = "/tmp/nao-existe-de-verdade-cota.db"
        self.assertIsNone(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 2), banco=ausente))
        self.assertEqual(cota_cvm.cota_mais_recente("12345678000199", banco=ausente), (None, None))
        self.assertFalse(cota_cvm.base_disponivel(banco=ausente))

    def test_cnpj_nao_coletado_devolve_none(self):
        banco = _banco_com_cotas([("12345678000199", "2026-01-02", 100.0)])
        self.assertIsNone(cota_cvm.cota_na_data("00000000000000", date(2026, 1, 2), banco=banco))

    def test_cota_na_data_exata(self):
        banco = _banco_com_cotas([("12345678000199", "2026-01-02", 100.0)])
        self.assertEqual(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 2), banco=banco), 100.0)

    def test_cota_recua_ate_o_pregao_mais_proximo_antes_da_data(self):
        # 2026-01-03 é sábado (sem pregão) — a cota tem que vir de 01/02.
        banco = _banco_com_cotas([("12345678000199", "2026-01-02", 100.0)])
        self.assertEqual(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 3), banco=banco), 100.0)

    def test_nunca_olha_para_a_frente(self):
        """Só há cota DEPOIS da data pedida — não pode vir dali (seria usar
        dado do futuro para marcar um ponto do passado)."""
        banco = _banco_com_cotas([("12345678000199", "2026-01-10", 100.0)])
        self.assertIsNone(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 5), banco=banco))

    def test_fora_da_tolerancia_devolve_none(self):
        banco = _banco_com_cotas([("12345678000199", "2025-01-02", 100.0)])
        self.assertIsNone(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 2), banco=banco))

    def test_cota_mais_recente_pega_a_ultima_data(self):
        banco = _banco_com_cotas([
            ("12345678000199", "2026-01-02", 100.0),
            ("12345678000199", "2026-02-02", 110.0),
        ])
        self.assertEqual(cota_cvm.cota_mais_recente("12345678000199", banco=banco), (110.0, "2026-02-02"))

    def test_base_disponivel_com_tabela_existente(self):
        banco = _banco_com_cotas([("12345678000199", "2026-01-02", 100.0)])
        self.assertTrue(cota_cvm.base_disponivel(banco=banco))


class TestPecasPurasDoColetorSemRede(unittest.TestCase):
    def test_mapeia_colunas_pelo_layout_oficial(self):
        mapa = coletor._mapear_colunas(
            ["CNPJ_FUNDO_CLASSE", "DT_COMPTC", "VL_QUOTA", "VL_PATRIM_LIQ", "NR_COTST"])
        self.assertEqual(mapa["cnpj"], "CNPJ_FUNDO_CLASSE")
        self.assertEqual(mapa["data"], "DT_COMPTC")
        self.assertEqual(mapa["cota"], "VL_QUOTA")
        self.assertEqual(mapa["patrimonio_liquido"], "VL_PATRIM_LIQ")
        self.assertEqual(mapa["numero_cotistas"], "NR_COTST")

    def test_mapeia_nome_antigo_cnpj_fundo_sem_classe(self):
        # Antes da Resolução 175 a coluna se chamava só CNPJ_FUNDO.
        mapa = coletor._mapear_colunas(["CNPJ_FUNDO", "DT_COMPTC", "VL_QUOTA"])
        self.assertEqual(mapa["cnpj"], "CNPJ_FUNDO")

    def test_para_numero_aceita_virgula_decimal(self):
        self.assertAlmostEqual(coletor._para_numero("1234,567890123456"), 1234.567890123456, places=6)

    def test_para_numero_aceita_milhar_com_ponto_e_decimal_com_virgula(self):
        self.assertAlmostEqual(coletor._para_numero("1.234.567,89"), 1234567.89, places=2)

    def test_para_numero_vazio_devolve_none(self):
        self.assertIsNone(coletor._para_numero(""))
        self.assertIsNone(coletor._para_numero(None))

    def test_so_digitos_remove_pontuacao_do_cnpj(self):
        self.assertEqual(coletor._so_digitos("12.345.678/0001-99"), "12345678000199")

    def test_mes_anterior_vira_dezembro_do_ano_anterior(self):
        self.assertEqual(coletor._mes_anterior(202601), 202512)

    def test_mes_anterior_dentro_do_mesmo_ano(self):
        self.assertEqual(coletor._mes_anterior(202603), 202602)

    def test_meses_no_intervalo_atravessa_virada_de_ano(self):
        self.assertEqual(coletor._meses_no_intervalo(202511, 202602),
                         [202511, 202512, 202601, 202602])

    def test_meses_no_intervalo_um_so_mes(self):
        self.assertEqual(coletor._meses_no_intervalo(202601, 202601), [202601])


class TestProcessarMesSemRede(unittest.TestCase):
    """`processar_mes` chama `_csvs_do_mes`, que baixa da CVM — aqui isso é
    trocado por um CSV sintético, no layout oficial, para testar o
    parsing/filtro sem rede."""

    def _instalar_csv_falso(self, texto_csv, nome="inf_diario_fi_202601.csv"):
        original = coletor._csvs_do_mes
        coletor._csvs_do_mes = lambda aaaamm: [(nome, texto_csv.encode("iso-8859-1"))]
        self.addCleanup(lambda: setattr(coletor, "_csvs_do_mes", original))

    def test_filtra_so_os_cnpjs_alvo(self):
        csv = (
            "CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA;VL_PATRIM_LIQ;NR_COTST\n"
            "12345678000199;2026-01-02;1234,567890123456;500000,00;10\n"
            "00000000000000;2026-01-02;99,00;100000,00;5\n"
        )
        self._instalar_csv_falso(csv)
        linhas = coletor.processar_mes(202601, {"12345678000199"})
        self.assertEqual(len(linhas), 1)
        cnpj, data_iso, cota, pl, cotistas = linhas[0]
        self.assertEqual(cnpj, "12345678000199")
        self.assertEqual(data_iso, "2026-01-02")
        self.assertAlmostEqual(cota, 1234.567890123456, places=6)
        self.assertEqual(pl, 500000.0)
        self.assertEqual(cotistas, 10)

    def test_cota_zero_ou_negativa_e_descartada(self):
        csv = ("CNPJ_FUNDO_CLASSE;DT_COMPTC;VL_QUOTA\n"
              "12345678000199;2026-01-02;0,00\n")
        self._instalar_csv_falso(csv)
        self.assertEqual(coletor.processar_mes(202601, {"12345678000199"}), [])

    def test_sem_coluna_reconhecida_e_ignorado_sem_quebrar(self):
        csv = "COLUNA_ESTRANHA;OUTRA\nabc;def\n"
        self._instalar_csv_falso(csv)
        self.assertEqual(coletor.processar_mes(202601, {"12345678000199"}), [])

    def test_gravar_e_reler(self):
        banco = os.path.join(tempfile.mkdtemp(), "grava_teste.db")
        coletor.gravar([("12345678000199", "2026-01-02", 1234.567890123456, 500000.0, 10)],
                       banco=banco)
        self.assertEqual(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 2), banco=banco),
                         1234.567890123456)

    def test_gravar_duas_vezes_acrescenta_em_vez_de_apagar(self):
        """Ao contrário do coletor de FII (que reconstrói a base inteira), o
        de cota diária é uma série no tempo — uma execução não pode apagar o
        que uma execução anterior já gravou."""
        banco = os.path.join(tempfile.mkdtemp(), "acrescenta_teste.db")
        coletor.gravar([("12345678000199", "2026-01-02", 100.0, None, None)], banco=banco)
        coletor.gravar([("12345678000199", "2026-02-02", 110.0, None, None)], banco=banco)
        self.assertEqual(cota_cvm.cota_na_data("12345678000199", date(2026, 1, 2), banco=banco), 100.0)
        self.assertEqual(cota_cvm.cota_mais_recente("12345678000199", banco=banco), (110.0, "2026-02-02"))


if __name__ == "__main__":
    unittest.main()
