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

from modules import mandato

registro = logging.getLogger(__name__)

CONFORME = "conforme"
ATENCAO = "atencao"
DESCONFORME = "desconforme"
NAO_APURADO = "nao_apurado"
# Não é o mesmo que NAO_APURADO. NAO_APURADO é "não foi possível medir" ou
# "ainda não escolheu a filosofia" — os dois pedem ação (esperar dado, ou
# escolher). SEM_FILOSOFIA é "o investidor escolheu não ser julgado por
# nenhuma das três teses", uma decisão já tomada, e a tela não pode tratar as
# duas coisas como se fossem a mesma pendência.
SEM_FILOSOFIA = "sem_filosofia"

ROTULOS = {
    CONFORME: "Conforme",
    ATENCAO: "Atenção",
    DESCONFORME: "Desconformidade",
    NAO_APURADO: "Não apurado",
    SEM_FILOSOFIA: "Sem filosofia",
}

# Ordem de gravidade, para ordenar a lista pelo que pede olhar primeiro.
# "Sem filosofia" fica ao lado de "conforme": não é urgência, é modo de leitura.
GRAVIDADE = {DESCONFORME: 0, ATENCAO: 1, NAO_APURADO: 2, SEM_FILOSOFIA: 3,
            CONFORME: 4}

# Consultas simultâneas ao avaliar a carteira. Cada posição pede perfil no
# Yahoo; o mesmo teto do radar de fundos, pelo mesmo motivo — é o `.info` que
# o Yahoo limita, e paralelismo demais devolve tabela vazia.
MAX_WORKERS = 6

# FII negociando acima disto do valor patrimonial não está "errado", mas está
# caro para entrada. Mesma faixa do `_recomendacao_fii` do radar.
PVP_AGIO = 1.05


def _veredito_graham(linha):
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


# Motivos do Barsi que indicam DETERIORAÇÃO da empresa, e não preço alto. A
# separação é a mesma de Graham e existe pelo mesmo motivo: "está caro" manda
# esperar, "teve prejuízo" manda rever a tese, e só o segundo é desconformidade.
# Margem de segurança e projeção de DPA ficam de fora — são preço.
DETERIORACAO_BARSI = ("prejuízo", "payout", "dívida líquida")


def _veredito_barsi(linha):
    """(estado, resumo, detalhes) a partir da avaliação de Barsi."""
    if linha is None:
        return NAO_APURADO, "Sem preço na fonte de mercado.", []

    if linha.get("fora_do_escopo"):
        return NAO_APURADO, linha.get("motivo_escopo") or "Fora do escopo do método.", []

    motivos = linha.get("motivos") or []
    if linha.get("aprovado"):
        teto = linha.get("preco_teto")
        return CONFORME, (f"Abaixo do preço-teto de R$ {teto:.2f} com qualidade medida."
                          if teto else "Cumpre os critérios da tese de renda."), []

    graves = [m for m in motivos
              if any(marca in m.lower() for marca in DETERIORACAO_BARSI)]
    if graves:
        return DESCONFORME, graves[0], graves
    return ATENCAO, motivos[0] if motivos else "Não cumpre algum critério.", motivos


# Mesma lógica para Bazin. `abaixo_do_teto` e `dy_suficiente` são preço; payout
# e alavancagem são a empresa.
DETERIORACAO_BAZIN = ("payout_saudavel", "alavancagem_ok")

EXPLICA_BAZIN = {
    "dy_suficiente": "Dividend yield abaixo dos 6% que o método exige.",
    "payout_saudavel": "Payout fora da faixa de 30% a 80%.",
    "alavancagem_ok": "Dívida líquida sobre EBIT acima de 2,5x.",
    "abaixo_do_teto": "Preço acima do teto que entregaria 6% de yield.",
}


def _veredito_bazin(linha):
    """(estado, resumo, detalhes) a partir da avaliação de Bazin."""
    if linha is None:
        return NAO_APURADO, "Sem preço na fonte de mercado.", []

    criterios = linha.get("criterios") or {}
    faltantes = linha.get("criterios_nao_apurados") or []

    reprovados = [chave for chave, valor in criterios.items() if valor is False]
    graves = [c for c in reprovados if c in DETERIORACAO_BAZIN]

    if graves:
        return DESCONFORME, EXPLICA_BAZIN[graves[0]], [EXPLICA_BAZIN[c] for c in graves]
    if linha.get("aprovado"):
        teto = linha.get("preco_teto")
        return CONFORME, (f"Abaixo do preço-teto de R$ {teto:.2f}, com payout e "
                          f"alavancagem dentro da faixa."
                          if teto else "Cumpre os critérios do método."), []
    if reprovados:
        return ATENCAO, EXPLICA_BAZIN[reprovados[0]], [EXPLICA_BAZIN[c] for c in reprovados]
    if faltantes:
        # Sem aprovação por ausência: critério não medido não vira aprovação,
        # e também não vira reprovação.
        nomes = ", ".join(EXPLICA_BAZIN.get(c, c) for c in faltantes)
        return NAO_APURADO, f"Critério sem dado para medir: {nomes}", []
    return ATENCAO, "Não cumpre algum critério.", []


def _avaliar_acao(motor, ticker, filosofia):
    """(estado, resumo, detalhes, metodo, extras) pela filosofia declarada."""
    if filosofia == mandato.BARSI:
        setor = motor.setor_besst(ticker)
        if setor is None:
            # Barsi é uma tese sobre setores perenes e regulados. Aplicá-la a
            # uma varejista não produz reprovação, produz uma pergunta que o
            # método não faz — mesmo tratamento que Graham dá a banco.
            return (NAO_APURADO,
                    "Fora dos setores da tese BESST (bancos, energia, "
                    "saneamento, seguros e telecomunicações).",
                    [], "Barsi (renda por setor perene)", {})
        linha = motor._avaliar_barsi(ticker, setor, aplicar_momentum=False)
        estado, resumo, detalhes = _veredito_barsi(linha)
        extras = {}
        if linha:
            extras = {"preco_teto": linha.get("preco_teto"),
                      "margem_seguranca": linha.get("margem_seguranca"),
                      "yield_sobre_preco": linha.get("yield_sobre_preco"),
                      "payout": linha.get("payout"),
                      "tendencia_dpa": (linha.get("tendencia_dpa") or {}).get("classificacao"),
                      "setor_besst": setor}
        return estado, resumo, detalhes, "Barsi (renda por setor perene)", extras

    if filosofia == mandato.BAZIN:
        linha = motor._avaliar_bazin(ticker)
        estado, resumo, detalhes = _veredito_bazin(linha)
        extras = {}
        if linha:
            extras = {"preco_teto": linha.get("preco_teto"),
                      "margem_seguranca": linha.get("margem_seguranca"),
                      "dy_12m": linha.get("dy_12m"),
                      "payout": linha.get("payout"),
                      "dl_ebit": linha.get("dl_ebit")}
        return estado, resumo, detalhes, "Bazin (renda por preço-teto)", extras

    linha = motor._avaliar_graham(ticker, aplicar_momentum=False)
    estado, resumo, detalhes = _veredito_graham(linha)
    extras = {}
    if linha:
        extras = {"numero_graham": linha.get("numero_graham"),
                  "margem_seguranca": linha.get("margem_seguranca"),
                  "criterios_medidos": linha.get("criterios_medidos")}
    return estado, resumo, detalhes, "Graham (investidor defensivo)", extras


def _avaliar_sem_filosofia(motor, ticker):
    """(estado, resumo, detalhes, metodo, extras) quando o investidor escolheu
    não aplicar nenhuma filosofia. Reaproveita o que Graham já calcula
    (preço, balanço, múltiplos), mas só os NÚMEROS — nunca `aprovado` nem
    `motivos`. Misturar os dois devolveria exatamente o veredito que o
    investidor pediu para não receber."""
    try:
        linha = motor._avaliar_graham(ticker, aplicar_momentum=False)
    except Exception:  # noqa: BLE001
        linha = None
    try:
        balanco = motor._balanco_cvm(ticker) or {}
    except Exception:  # noqa: BLE001
        balanco = {}

    extras = {}
    if linha:
        extras.update(numero_graham=linha.get("numero_graham"),
                      pvp=linha.get("pvp"),
                      produto_pl_pvp=linha.get("produto_pl_pvp"))

    from modules import quant
    patrimonio = balanco.get("patrimonio_liquido")
    lucro = balanco.get("lucro_liquido")
    receita = balanco.get("receita_liquida")
    if lucro is not None and patrimonio:
        extras["roe"] = round(lucro / patrimonio * 100.0, 2)
    if lucro is not None and receita:
        extras["margem_liquida"] = round(lucro / receita * 100.0, 2)
    divida = quant.divida_liquida(balanco.get("divida_curto_prazo"),
                                  balanco.get("divida_longo_prazo"),
                                  balanco.get("caixa"))
    dl_ebit = quant.dl_sobre_ebit(divida, balanco.get("ebit"))
    if dl_ebit is not None:
        extras["dl_ebit"] = round(dl_ebit, 2)

    # O resumo é onde a tela HOJE mostra o texto do estado (a bolha traz só o
    # rótulo; o número mora no title). "Sem veredito" sem o dado junto seria
    # uma régua a menos sem nada em troca — o ponto inteiro de "nenhuma" é
    # entregar o número cru em vez do veredito.
    partes = []
    if extras.get("pvp") is not None:
        partes.append(f"P/VP {extras['pvp']:.2f}")
    if extras.get("roe") is not None:
        partes.append(f"ROE {extras['roe']:.1f}%")
    if extras.get("margem_liquida") is not None:
        partes.append(f"margem líquida {extras['margem_liquida']:.1f}%")
    if extras.get("dl_ebit") is not None:
        partes.append(f"DL/EBIT {extras['dl_ebit']:.2f}x")

    resumo = ("Sem preço ou balanço suficiente para mostrar os números."
             if not partes else
             "Sem filosofia aplicada — " + " · ".join(partes) + ".")
    return SEM_FILOSOFIA, resumo, [], "Nenhuma (dado bruto)", extras


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
            "estado": NAO_APURADO, "resumo": None, "detalhes": [],
            "filosofia": None}

    try:
        if classe == "acao":
            filosofia = posicao.get("filosofia_efetiva")
            if filosofia is None:
                # Sem filosofia declarada não há pergunta a fazer. Medir por
                # Graham "porque é o padrão" foi exatamente o que produzia
                # ruído para quem tem mandato de renda.
                base.update(
                    metodo="Não definido",
                    resumo=("Escolha a filosofia da carteira para esta posição "
                            "ser medida."))
            elif filosofia == mandato.NENHUMA:
                # Diferente do caso acima: aqui já HOUVE escolha — a de não
                # ser julgado por nenhuma das três teses. Não é a mesma
                # pendência, e por isso não é o mesmo estado.
                estado, resumo, detalhes, metodo, extras = _avaliar_sem_filosofia(
                    motor, ticker)
                base.update(metodo=metodo, estado=estado, resumo=resumo,
                            detalhes=detalhes, filosofia=filosofia, **extras)
            else:
                estado, resumo, detalhes, metodo, extras = _avaliar_acao(
                    motor, ticker, filosofia)
                base.update(metodo=metodo, estado=estado, resumo=resumo,
                            detalhes=detalhes, filosofia=filosofia, **extras)

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


def diagnosticar(motor, posicoes, filosofia_carteira=None):
    """Veredito de cada posição e o retrato do conjunto.

    O peso de cada estado é sobre CUSTO, e é ele que importa mais que a
    contagem: três posições em desconformidade valendo 2% da carteira é um
    problema diferente de uma valendo 40%.

    `filosofia_carteira` é a lente declarada pelo investidor. Cada posição pode
    ter a sua, e a da posição vence — ver `modules/mandato.py`.
    """
    posicoes = list(posicoes or [])
    # Resolvida UMA vez, aqui, e não dentro de cada avaliação: assim a posição
    # que chega ao avaliador já sabe por qual régua vai ser medida, e o teste
    # do avaliador não precisa do banco para existir.
    posicoes = [{**p, "filosofia_efetiva":
                 mandato.resolver(filosofia_carteira, p.get("filosofia"))}
                for p in posicoes]
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
