"""Fumaça: o serviço importa inteiro?

Este arquivo existia com dois bytes de lixo ("ok") e derrubava a coleta do
pytest antes de qualquer teste rodar — `NameError: name 'ok' is not defined`.
Em vez de deixar um arquivo morto no repositório, virou o teste mais barato
que existe: se algum módulo do serviço não importa, nada mais importa.
"""
import unittest


class TestImportacaoDoServico(unittest.TestCase):
    def test_modulos_importam(self):
        import importlib

        for nome in ("modules.composicao_ibov", "modules.taxas",
                     "modules.fundamentos_cvm", "modules.fundamentos_fii",
                     "modules.cadastro_b3", "modules.credit_engine",
                     "modules.credito_cvm", "modules.credito_score", "modules.identidade", "modules.otimizador", "modules.opcoes",
                     "modules.estruturas", "routers.opcoes",
                     "modules.mercado", "modules.noticias",
                     "routers.mercado", "routers.quantitativo",
                     "modules.quant", "api",
                     "atualizar_fundos_cvm", "verificar_fundos"):
            with self.subTest(modulo=nome):
                self.assertIsNotNone(importlib.import_module(nome))
