"""Motor de crédito: índices calculados por fórmula, nunca gerados por modelo.

Divisão de trabalho do módulo, que é o ponto do redesenho:

    LLM  -> só EXTRAI campos brutos do balanço (ativo, passivo, EBITDA, dívida)
    aqui -> CALCULA alavancagem, cobertura de juros e Altman Z a partir deles

Antes, `routers/fixed_income.py` pedia os próprios índices ao Gemini e usava a
resposta para carimbar APROVADO/REPROVADO. Número de crédito gerado por modelo
não é reproduzível nem auditável, e instrução de prompt ("não invente") não é
garantia. Estas funções são determinísticas: mesma entrada, mesmo resultado.

Regra que vale para tudo aqui: dado ausente vira None e o laudo sai
INCONCLUSIVO. Nunca preenchemos lacuna com estimativa — a versão anterior
chegava a assumir "ativo circulante = 40% do ativo total" quando o campo
faltava, o que produz um Z-Score com cara de medição.
"""

TETO_ALAVANCAGEM = 3.5
PISO_COBERTURA_JUROS = 1.5
PISO_Z_SCORE = 1.10

# Campos sem os quais não existe laudo.
CAMPOS_OBRIGATORIOS_Z = (
    "ativo_circulante", "passivo_circulante", "ativo_total",
    "ebitda", "patrimonio_liquido", "passivo_total",
)


def _num(valor):
    """Converte para float, ou None. String vazia e não-numérico viram None."""
    if valor is None or valor == "":
        return None
    try:
        convertido = float(valor)
    except (TypeError, ValueError):
        return None
    if convertido != convertido:  # NaN
        return None
    return convertido


def calcular_altman_z_score_emergente(ativo_circulante, passivo_circulante,
                                      ativo_total, lucros_retidos,
                                      ebitda, patrimonio_liquido,
                                      passivo_total):
    """Altman Z'' para mercados emergentes.

        Z = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4

    X1 capital de giro/ativo total, X2 lucros retidos/ativo total,
    X3 EBITDA/ativo total, X4 patrimônio líquido/passivo total.

    Devolve z_score None quando o denominador não existe — um Z calculado com
    ativo total zerado não é conservador, é inventado.
    """
    ativo_circulante = _num(ativo_circulante)
    passivo_circulante = _num(passivo_circulante)
    ativo_total = _num(ativo_total)
    lucros_retidos = _num(lucros_retidos)
    ebitda = _num(ebitda)
    patrimonio_liquido = _num(patrimonio_liquido)
    passivo_total = _num(passivo_total)

    faltando = []
    if ativo_total is None or ativo_total <= 0:
        faltando.append("ativo_total")
    if passivo_total is None or passivo_total <= 0:
        faltando.append("passivo_total")
    for nome, valor in (("ativo_circulante", ativo_circulante),
                        ("passivo_circulante", passivo_circulante),
                        ("ebitda", ebitda),
                        ("patrimonio_liquido", patrimonio_liquido)):
        if valor is None:
            faltando.append(nome)

    if faltando:
        return {"z_score": None, "classificacao": "Não calculável",
                "campos_faltantes": faltando}

    # Lucros retidos ausentes: o único campo que aceitamos como 0, porque a DFP
    # brasileira frequentemente não o destaca. Fica registrado no laudo.
    lucros_retidos_ausente = lucros_retidos is None
    if lucros_retidos_ausente:
        lucros_retidos = 0.0

    capital_giro = ativo_circulante - passivo_circulante

    x1 = capital_giro / ativo_total
    x2 = lucros_retidos / ativo_total
    x3 = ebitda / ativo_total
    x4 = patrimonio_liquido / passivo_total

    z_score = round(6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4, 2)

    if z_score < PISO_Z_SCORE:
        classificacao = "Zona de Estresse / Alto Risco de Insolvência"
    elif z_score <= 2.60:
        classificacao = "Zona Cinzenta / Alerta de Alavancagem"
    else:
        classificacao = "Zona Segura / Baixo Risco de Falência"

    return {
        "z_score": z_score,
        "classificacao": classificacao,
        "campos_faltantes": [],
        "componentes": {"x1_capital_giro": round(x1, 4), "x2_lucros_retidos": round(x2, 4),
                        "x3_ebitda": round(x3, 4), "x4_estrutura": round(x4, 4)},
        "lucros_retidos_assumidos_zero": lucros_retidos_ausente,
    }


def auditar_credito_corporativo(nome_emissor, divida_liquida, ebitda,
                                despesa_financeira_anual, z_metrics):
    """Laudo de crédito corporativo. Sem dado suficiente, status INCONCLUSIVO."""
    divida_liquida = _num(divida_liquida)
    ebitda = _num(ebitda)
    despesa_financeira_anual = _num(despesa_financeira_anual)

    z_info = calcular_altman_z_score_emergente(**z_metrics)

    if ebitda is not None and ebitda > 0 and divida_liquida is not None:
        alavancagem = round(divida_liquida / ebitda, 2)
    else:
        alavancagem = None

    # Despesa financeira vem negativa na DFP; o sinal não muda a cobertura.
    despesa = abs(despesa_financeira_anual) if despesa_financeira_anual else None
    if ebitda is not None and despesa and despesa > 0:
        icj = round(ebitda / despesa, 2)
    else:
        icj = None

    vetos = []
    if alavancagem is not None and alavancagem > TETO_ALAVANCAGEM:
        vetos.append(f"Dívida Líquida/EBITDA ({alavancagem}x) acima do teto de {TETO_ALAVANCAGEM}x.")
    if icj is not None and icj < PISO_COBERTURA_JUROS:
        vetos.append(f"Cobertura de Juros insuficiente ({icj}x): geração operacional não suporta os juros da dívida.")
    if z_info["z_score"] is not None and z_info["z_score"] < PISO_Z_SCORE:
        vetos.append(f"Altman Z-Score crítico ({z_info['z_score']}): risco de insolvência elevado.")
    if ebitda is not None and ebitda <= 0:
        vetos.append("EBITDA não positivo: sem geração operacional para servir dívida.")

    indisponiveis = []
    if alavancagem is None:
        indisponiveis.append("alavancagem")
    if icj is None:
        indisponiveis.append("cobertura de juros")
    if z_info["z_score"] is None:
        indisponiveis.append("Altman Z-Score")

    if vetos:
        status = "REPROVADO / VETO"
    elif indisponiveis:
        # Sem os três índices não existe aprovação: ausência de veto não é
        # evidência de solidez.
        status = "INCONCLUSIVO / DADO INSUFICIENTE"
    else:
        status = "APROVADO / ALTA CONFIANÇA"

    return {
        "emissor": nome_emissor,
        "tipo_ativo": "Credito Corporativo",
        "status": status,
        "alavancagem_dl_ebitda": alavancagem,
        "cobertura_juros_icj": icj,
        "altman_z_score": z_info["z_score"],
        "classificacao_z": z_info["classificacao"],
        "motivos_veto": vetos,
        "indices_indisponiveis": indisponiveis,
        "campos_faltantes": z_info.get("campos_faltantes", []),
        "detalhe_z": z_info.get("componentes"),
        "lucros_retidos_assumidos_zero": z_info.get("lucros_retidos_assumidos_zero", False),
    }


def auditar_ativo_bancario(nome_banco, indice_basileia, indice_imobilizacao,
                           lucros_ultimos_3_anos):
    """Laudo para emissor bancário (CDB/LCI/LCA)."""
    basileia = _num(indice_basileia)
    imobilizacao = _num(indice_imobilizacao)

    vetos = []
    indisponiveis = []

    if basileia is None:
        indisponiveis.append("índice de Basileia")
    elif basileia < 11.0:
        vetos.append(f"Índice de Basileia ({basileia}%) abaixo do patamar prudencial de 11%.")

    if imobilizacao is None:
        indisponiveis.append("índice de imobilização")
    elif imobilizacao > 50.0:
        vetos.append(f"Índice de Imobilização elevado ({imobilizacao}%): risco de liquidez patrimonial.")

    if lucros_ultimos_3_anos is None:
        indisponiveis.append("histórico de lucratividade")
    elif not lucros_ultimos_3_anos:
        vetos.append("Instituição financeira com prejuízos recorrentes nos últimos balanços.")

    if vetos:
        status = "REPROVADO / VETO"
    elif indisponiveis:
        status = "INCONCLUSIVO / DADO INSUFICIENTE"
    else:
        status = "APROVADO / SEGURO"

    if lucros_ultimos_3_anos is None:
        historico = "Não informado"
    else:
        historico = "Consistente" if lucros_ultimos_3_anos else "Prejuízos Recorrentes"

    return {
        "instituicao": nome_banco,
        "tipo_ativo": "Ativo Bancario (CDB/LCI/LCA)",
        "status": status,
        "indice_basileia": f"{basileia}%" if basileia is not None else None,
        "indice_imobilizacao": f"{imobilizacao}%" if imobilizacao is not None else None,
        "historico_lucratividade": historico,
        "motivos_veto": vetos,
        "indices_indisponiveis": indisponiveis,
    }


# Campos do balanço que o Yahoo expõe, e o nome que usamos internamente.
_MAPA_BALANCO = {
    "ativo_total": "Total Assets",
    "passivo_total": "Total Liabilities Net Minority Interest",
    "ativo_circulante": "Current Assets",
    "passivo_circulante": "Current Liabilities",
    "patrimonio_liquido": "Stockholders Equity",
    "lucros_retidos": "Retained Earnings",
}


def auditar_ticker_b3(ticker_input):
    """Laudo de empresa listada, a partir do balanço publicado no Yahoo.

    Campo que não vier fica None e derruba o laudo para INCONCLUSIVO. A versão
    anterior preenchia ativo circulante com 40% do ativo total e despesa
    financeira com 15% do EBITDA quando faltavam — números inventados que
    entravam no Z-Score como se fossem medidos.
    """
    import yfinance as yf

    ticker = (ticker_input or "").upper().strip()
    if not ticker:
        return {"erro": "Informe um ticker."}
    simbolo = ticker if ticker.endswith(".SA") else f"{ticker}.SA"

    try:
        ativo = yf.Ticker(simbolo)
        try:
            info = ativo.info or {}
        except Exception:  # noqa: BLE001
            info = {}

        balanco = ativo.balance_sheet
        financeiro = ativo.financials

        def do_balanco(rotulo):
            try:
                serie = balanco.loc[rotulo].dropna()
                return float(serie.iloc[0]) if len(serie) else None
            except Exception:  # noqa: BLE001
                return None

        campos = {chave: do_balanco(rotulo) for chave, rotulo in _MAPA_BALANCO.items()}

        ebitda = _num(info.get("ebitda"))
        divida_total = _num(info.get("totalDebt"))
        caixa = _num(info.get("totalCash"))
        divida_liquida = (divida_total - caixa) if (divida_total is not None and caixa is not None) else None

        try:
            serie = financeiro.loc["Interest Expense"].dropna()
            despesa_financeira = float(serie.iloc[0]) if len(serie) else None
        except Exception:  # noqa: BLE001
            despesa_financeira = None

        z_metrics = dict(campos)
        z_metrics["ebitda"] = ebitda

        resultado = auditar_credito_corporativo(
            nome_emissor=info.get("shortName") or ticker,
            divida_liquida=divida_liquida,
            ebitda=ebitda,
            despesa_financeira_anual=despesa_financeira,
            z_metrics=z_metrics,
        )
        resultado["origem_dados"] = "Balanço publicado (Yahoo Finance)"
        resultado["ticker_analisado"] = simbolo
        resultado["campos_brutos"] = {**campos, "ebitda": ebitda,
                                      "divida_liquida": divida_liquida,
                                      "despesa_financeira": despesa_financeira}
        return resultado

    except Exception as exc:  # noqa: BLE001
        return {"erro": f"Falha ao ler o balanço de {ticker}: {type(exc).__name__}: {exc}"}


# NOTA: `auditar_empresa_cvm` foi removida. Ela consultava a tabela
# `cvm_balancos` em `dados_mercado.db` — tabela que não existe: esse arquivo só
# tem `cotacoes_b3`, e a base da CVM (`cvm_dados.db`) guarda `demonstracoes`,
# com valores gravados como texto formatado ("R$ 1.23 Bi") e sem patrimônio
# líquido. Para ressuscitar esse caminho é preciso primeiro reescrever
# `coletor_cvm.py` para gravar números crus, incluindo a conta 2.03
# (patrimônio líquido) e o CNPJ, e mapear CNPJ -> ticker.
