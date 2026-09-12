"""Confere os motores contra dado REAL, e diz onde a integração falhou.

Os testes de `test_filosofias.py` provam a regra de negócio com fontes falsas.
Este script prova a outra metade: se o que a API devolve hoje ainda tem os
campos que o código espera. São falhas diferentes, e só a segunda depende de
o Yahoo não ter renomeado uma linha de balanço desde a última versão.

    python conferir_filosofias.py
    python conferir_filosofias.py --papel BBAS3 --acao MSFT

O que interessa na saída não é o número: é **quais campos vieram vazios**.
Campo vazio aqui é contrato quebrado, não ativo sem dado.
"""

import argparse
import logging
import sys

logging.basicConfig(level=logging.WARNING,
                    format="  [log] %(levelname)s %(name)s: %(message)s")


def rotulo(texto):
    print(f"\n{'=' * 68}\n{texto}\n{'=' * 68}")


def campo(nome, valor, essencial=False):
    if valor is None or (isinstance(valor, str) and not valor.strip()):
        marca = "VAZIO" if essencial else "vazio"
        print(f"  {marca:>7}  {nome}")
        return not essencial
    if isinstance(valor, float):
        print(f"  {'ok':>7}  {nome} = {valor:,.4f}".replace(",", "."))
    else:
        print(f"  {'ok':>7}  {nome} = {valor}")
    return True


def main():
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("--papel", default="TAEE11",
                            help="Ticker BESST sem .SA (padrão: TAEE11)")
    analisador.add_argument("--acao", default="AAPL",
                            help="Ticker americano (padrão: AAPL)")
    args = analisador.parse_args()

    from modules import bdr, filosofias, fontes, fundamentos_cvm, taxas

    problemas = []
    fonte = fontes.FonteYahoo()

    # ------------------------------------------------------------------
    rotulo("1. Yahoo — preço, proventos e perfil")
    simbolo = f"{args.papel}.SA"

    perfil = fonte.perfil(simbolo)
    print(f"\n  perfil de {simbolo}:")
    if not campo("preco", perfil.get("preco"), essencial=True):
        problemas.append(f"preço de {simbolo} não veio — o motor Barsi não roda sem ele")
    campo("nome", perfil.get("nome"))
    campo("moeda", perfil.get("moeda"))
    campo("volume", perfil.get("volume"))

    precos = fonte.precos(simbolo, periodo="2y")
    print(f"\n  histórico de {simbolo}:")
    if precos is None or len(precos) < 250:
        print(f"  {'VAZIO':>7}  série com {0 if precos is None else len(precos)} "
              "pregões — o momentum 12M-1M precisa de ~500")
        problemas.append(f"histórico de {simbolo} curto demais para momentum")
    else:
        print(f"  {'ok':>7}  {len(precos)} pregões, de "
              f"{precos.index[0].date()} a {precos.index[-1].date()}")

    proventos = fonte.dividendos(simbolo)
    print(f"\n  proventos de {simbolo}:")
    if proventos is None or not len(proventos):
        print(f"  {'VAZIO':>7}  nenhum provento — sem DPA não há preço teto")
        problemas.append(f"proventos de {simbolo} não vieram")
    else:
        por_ano = proventos.groupby(proventos.index.year).sum()
        print(f"  {'ok':>7}  {len(proventos)} pagamentos; por ano:")
        for ano, valor in list(por_ano.items())[-5:]:
            print(f"            {ano}: {valor:.4f}")
        print("            (confira se JCP está incluído — se o valor parecer "
              "baixo, o Yahoo separou)")

    # ------------------------------------------------------------------
    rotulo("2. Base da CVM — balanço")
    if not fundamentos_cvm.base_disponivel():
        print("  VAZIO  fundamentos_cvm.db não encontrado")
        problemas.append("base da CVM ausente")
    else:
        from modules import cadastro_b3
        cnpj = cadastro_b3.cnpj_do_ticker(args.papel)
        print(f"\n  CNPJ de {args.papel}: {cnpj or 'NÃO ENCONTRADO'}")
        if not cnpj:
            problemas.append(f"{args.papel} sem CNPJ no cadastro da B3")
        else:
            balanco = fundamentos_cvm.balanco_por_cnpj(cnpj)
            if not balanco:
                print("  VAZIO  sem balanço para este CNPJ")
                problemas.append(f"{args.papel} sem balanço na base da CVM")
            else:
                print(f"\n  balanço {balanco.get('ano')} — {balanco.get('denom_cia')}:")
                for nome in ("lpa_on", "ebit", "lucro_liquido",
                             "divida_curto_prazo", "divida_longo_prazo", "caixa"):
                    campo(nome, fontes.numero(balanco.get(nome)))
            historico = fundamentos_cvm.historico_por_cnpj(cnpj)
            print(f"\n  exercícios disponíveis: {[h.get('ano') for h in historico]}")

    # ------------------------------------------------------------------
    rotulo("3. BCB — Selic")
    selic = taxas.obter_selic_meta()
    campo("valor", selic.get("valor"), essencial=True)
    campo("origem", selic.get("origem"))
    if selic.get("origem") == "fallback":
        problemas.append("BCB fora do ar — Selic veio da constante embutida")

    # ------------------------------------------------------------------
    rotulo(f"4. Barsi — {args.papel} ponta a ponta")
    motor = filosofias.PhilosophyEngine(fonte=fonte)
    linha = motor._avaliar_barsi(args.papel, "energia", aplicar_momentum=True)
    if linha is None:
        print("  VAZIO  o motor não conseguiu avaliar o papel")
        problemas.append(f"Barsi não avaliou {args.papel}")
    else:
        for nome, essencial in [("preco", True), ("dpa_projetado", True),
                                ("preco_teto", True), ("margem_seguranca", True),
                                ("payout", False), ("divida_liquida_ebit", False),
                                ("yield_sobre_preco", False)]:
            if not campo(nome, linha.get(nome), essencial):
                problemas.append(f"Barsi: {nome} não apurado para {args.papel}")
        print(f"\n  aprovado: {linha['aprovado']}")
        for motivo in linha["motivos"]:
            print(f"     reprova: {motivo}")
        for item in linha["nao_apurados"]:
            print(f"     não apurado: {item}")
        momento = linha.get("momentum") or {}
        print(f"\n  momentum: {momento.get('veredito')} — {momento.get('motivo')}")
        campo("momentum_12m_1m", momento.get("momentum_12m_1m"))
        campo("rsi_semanal", momento.get("rsi_semanal"))

    # ------------------------------------------------------------------
    rotulo(f"5. Greenblatt — {args.acao} ponta a ponta")
    import os
    identificacao = os.environ.get("SEC_USER_AGENT", "").strip()
    fonte_sec = None
    if identificacao and "@" in identificacao:
        try:
            fonte_sec = fontes.FonteSEC(identificacao)
            print(f"  SEC configurada como: {identificacao}")
        except Exception as falha:
            print(f"  SEC recusou a identificação: {falha}")
    else:
        print("  SEC_USER_AGENT não definido — o balanço virá do Yahoo.")
        print("  Para usar a fonte auditada, ponha no .env:")
        print("     SEC_USER_AGENT=Seu Nome seu@email.com")

    motor_eua = filosofias.PhilosophyEngine(fonte=fonte, fonte_sec=fonte_sec)
    contabil = motor_eua._contabil_eua(args.acao)
    print(f"\n  contabilidade de {args.acao} (origem: {contabil.get('origem')}):")
    for nome, essencial in [("ebit", True), ("caixa", False), ("divida_total", False),
                            ("ativo_circulante", True), ("passivo_circulante", True),
                            ("imobilizado", True), ("dividendos_pagos", False),
                            ("recompras", False), ("exercicio", False)]:
        if not campo(nome, contabil.get(nome), essencial):
            problemas.append(f"Greenblatt: {nome} não veio para {args.acao} "
                             f"(origem {contabil.get('origem')})")

    linha = motor_eua._avaliar_greenblatt(args.acao)
    if linha is None:
        print("\n  VAZIO  o motor não conseguiu avaliar a ação")
        problemas.append(f"Greenblatt não avaliou {args.acao}")
    else:
        print()
        campo("setor", linha.get("setor"), essencial=True)
        campo("valor_mercado", linha.get("valor_mercado"), essencial=True)
        campo("roic", linha.get("roic"))
        campo("ev_ebit", linha.get("ev_ebit"))
        campo("shareholder_yield", linha.get("shareholder_yield"))
        print(f"\n  elegível: {linha['elegivel']}")
        for motivo in linha["motivos"]:
            print(f"     motivo: {motivo}")

    # ------------------------------------------------------------------
    rotulo(f"6. BDR — {args.acao} contra o BDR na B3")
    painel = bdr.GlobalEquitiesPanel(fonte=fonte)
    cambio, _ = painel.dolar()
    if not campo("dolar", cambio, essencial=True):
        problemas.append("cotação do dólar não veio — o painel de BDR não roda")
    linha = painel.avaliar(args.acao, cambio)
    for nome in ("bdr", "preco_usd", "preco_brl", "razao_configurada",
                 "razao_implicita", "razao_usada", "origem_razao",
                 "preco_justo_brl", "spread_pct", "volume_bdr"):
        campo(nome, linha.get(nome))
    print(f"\n  confiável: {linha['confiavel']}")
    for alerta in linha["alertas"]:
        print(f"     alerta: {alerta}")
    if linha.get("razao_implicita") and linha.get("razao_configurada"):
        desvio = abs(linha["razao_implicita"] - linha["razao_configurada"]) \
            / linha["razao_configurada"]
        if desvio > bdr.TOLERANCIA_RAZAO:
            problemas.append(
                f"fator de paridade de {args.acao} está errado na tabela "
                f"(configurado {linha['razao_configurada']:g}, "
                f"mercado indica {linha['razao_implicita']:.2f})")

    # ------------------------------------------------------------------
    rotulo("Resumo")
    if not problemas:
        print("\n  Nenhum contrato quebrado. Os motores estão lendo dado real.\n")
        return 0
    print(f"\n  {len(problemas)} ponto(s) para olhar:\n")
    for item in problemas:
        print(f"   - {item}")
    print("\n  Campo vazio costuma ser nome de campo que a fonte renomeou, não")
    print("  ativo sem dado. Me mande esta saída que eu ajusto o mapeamento.\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
