"""Confere os fatores de paridade dos BDRs contra o preço de tela.

    python conferir_bdr.py

O fator de cada BDR muda em desdobramento e não tem API gratuita. Uma
constante velha aqui não dá erro: ela fabrica spread. Este script pergunta ao
mercado qual é a razão praticada e imprime a tabela corrigida, pronta para
colar em `modules/bdr.py`.

Rode depois de qualquer desdobramento e, por garantia, a cada poucos meses.
"""

import logging
import sys

logging.basicConfig(level=logging.ERROR)

from modules import bdr  # noqa: E402


def main():
    painel = bdr.GlobalEquitiesPanel()
    relatorio = painel.sugerir_tabela()
    if "erro" in relatorio:
        print(f"\n  {relatorio['erro']}\n")
        return 1

    print(f"\n  Dólar: {relatorio['dolar']:.4f}")
    print(f"  Tabela verificada em: {bdr.VERIFICADO_EM}\n")
    print(f"  {'ATIVO':<8} {'BDR':<9} {'TABELA':>8} {'MERCADO':>9} "
          f"{'SUGERIDO':>9}  SITUAÇÃO")
    print("  " + "-" * 64)

    rotulos = {"confere": "confere", "corrigir": "CORRIGIR",
               "sem_preco": "sem preço", "sem_razao_plausivel": "sem razão clara",
               "erro": "erro"}
    for linha in sorted(relatorio["linhas"], key=lambda l: l["ativo"]):
        config = linha.get("configurada")
        implicita = linha.get("implicita")
        sugerida = linha.get("sugerida")
        print(f"  {linha['ativo']:<8} {linha['bdr']:<9} "
              f"{(f'{config:g}' if config else '—'):>8} "
              f"{(f'{implicita:.2f}' if implicita else '—'):>9} "
              f"{(f'{sugerida:g}' if sugerida else '—'):>9}  "
              f"{rotulos.get(linha.get('situacao'), '?')}"
              + (f" ({linha['detalhe']})" if linha.get("detalhe") else ""))

    print(f"\n  {relatorio['conferem']} conferem, {relatorio['corrigir']} a corrigir.")

    if relatorio["corrigir"]:
        print("\n  Cole isto em BDRS_POR_ACAO, em modules/bdr.py:\n")
        print("BDRS_POR_ACAO = {")
        for ativo, (nome, razao) in sorted(relatorio["tabela_sugerida"].items()):
            print(f'    "{ativo}": ("{nome}", {razao}),')
        print("}")
        print("\n  E atualize VERIFICADO_EM para o mês corrente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
