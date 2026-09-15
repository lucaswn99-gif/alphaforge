"""Relatório em PDF: composição, diagnóstico, backtest, estresse e projeção
num único documento, com a marca do escritório do assessor e a do
AlphaForge.

**Duas marcas.** Espaço reservado para o logo do escritório no cabeçalho da
primeira página — por ora sem logo (o assessor ainda não definiu como vai
enviá-lo; ver `_cabecalho_rodape`), moldura tracejada em vez de espaço em
branco sem explicação, para não parecer um erro de layout. "AlphaForge" no
rodapé de toda página e por extenso na última, ao lado do aviso legal padrão
do produto (`modules/legal.py` — fonte única; este módulo NÃO duplica o
texto, só o importa).

**Cada seção é opcional e diz por que está ausente — nunca finge existir.**
Uma carteira sem ação não tem diagnóstico nem backtest de ação; uma projeção
sem premissa informada não aparece com taxa inventada. É o mesmo princípio
de "não apurado nunca vira zero" que rege o resto do projeto, generalizado
ao formato PDF: seção faltando é uma frase, nunca um vazio silencioso nem um
placeholder que finja ser dado real.

**"Conciso e descritivo" — sem parágrafo de preenchimento.** Cada número
vem, ao lado dele, com a frase que diz o que ele significa. Este módulo é
puro: recebe um dicionário já montado pela rota (que consulta banco e rede)
e devolve bytes de PDF — nada aqui lê banco, chama API externa ou depende de
estado global, o que faz dar para testar com um dicionário sintético.
"""

import io
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from reportlab.graphics.shapes import Drawing, Line, PolyLine, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from modules import legal

COR_MARCA = colors.HexColor("#0a5c3a")
COR_TEXTO = colors.HexColor("#1a221d")
COR_SUAVE = colors.HexColor("#5b6b63")
COR_QUEDA = colors.HexColor("#a8202b")
COR_ALERTA = colors.HexColor("#9c5b00")
COR_LINHA = colors.HexColor("#d8ded9")
COR_FUNDO_ALT = colors.HexColor("#f2f5f3")


def _agora_formatado():
    return datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")


def _esc(valor):
    return escape(str(valor if valor is not None else "—"))


def _brl(valor):
    """`1234.5` -> `1.234,50` — sem depender de locale do sistema, que muda
    de máquina para máquina e não é algo que o processo deva configurar
    globalmente só para formatar um número."""
    if valor is None:
        valor = 0.0
    texto = f"{valor:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _pct(valor, casas=2):
    if valor is None:
        return "—"
    sinal = "+" if valor > 0 else ""
    return f"{sinal}{valor:.{casas}f}%"


# --------------------------------------------------------------------------
# Estilos e tabela
# --------------------------------------------------------------------------

def _estilos():
    return {
        "titulo": ParagraphStyle(
            "titulo", fontName="Helvetica-Bold", fontSize=13, leading=16,
            textColor=COR_MARCA, spaceBefore=14, spaceAfter=6),
        "corpo": ParagraphStyle(
            "corpo", fontName="Helvetica", fontSize=9.5, leading=13.5,
            textColor=COR_TEXTO, spaceAfter=6),
        "legenda": ParagraphStyle(
            "legenda", fontName="Helvetica-Oblique", fontSize=8.5, leading=12,
            textColor=COR_SUAVE, spaceAfter=8),
        "aviso": ParagraphStyle(
            "aviso", fontName="Helvetica-Bold", fontSize=9, leading=13,
            textColor=COR_ALERTA, spaceBefore=4, spaceAfter=4),
        "rotulo_tabela": ParagraphStyle(
            "rotulo_tabela", fontName="Helvetica-Bold", fontSize=8,
            textColor=colors.white, leading=10),
        "celula": ParagraphStyle(
            "celula", fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=COR_TEXTO),
        "disclaimer_titulo": ParagraphStyle(
            "disclaimer_titulo", fontName="Helvetica-Bold", fontSize=11,
            textColor=COR_TEXTO, spaceBefore=18, spaceAfter=6),
        "disclaimer_corpo": ParagraphStyle(
            "disclaimer_corpo", fontName="Helvetica", fontSize=7.5, leading=11,
            textColor=COR_SUAVE, spaceAfter=5),
        "marca_final": ParagraphStyle(
            "marca_final", fontName="Helvetica-Bold", fontSize=12,
            textColor=COR_MARCA, spaceBefore=16, alignment=1),
    }


def _tabela(cabecalho, linhas, larguras, estilos, alinhamentos=None):
    """Tabela com cabeçalho verde-marca e linhas zebradas — o mesmo padrão
    visual em toda seção, para a tabela nunca ser o que muda de seção para
    seção, só o conteúdo dela."""
    cab = [Paragraph(_esc(c), estilos["rotulo_tabela"]) for c in cabecalho]
    dados = [cab]
    for linha in linhas:
        dados.append([Paragraph(str(c), estilos["celula"]) for c in linha])

    tabela = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_MARCA),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, COR_MARCA),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, COR_FUNDO_ALT]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, COR_LINHA),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]
    for indice, alinhamento in (alinhamentos or {}).items():
        estilo.append(("ALIGN", (indice, 0), (indice, -1), alinhamento))
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _caixa_aviso(texto, estilos):
    """Bloco com fundo, não nota de rodapé — o mesmo destaque que o
    console dá ao aviso "hipotético/ilustrativo" (ver templates/vip.html)."""
    tabela = Table([[Paragraph(_esc(texto), estilos["aviso"])]],
                   colWidths=[17 * cm])
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fdf1de")),
        ("BOX", (0, 0), (-1, -1), 0.6, COR_ALERTA),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    return tabela


def _grafico_linha(serie, campo_valor, largura=17 * cm, altura=3.2 * cm):
    """Linha desenhada à mão — mesmo espírito do SVG do console (sem
    biblioteca de gráfico): só um traço mostrando a forma da série, os
    números de verdade vêm no texto ao lado."""
    if len(serie) < 2:
        return None
    valores = [p[campo_valor] for p in serie]
    minimo, maximo = min(valores), max(valores)
    amplitude = (maximo - minimo) or 1
    margem = 0.3 * cm
    desenho = Drawing(largura, altura)
    passo_x = (largura - 2 * margem) / (len(serie) - 1)

    def ponto(indice):
        x = margem + indice * passo_x
        y = margem + ((serie[indice][campo_valor] - minimo) / amplitude) * (altura - 2 * margem)
        return x, y

    pontos = []
    for indice in range(len(serie)):
        x, y = ponto(indice)
        pontos.extend([x, y])
    desenho.add(PolyLine(pontos, strokeColor=COR_MARCA, strokeWidth=1.4))
    return desenho


# --------------------------------------------------------------------------
# Seções
# --------------------------------------------------------------------------

def _secao_composicao(composicao, estilos):
    historia = [Paragraph("Composição da carteira", estilos["titulo"])]
    total = composicao.get("patrimonio_total") or 0.0
    historia.append(Paragraph(
        f"Patrimônio total de R$ {_brl(total)} — ação/FII/ETF pelo custo, "
        "renda fixa e fundos pelo valor atual (marcação na curva e valor "
        "aplicado, respectivamente).", estilos["corpo"]))

    acoes = composicao.get("acoes") or []
    if acoes:
        custo_acoes = composicao.get("custo_total_acoes") or 0.0
        peso_acoes = round(custo_acoes / total * 100.0, 1) if total else 0.0
        historia.append(Paragraph(
            f"Ação, FII e ETF: R$ {_brl(custo_acoes)} ({peso_acoes:.1f}% do "
            "patrimônio), pelo custo de aquisição.", estilos["corpo"]))
        linhas = [[_esc(p["ticker"]), str(p["quantidade"]), f"R$ {_brl(p['preco_medio'])}",
                  f"R$ {_brl(p['custo_total'])}",
                  f"{p['peso_pct']:.1f}%" if p.get("peso_pct") is not None else "—"]
                 for p in acoes]
        historia.append(_tabela(
            ["Papel", "Quantidade", "Preço médio", "Custo total", "Peso"],
            linhas, [3 * cm, 3 * cm, 3.5 * cm, 3.7 * cm, 2.3 * cm], estilos,
            {1: "RIGHT", 2: "RIGHT", 3: "RIGHT", 4: "RIGHT"}))
        historia.append(Spacer(1, 8))

    renda_fixa = composicao.get("renda_fixa") or []
    if renda_fixa:
        valor_rf = composicao.get("valor_atual_renda_fixa") or 0.0
        peso_rf = round(valor_rf / total * 100.0, 1) if total else 0.0
        nao_apurados = sum(1 for p in renda_fixa if not p.get("apurado"))
        frase = (f"Renda fixa: R$ {_brl(valor_rf)} ({peso_rf:.1f}% do "
                "patrimônio), marcado na curva pelo indexador contratado.")
        if nao_apurados:
            frase += (f" {nao_apurados} posição(ões) ainda sem correção "
                     "apurada — valor mostrado é o aplicado.")
        historia.append(Paragraph(frase, estilos["corpo"]))
        linhas = [[_esc(p["emissor"]), _esc(p["rotulo_tipo"]), _esc(p["rotulo_indexador"]),
                  f"R$ {_brl(p['valor_aplicado'])}",
                  f"R$ {_brl(p['valor_atual'])}" + ("" if p.get("apurado") else " *")]
                 for p in renda_fixa]
        historia.append(_tabela(
            ["Emissor", "Tipo", "Indexador", "Aplicado", "Atual (curva)"],
            linhas, [4 * cm, 2.3 * cm, 3.7 * cm, 3.2 * cm, 3.3 * cm], estilos,
            {3: "RIGHT", 4: "RIGHT"}))
        if nao_apurados:
            historia.append(Paragraph(
                "* correção ainda não apurada (série do indexador indisponível).",
                estilos["legenda"]))
        historia.append(Spacer(1, 8))

    fundos = composicao.get("fundos") or []
    if fundos:
        valor_fundos = composicao.get("valor_atual_fundos") or 0.0
        peso_fundos = round(valor_fundos / total * 100.0, 1) if total else 0.0
        historia.append(Paragraph(
            f"Fundos de investimento: R$ {_brl(valor_fundos)} ({peso_fundos:.1f}% "
            "do patrimônio) — cota diária ainda não integrada, valor mostrado "
            "é o aplicado (cotas × valor da cota na aplicação).",
            estilos["corpo"]))
        linhas = [[_esc(p["nome_fundo"]), _esc(p["rotulo_classe"]),
                  f"{p['numero_cotas']:g}", f"R$ {_brl(p['valor_aplicado'])}"]
                 for p in fundos]
        historia.append(_tabela(
            ["Fundo", "Classe", "Cotas", "Aplicado"],
            linhas, [6.5 * cm, 3.5 * cm, 3 * cm, 3.5 * cm], estilos,
            {2: "RIGHT", 3: "RIGHT"}))
        historia.append(Spacer(1, 8))

    if not acoes and not renda_fixa and not fundos:
        historia.append(Paragraph("Nenhuma posição cadastrada.", estilos["corpo"]))

    return historia


def _secao_diagnostico(filosofia, diagnostico, estilos):
    historia = [Paragraph("Diagnóstico das ações", estilos["titulo"])]
    if not diagnostico or not diagnostico.get("posicoes"):
        historia.append(Paragraph(
            "Diagnóstico por filosofia se aplica a ação/FII/ETF — nenhuma "
            "posição dessa classe cadastrada.", estilos["corpo"]))
        return historia

    rotulo_filosofia = (filosofia or {}).get("rotulo")
    if rotulo_filosofia:
        historia.append(Paragraph(
            f"Filosofia aplicada: {_esc(rotulo_filosofia)}. Cada papel abaixo "
            "é medido contra essa régua, com exceção de quem tem filosofia "
            "própria definida.", estilos["corpo"]))
    else:
        historia.append(Paragraph(
            "Nenhuma filosofia em uso — dado bruto de cada papel, sem "
            "veredito de aprovado ou reprovado.", estilos["corpo"]))

    linhas = [[_esc(p["ticker"]), _esc(p["diagnostico"]["rotulo"]),
              _esc(p["diagnostico"].get("resumo") or "—")]
             for p in diagnostico["posicoes"]]
    historia.append(_tabela(
        ["Papel", "Estado", "Resumo"], linhas,
        [2.5 * cm, 3.5 * cm, 10.5 * cm], estilos))

    resumo = diagnostico.get("resumo") or {}
    rotulos_estado = diagnostico.get("rotulos") or {}
    partes = [f"{qtd} {rotulos_estado.get(estado, estado).lower()}"
             for estado, qtd in resumo.items() if qtd]
    if partes:
        historia.append(Paragraph(
            "Resumo: " + ", ".join(partes) + ".", estilos["legenda"]))
    return historia


def _secao_backtest(backtest, estilos):
    historia = [Paragraph("Backtest de 12 meses", estilos["titulo"])]
    if not backtest or not backtest.get("serie"):
        motivo = (backtest or {}).get("motivo") or (
            "Sem posições com histórico suficiente para simular os últimos "
            "12 meses.")
        historia.append(Paragraph(motivo, estilos["corpo"]))
        return historia

    historia.append(Paragraph(
        f"Retorno de {_pct(backtest.get('retorno_periodo_pct'))} nos últimos "
        f"{backtest.get('meses', 12)} meses, com {backtest.get('cobertura_pct', 0):.1f}% "
        "do patrimônio coberto pela simulação.", estilos["corpo"]))

    desenho = _grafico_linha(backtest["serie"], "indice")
    if desenho:
        historia.append(desenho)
        historia.append(Spacer(1, 4))

    sem_historico = backtest.get("sem_historico") or []
    if sem_historico:
        nomes = ", ".join(_esc(s["identificador"]) for s in sem_historico)
        historia.append(Paragraph(
            f"Fora da simulação, sem histórico para os {backtest.get('meses', 12)} "
            f"meses inteiros: {nomes}.", estilos["legenda"]))

    historia.append(Paragraph(
        "Simulação, não cotação: ação/FII/ETF pelo preço histórico, renda "
        "fixa pelo replay da fórmula do indexador contratado. Rentabilidade "
        "passada não garante rentabilidade futura.", estilos["legenda"]))
    return historia


def _secao_estresse(estresse, estilos):
    historia = [Paragraph("Estresse macro — eventos de cauda", estilos["titulo"])]
    eventos = (estresse or {}).get("eventos") or []
    if not eventos:
        motivo = (estresse or {}).get("motivo") or (
            "Nenhum cenário selecionado, ou nenhuma posição em ação para medir "
            "— o estresse mede só ação, que tem dívida de companhia no "
            "balanço; FII e ETF não.")
        historia.append(Paragraph(motivo, estilos["corpo"]))
        return historia

    historia.append(Paragraph(
        "Contrafactual: aplica os pesos de HOJE a preços do passado. Mede a "
        "fragilidade da composição atual, não uma previsão.", estilos["corpo"]))

    linhas = []
    for evento in eventos:
        queda = evento.get("drawdown_pct")
        cobertura = evento.get("cobertura_pct")
        pior = evento.get("pior_papel")
        pior_texto = (f"{_esc(pior['ticker'])} ({pior['drawdown_pct']:.1f}%)"
                     if pior else "—")
        linhas.append([
            _esc(evento["nome"]),
            _pct(queda, 1) if queda is not None else "—",
            f"{cobertura:.0f}%" if cobertura is not None else "—",
            pior_texto,
        ])
    historia.append(_tabela(
        ["Evento", "Queda simulada", "Cobertura", "Pior papel"],
        linhas, [6 * cm, 3.5 * cm, 3 * cm, 4 * cm], estilos,
        {1: "RIGHT", 2: "RIGHT"}))
    return historia


def _secao_projecao(projecao, estilos):
    historia = [Paragraph("Projeção de capital", estilos["titulo"])]
    if not projecao:
        historia.append(Paragraph(
            "Projeção não incluída neste relatório — nenhuma premissa (taxa, "
            "aporte, horizonte) foi informada antes de gerar.", estilos["corpo"]))
        return historia

    historia.append(Paragraph(
        f"Premissas informadas por você: taxa de {projecao['taxa_anual_pct']:.2f}% "
        f"a.a., aporte mensal de R$ {_brl(projecao['aporte_mensal'])}, horizonte "
        f"de {projecao['horizonte_anos']} anos.", estilos["corpo"]))
    historia.append(_caixa_aviso(
        "Projeção hipotética/ilustrativa, não uma promessa de resultado. "
        "Mude qualquer premissa acima e o resultado muda junto.", estilos))
    historia.append(Spacer(1, 6))

    historia.append(Paragraph(
        f"Ao final do horizonte: R$ {_brl(projecao['valor_final'])}, sendo "
        f"R$ {_brl(projecao['total_aportado'])} de aporte e R$ "
        f"{_brl(projecao['rendimento_total'])} de rendimento. Isso "
        f"sustentaria uma renda mensal de R$ "
        f"{_brl(projecao['renda_mensal_sustentavel_final'])} sem consumir o "
        "principal, na mesma taxa assumida.", estilos["corpo"]))

    desenho = _grafico_linha(
        [{"valor": p["valor"]} for p in projecao["serie"]], "valor")
    if desenho:
        historia.append(desenho)
        historia.append(Spacer(1, 4))

    for gap in projecao.get("gaps") or []:
        falta = gap["gap"] > 0
        texto = (f"{gap['rotulo']}: {'faltam' if falta else 'sobram'} R$ "
                f"{_brl(abs(gap['gap']))}" +
                ("/mês" if gap["tipo"] == "renda_mensal" else "") +
                f" frente à meta de R$ {_brl(gap['meta'])}"
                + ("/mês" if gap["tipo"] == "renda_mensal" else "") + ".")
        historia.append(Paragraph(texto, estilos["corpo"]))

    return historia


def _secao_disclaimer(estilos):
    historia = [PageBreak(),
               Paragraph(legal.AVISO_TITULO, estilos["disclaimer_titulo"])]
    for paragrafo in legal.AVISO_CVM:
        historia.append(Paragraph(_esc(paragrafo), estilos["disclaimer_corpo"]))
    historia.append(Paragraph("AlphaForge", estilos["marca_final"]))
    return historia


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------

def _cabecalho_rodape(canvas, doc, dados):
    canvas.saveState()
    largura, altura = A4
    pagina = canvas.getPageNumber()

    if pagina == 1:
        caixa_x, caixa_y = 2 * cm, altura - 3.3 * cm
        caixa_l, caixa_a = 3.6 * cm, 1.7 * cm
        canvas.setDash(2, 2)
        canvas.setStrokeColor(COR_SUAVE)
        canvas.rect(caixa_x, caixa_y, caixa_l, caixa_a)
        canvas.setDash()
        canvas.setFont("Helvetica", 6.5)
        canvas.setFillColor(COR_SUAVE)
        canvas.drawCentredString(caixa_x + caixa_l / 2, caixa_y + caixa_a / 2 - 3,
                                 "logo do escritório")

        canvas.setFillColor(COR_TEXTO)
        canvas.setFont("Helvetica-Bold", 16)
        canvas.drawString(6.2 * cm, altura - 2.15 * cm, "Relatório da carteira")
        canvas.setFont("Helvetica", 9.5)
        canvas.setFillColor(COR_SUAVE)
        canvas.drawString(6.2 * cm, altura - 2.75 * cm,
                          f"Cliente: {dados['cliente_email']}")
        canvas.drawString(6.2 * cm, altura - 3.2 * cm,
                          f"Gerado em {dados['gerado_em']}")

    canvas.setStrokeColor(COR_LINHA)
    canvas.setLineWidth(0.6)
    canvas.line(2 * cm, 1.7 * cm, largura - 2 * cm, 1.7 * cm)
    canvas.setFont("Helvetica-Bold", 7.5)
    canvas.setFillColor(COR_MARCA)
    canvas.drawString(2 * cm, 1.3 * cm, "AlphaForge")
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(COR_SUAVE)
    canvas.drawRightString(largura - 2 * cm, 1.3 * cm, f"Página {pagina}")
    canvas.restoreState()


def montar(dados):
    """`dados` já vem inteiramente montado pela rota — este módulo só
    desenha. Ver docstring do módulo para o contrato de cada chave."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=3.8 * cm, bottomMargin=2.2 * cm,
        title="Relatório da carteira — AlphaForge")

    estilos = _estilos()
    historia = []
    historia += _secao_composicao(dados["composicao"], estilos)
    historia += _secao_diagnostico(dados.get("filosofia"), dados.get("diagnostico"), estilos)
    historia += _secao_backtest(dados.get("backtest"), estilos)
    historia += _secao_estresse(dados.get("estresse"), estilos)
    historia += _secao_projecao(dados.get("projecao"), estilos)
    historia += _secao_disclaimer(estilos)

    def _pagina(canvas, doc_):
        _cabecalho_rodape(canvas, doc_, dados)

    doc.build(historia, onFirstPage=_pagina, onLaterPages=_pagina)
    return buffer.getvalue()
