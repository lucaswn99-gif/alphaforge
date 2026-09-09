"""Laudo de crédito de companhia listada, a partir do balanço da CVM.

Substitui o caminho que dependia do Yahoo (`.info` e `balance_sheet`), que não
responde de datacenter — no Render o laudo de emissor vinha vazio. E substitui
o caminho que dependia de uma chave de LLM: a CVM entrega os campos crus, e
índice de crédito é conta, não interpretação. O que o modelo fazia era extrair
número de PDF; quando o número já vem estruturado, não há o que extrair.

    ticker -> raiz (cadastro_b3) -> CNPJ -> balanço (CVM) -> índices -> veredito

Campo que não vier fica None e derruba o laudo para INCONCLUSIVO. Nada é
estimado: a versão antiga do motor preenchia ativo circulante com 40% do ativo
total quando faltava, e esse chute entrava no Z-Score como se fosse medido.

INSTITUIÇÃO FINANCEIRA NÃO ENTRA. Alavancagem sobre EBITDA e cobertura de
juros não descrevem banco — o passivo dele é o negócio, não a dívida. Para
esses o veredito correto é remeter aos índices prudenciais, e é o que a função
faz em vez de emitir um número que pareceria análise.
"""

from modules import credit_engine, fundamentos_cvm, identidade

SETORES_FINANCEIROS = ("Financeiro",)

# O motor devolve `status` em prosa ("REPROVADO / VETO"). A tela e o roteador
# trabalham com um `veredito` de uma palavra; manter os dois evita que uma
# comparação de string em outro arquivo decida errado por causa do sufixo.
VEREDITO_POR_STATUS = {
    "REPROVADO / VETO": "REPROVADO",
    "INCONCLUSIVO / DADO INSUFICIENTE": "INCONCLUSIVO",
    "APROVADO / ALTA CONFIANÇA": "APROVADO",
}


def veredito_do_status(status):
    return VEREDITO_POR_STATUS.get(status, "INCONCLUSIVO")


def _num(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None


def _divida_liquida(balanco):
    """Dívida onerosa menos caixa. Sem as duas pontas, não há dívida líquida."""
    curto = _num(balanco.get("divida_curto_prazo"))
    longo = _num(balanco.get("divida_longo_prazo"))
    if curto is None and longo is None:
        return None
    bruta = (curto or 0.0) + (longo or 0.0)
    caixa = _num(balanco.get("caixa"))
    if caixa is None:
        return None
    return bruta - caixa


def _despesa_financeira(balanco):
    """Positiva, como o motor espera.

    A DFP publica despesa financeira com sinal negativo em 3.06.02. Quando a
    conta específica não vier, o resultado financeiro líquido só serve se for
    negativo — resultado financeiro positivo significa que a empresa ganhou
    dinheiro no financeiro, e usar isso como "despesa" inverteria a cobertura.
    """
    especifica = _num(balanco.get("despesa_financeira"))
    if especifica is not None and especifica != 0:
        return abs(especifica)
    liquido = _num(balanco.get("resultado_financeiro"))
    if liquido is not None and liquido < 0:
        return abs(liquido)
    return None


def _proxy_ebitda(balanco):
    """EBIT como proxy de EBITDA.

    A DFP não publica depreciação numa conta padronizada do BPA/DRE, então não
    dá para reconstruir EBITDA sem ir à DVA ou às notas explicativas. EBIT é
    menor que EBITDA, logo a alavancagem sai CONSERVADORA — erra para o lado de
    parecer mais endividado, nunca menos. A tela diz que é EBIT.
    """
    return _num(balanco.get("ebit"))


def laudo_por_ticker(ticker, banco=None, caminho_cadastro=None):
    """Laudo completo. Sempre devolve dicionário; nunca levanta."""
    codigo = (ticker or "").upper().strip()
    if not codigo:
        return {"erro": "Informe um ticker."}

    marca = identidade.identidade(codigo)
    base = {
        "ticker_analisado": codigo,
        "identidade": marca,
        "origem_dados": "Balanço publicado na CVM (DFP)",
    }

    if marca["setor"] in SETORES_FINANCEIROS:
        return {
            **base,
            "veredito": "NÃO APLICÁVEL",
            "classificacao": "Instituição financeira",
            "parecer": (
                "Alavancagem sobre EBITDA e cobertura de juros não descrevem "
                "instituição financeira — o passivo dela é o negócio, não a "
                "dívida. Avalie por índices prudenciais (Basileia, "
                "imobilização, inadimplência), não por este motor."),
            "indices": {},
            "campos_brutos": {},
        }

    dados = fundamentos_cvm.multiplos_do_ticker(codigo, banco=banco,
                                                caminho_cadastro=caminho_cadastro)
    cnpj = dados.get("cnpj")
    if not cnpj:
        return {**base, "veredito": "INCONCLUSIVO",
                "parecer": f"{codigo} não está no cadastro de companhias do B3.",
                "indices": {}, "campos_brutos": {}}

    balanco = fundamentos_cvm.balanco_por_cnpj(cnpj, banco)
    if not balanco:
        return {**base, "cnpj": cnpj, "veredito": "INCONCLUSIVO",
                "parecer": "Sem demonstração financeira desta companhia na base da CVM.",
                "indices": {}, "campos_brutos": {}}

    divida_liquida = _divida_liquida(balanco)
    ebitda = _proxy_ebitda(balanco)
    despesa = _despesa_financeira(balanco)

    # Chaves exatamente como `calcular_altman_z_score_emergente` as espera.
    circulante = _num(balanco.get("passivo_circulante"))
    nao_circulante = _num(balanco.get("passivo_nao_circulante"))
    passivo_total = (None if circulante is None and nao_circulante is None
                     else (circulante or 0.0) + (nao_circulante or 0.0))

    z_metrics = {
        "ativo_total": _num(balanco.get("ativo_total")),
        "ativo_circulante": _num(balanco.get("ativo_circulante")),
        "passivo_circulante": circulante,
        "passivo_total": passivo_total,
        "patrimonio_liquido": _num(balanco.get("patrimonio_liquido")),
        "lucros_retidos": _num(balanco.get("lucros_acumulados")),
        "ebitda": ebitda,
    }

    resultado = credit_engine.auditar_credito_corporativo(
        nome_emissor=balanco.get("denom_cia") or codigo,
        divida_liquida=divida_liquida,
        ebitda=ebitda,
        despesa_financeira_anual=despesa,
        z_metrics=z_metrics,
    )

    faltando = [nome for nome, valor in
                (("dívida líquida", divida_liquida), ("EBIT", ebitda),
                 ("despesa financeira", despesa)) if valor is None]

    return {
        **resultado,
        **base,
        "veredito": veredito_do_status(resultado.get("status")),
        "cnpj": cnpj,
        "exercicio": balanco.get("ano"),
        "proxy_ebitda": "EBIT (a DFP não padroniza depreciação; alavancagem sai conservadora)",
        "campos_ausentes": faltando,
        "campos_brutos": {
            "divida_curto_prazo": _num(balanco.get("divida_curto_prazo")),
            "divida_longo_prazo": _num(balanco.get("divida_longo_prazo")),
            "caixa": _num(balanco.get("caixa")),
            "divida_liquida": divida_liquida,
            "ebit": ebitda,
            "despesa_financeira": despesa,
            **z_metrics,
        },
    }


def base_disponivel(banco=None):
    return fundamentos_cvm.base_disponivel(banco)
