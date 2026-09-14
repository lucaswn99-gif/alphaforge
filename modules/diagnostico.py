"""Diagnóstico da carteira: cada posição medida contra a filosofia que cabe a ela.

Liga `modules/carteira.py` ao `PhilosophyEngine`. Não guarda nada: recebe as
posições, devolve o veredito de cada uma e o retrato do conjunto.

**São quatro estados, não três.** O pilar pedia três — conforme, atenção e
desconformidade — e o quarto existe porque sem ele o motor mente:

    conforme     cumpre os critérios aplicáveis
    atencao      cumpre com ressalva, ou não cabe no critério por PREÇO
    desconforme  deterioração medida: prejuízo, dívida acima do capital de giro
    nao_apurado  NÃO FOI POSSÍVEL MEDIR

Sem `nao_apurado`, um papel sem balanço na base cairia em "desconformidade
crítica" — acusar de falha o que só não se conseguiu medir. Este projeto já
tropeçou nisso duas vezes (Barsi aprovando por ausência de critério, Graham
aprovando três bancos com "4 de 6"), e nas duas a correção foi a mesma:
ausência é um estado próprio, nunca um veredito.

**Preço alto não é desconformidade.** Graham reprova por preço E por
qualidade com a mesma lista de motivos, e as duas coisas pedem ações opostas:
"está caro" significa não aportar agora, "teve prejuízo" significa rever a
tese. Um papel excelente que subiu demais não é candidato a reciclagem — é
candidato a esperar. Por isso só `alertas_qualidade` (ver
`filosofias._avaliar_graham`) leva a `desconforme`.

**Cada classe é medida pelo que cabe nela.** Ação vai pelo investidor
defensivo de Graham; FII vai por desconto patrimonial, que é o gatilho de
entrada do ativo; ETF não é empresa — é cesta de índice, e critério
fundamentalista de companhia não se aplica. Forçar os três no mesmo teste
produziria veredito com cara de rigor e conteúdo de ruído.
"""

import concurrent.futures
import logging

registro = logging.getLogger(__name__)

CONFORME = "conforme"
ATENCAO = "atencao"
DESCONFORME = "desconforme"
NAO_APURADO = "nao_apurado"

ROTULOS = {
    CONFORME: "Conforme",
    ATENCAO: "Atenção",
    DESCONFORME: "Desconformidade",
    NAO_APURADO: "Não apurado",
}

# Ordem de gravidade, para ordenar a lista pelo que pede olhar primeiro.
GRAVIDADE = {DESCONFORME: 0, ATENCAO: 1, NAO_APURADO: 2, CONFORME: 3}

# Consultas simultâneas ao avaliar a carteira. Cada posição pede perfil no
# Yahoo; o mesmo teto do radar de fundos, pelo mesmo motivo — é o `.info` que
# o Yahoo limita, e paralelismo demais devolve tabela vazia.
MAX_WORKERS = 6

# FII negociando acima disto do valor patrimonial não está "errado", mas está
# caro para entrada. Mesma faixa do `_recomendacao_fii` do radar.
PVP_AGIO = 1.05


def _veredito_acao(linha):
    """(estado, resumo, detalhes) a partir da avaliação de Graham."""
    if linha is None:
        return NAO_APURADO, "Sem preço ou sem balanço na base.", []

    if linha.get("fora_do_escopo"):
        # Banco e seguradora: Graham não aplicava os critérios do defensivo a
        # instituição financeira. Não é falha do papel nem da nossa base.
        return NAO_APURADO, linha.get("motivo_escopo") or "Fora do escopo do método.", []

    alertas = linha.get("alertas_qualidade") or []
    if alertas:
        return DESCONFORME, alertas[0], alertas

    motivos = linha.get("motivos") or []
    if linha.get("aprovado"):
        return CONFORME, "Cumpre os sete critérios do investidor defensivo.", []

    # Reprovou, mas sem deterioração medida: é preço, porte ou crescimento —
    # motivo para não aportar agora, não para reciclar.
    return ATENCAO, motivos[0] if motivos else "Não cumpre algum critério.", motivos


def _veredito_fii(informe, preco):
    """(estado, resumo, detalhes) pelo desconto sobre o valor patrimonial."""
    pvp = (informe or {}).get("pvp")
    if pvp is None:
        return NAO_APURADO, "Sem P/VP no Informe Mensal da CVM.", []
    if pvp < 1.0:
        return CONFORME, f"Negociando a {pvp:.2f}x o valor patrimonial.", []
    if pvp <= PVP_AGIO:
        return ATENCAO, f"Colado no valor patrimonial ({pvp:.2f}x).", []
    return ATENCAO, f"Ágio de {(pvp - 1) * 100:.0f}% sobre o valor patrimonial.", []


def avaliar_posicao(motor, posicao):
    """Veredito de uma posição. Nunca levanta: uma falha de fonte não pode
    derrubar o diagnóstico da carteira inteira."""
    ticker = posicao["ticker"]
    classe = posicao.get("classe") or "desconhecida"
    base = {"ticker": ticker, "classe": classe, "metodo": None,
            "estado": NAO_APURADO, "resumo": None, "detalhes": []}

    try:
        if classe == "acao":
            linha = motor._avaliar_graham(ticker, aplicar_momentum=False)
            estado, resumo, detalhes = _veredito_acao(linha)
            base.update(metodo="Graham (investidor defensivo)", estado=estado,
                        resumo=resumo, detalhes=detalhes)
            if linha:
                base["numero_graham"] = linha.get("numero_graham")
                base["margem_seguranca"] = linha.get("margem_seguranca")
                base["criterios_medidos"] = linha.get("criterios_medidos")

        elif classe == "fii":
            from modules import fundamentos_fii
            preco = posicao.get("preco_medio")
            informe = fundamentos_fii.pvp_do_fii(ticker, preco)
            estado, resumo, detalhes = _veredito_fii(informe, preco)
            base.update(metodo="Desconto patrimonial (P/VP)", estado=estado,
                        resumo=resumo, detalhes=detalhes)
            base["pvp"] = informe.get("pvp")
            base["competencia_vp"] = informe.get("competencia")
            # O P/VP sai do PREÇO MÉDIO da posição, não da cotação de hoje:
            # a pergunta aqui é "o que você pagou foi desconto?", não "está
            # barato agora". A segunda pergunta é a do radar de fundos.
            base["base_do_pvp"] = "preço médio da posição"

        elif classe == "etf":
            base.update(
                metodo="Não se aplica",
                resumo=("ETF é cesta de índice, não empresa — critério "
                        "fundamentalista de companhia não se aplica."))

        else:
            base.update(
                metodo="Não se aplica",
                resumo="Papel fora dos nossos registros: não há o que medir.")

    except Exception as falha:  # noqa: BLE001
        registro.exception("diagnostico(%s)", ticker)
        base.update(resumo=f"Falha ao avaliar: {type(falha).__name__}.")

    return base


def diagnosticar(motor, posicoes):
    """Veredito de cada posição e o retrato do conjunto.

    O peso de cada estado é sobre CUSTO, e é ele que importa mais que a
    contagem: três posições em desconformidade valendo 2% da carteira é um
    problema diferente de uma valendo 40%.
    """
    posicoes = list(posicoes or [])
    if not posicoes:
        return {"posicoes": [], "resumo": {estado: 0 for estado in ROTULOS},
                "peso_por_estado": {estado: 0.0 for estado in ROTULOS},
                "custo_total": 0.0, "avaliadas": 0}

    vereditos = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futuros = {pool.submit(avaliar_posicao, motor, p): p["ticker"] for p in posicoes}
        for futuro in concurrent.futures.as_completed(futuros):
            ticker = futuros[futuro]
            try:
                vereditos[ticker] = futuro.result()
            except Exception:  # noqa: BLE001
                vereditos[ticker] = {"ticker": ticker, "estado": NAO_APURADO,
                                     "metodo": None, "resumo": "Falha ao avaliar.",
                                     "detalhes": [], "classe": None}

    custo_total = sum(p.get("custo_total") or 0.0 for p in posicoes)
    resumo = {estado: 0 for estado in ROTULOS}
    peso = {estado: 0.0 for estado in ROTULOS}

    saida = []
    for posicao in posicoes:
        veredito = vereditos.get(posicao["ticker"], {})
        estado = veredito.get("estado", NAO_APURADO)
        custo = posicao.get("custo_total") or 0.0
        resumo[estado] += 1
        peso[estado] += custo
        saida.append({
            **posicao,
            "diagnostico": {**veredito, "rotulo": ROTULOS.get(estado, estado)},
        })

    if custo_total:
        peso = {estado: round(valor / custo_total * 100.0, 2)
                for estado, valor in peso.items()}
    else:
        peso = {estado: 0.0 for estado in peso}

    # Ordena pelo que pede olhar primeiro; desempata pelo tamanho da posição,
    # porque desconformidade em 1% da carteira e em 30% não são o mesmo aviso.
    saida.sort(key=lambda l: (GRAVIDADE.get(l["diagnostico"]["estado"], 9),
                              -(l.get("custo_total") or 0.0)))

    return {"posicoes": saida, "resumo": resumo, "peso_por_estado": peso,
            "custo_total": round(custo_total, 2), "avaliadas": len(saida),
            "rotulos": ROTULOS}
