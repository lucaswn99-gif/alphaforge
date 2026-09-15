"""Renda fixa privada: CDB, LCI, LCA, CRI, CRA, debênture, LF, LCD.

**Não tem ticker, não tem cotação — e por isso não cabe em `carteiras`.** Ação
e FII têm preço de pregão; um CDB de balcão não é negociado todo dia, e para
o cliente típico de assessor é carregado até o vencimento. "Valor atual" aqui
não é preço de mercado — é **marcação na curva**: o valor aplicado, composto
pelo indexador contratado (pré-fixado, IPCA+, % do CDI, CDI+) desde a data de
aplicação até hoje. É uma conta, não uma cotação, e a tela precisa dizer isso
— do contrário parece existir um mercado líquido que não existe.

**Quando falta a série do indexador, o valor mostrado é o aplicado, nunca um
número calculado com dado que não veio.** CDI e IPCA vêm do BCB/SGS
(`modules/taxas.py`), que não tem fallback sintético para série histórica —
inventar um dia de CDI seria inventar rentabilidade. `apurado=False` é o sinal
de que a correção ainda não pôde ser calculada; o valor aplicado não é zero
disfarçado, é o piso conhecido enquanto o resto não é apurado.

**Dias úteis são aproximados por exclusão de fim de semana.** O projeto não
tem calendário de feriados nacionais, e adicionar um agora seria inventar
precisão que os outros módulos (estresse, radar) também não têm. O erro típico
é de poucos dias por ano — pequeno frente à taxa contratada — e fica
documentado aqui, não escondido atrás de um número que parece exato.
"""

from datetime import date, datetime, timedelta, timezone

from modules import contas, taxas

CDB = "cdb"
LCI = "lci"
LCA = "lca"
CRI = "cri"
CRA = "cra"
DEBENTURE = "debenture"
LF = "lf"
LCD = "lcd"

TIPOS = (CDB, LCI, LCA, CRI, CRA, DEBENTURE, LF, LCD)

ROTULOS_TIPO = {
    CDB: "CDB",
    LCI: "LCI",
    LCA: "LCA",
    CRI: "CRI",
    CRA: "CRA",
    DEBENTURE: "Debênture",
    LF: "LF",
    LCD: "LCD",
}

PRE = "pre"
IPCA_MAIS = "ipca_mais"
PCT_CDI = "pct_cdi"
CDI_MAIS = "cdi_mais"

INDEXADORES = (PRE, IPCA_MAIS, PCT_CDI, CDI_MAIS)

ROTULOS_INDEXADOR = {
    PRE: "Pré-fixado",
    IPCA_MAIS: "IPCA+",
    PCT_CDI: "% do CDI",
    CDI_MAIS: "CDI+",
}

# Guarda-corpo, mesmo espírito do limite de posições em ação: carteira com
# mais linhas que isso é script em laço, não cliente de assessor.
MAXIMO_POSICOES = 200
VALOR_MAXIMO = 100_000_000.0
# Acima disso é dedo errado no teclado (ninguém contrata CDB a 100% a.a.).
TAXA_MAXIMA = 100.0
DU_ANO = 252


class ErroRendaFixa(ValueError):
    """Entrada recusada, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _hoje():
    return date.today()


def validar_tipo(bruto):
    tipo = str(bruto or "").strip().lower()
    if tipo not in TIPOS:
        raise ErroRendaFixa(
            f"Tipo desconhecido: '{bruto}'. Use {', '.join(TIPOS)}.")
    return tipo


def validar_indexador(bruto):
    indexador = str(bruto or "").strip().lower()
    if indexador not in INDEXADORES:
        raise ErroRendaFixa(
            f"Indexador desconhecido: '{bruto}'. Use {', '.join(INDEXADORES)}.")
    return indexador


def _converter_data(bruto, campo):
    if isinstance(bruto, date):
        return bruto
    try:
        return datetime.strptime(str(bruto).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        raise ErroRendaFixa(f"{campo} inválida: '{bruto}'. Use AAAA-MM-DD.")


def validar(emissor, tipo, indexador, taxa, data_aplicacao, data_vencimento,
           valor_aplicado):
    emissor = str(emissor or "").strip()
    if not emissor:
        raise ErroRendaFixa("Informe o emissor.")
    if len(emissor) > 120:
        raise ErroRendaFixa("Nome do emissor muito longo (máximo 120 caracteres).")

    tipo = validar_tipo(tipo)
    indexador = validar_indexador(indexador)

    try:
        taxa = float(taxa)
    except (TypeError, ValueError):
        raise ErroRendaFixa("Taxa precisa ser um número.")
    if not (0 <= taxa <= TAXA_MAXIMA):
        raise ErroRendaFixa(
            f"Taxa fora da faixa plausível (0 a {TAXA_MAXIMA:.0f}% a.a.).")

    aplicacao = _converter_data(data_aplicacao, "Data de aplicação")
    if aplicacao > _hoje():
        raise ErroRendaFixa("Data de aplicação não pode ser no futuro.")

    vencimento = (_converter_data(data_vencimento, "Data de vencimento")
                 if data_vencimento else None)
    if vencimento and vencimento <= aplicacao:
        raise ErroRendaFixa("Vencimento precisa ser depois da aplicação.")

    try:
        valor_aplicado = float(valor_aplicado)
    except (TypeError, ValueError):
        raise ErroRendaFixa("Valor aplicado precisa ser um número.")
    if not (0 < valor_aplicado <= VALOR_MAXIMO):
        raise ErroRendaFixa("Valor aplicado fora da faixa aceita.")

    return emissor, tipo, indexador, taxa, aplicacao, vencimento, valor_aplicado


# --------------------------------------------------------------------------
# Marcação na curva
# --------------------------------------------------------------------------

def _dias_uteis_aprox(inicio, fim):
    """Dias úteis entre duas datas, por exclusão de fim de semana. Ver
    docstring do módulo sobre a aproximação (sem calendário de feriados)."""
    if fim <= inicio:
        return 0
    dias = 0
    cursor = inicio
    while cursor < fim:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:
            dias += 1
    return dias


def _fator_prefixado(taxa_ano_pct, dias_uteis):
    return (1 + taxa_ano_pct / 100.0) ** (dias_uteis / DU_ANO)


def _fim_do_mes(data_sgs):
    """Último dia do mês de uma data 'dd/mm/aaaa' do SGS."""
    dia, mes, ano = (int(parte) for parte in data_sgs.split("/"))
    if mes == 12:
        proximo_mes = date(ano + 1, 1, 1)
    else:
        proximo_mes = date(ano, mes + 1, 1)
    return proximo_mes - timedelta(days=1)


def marcar_na_curva(posicao, hoje=None, buscar_cdi=None, buscar_ipca=None):
    """(valor_atual, resumo, apurado, detalhes).

    `posicao` precisa de data_aplicacao (date), valor_aplicado (float),
    indexador e taxa. `apurado=False` só quando falta a série do indexador —
    nesse caso `valor_atual == valor_aplicado`, nunca um número inventado.
    """
    hoje = hoje or _hoje()
    buscar_cdi = buscar_cdi or taxas.serie_cdi
    buscar_ipca = buscar_ipca or taxas.serie_ipca

    aplicacao = posicao["data_aplicacao"]
    valor_aplicado = posicao["valor_aplicado"]
    indexador = posicao["indexador"]
    taxa = posicao["taxa"]

    if hoje <= aplicacao:
        return (round(valor_aplicado, 2),
                "Aplicado hoje — ainda sem rentabilidade a apurar.", True, {})

    if indexador == PRE:
        du = _dias_uteis_aprox(aplicacao, hoje)
        fator = _fator_prefixado(taxa, du)
        valor_atual = valor_aplicado * fator
        resumo = (f"Pré-fixado a {taxa:.2f}% a.a., {du} dias úteis "
                 f"(aproximados) desde a aplicação.")
        return round(valor_atual, 2), resumo, True, {"dias_uteis": du,
                                                       "fator": round(fator, 6)}

    if indexador in (PCT_CDI, CDI_MAIS):
        serie = buscar_cdi(aplicacao, hoje)
        if not serie:
            return (round(valor_aplicado, 2),
                    "Série do CDI indisponível no momento — valor mostrado é "
                    "o aplicado, correção ainda não apurada.", False, {})
        fator_cdi = 1.0
        for ponto in serie:
            fator_cdi *= (1 + ponto["valor"] / 100.0)
        du = len(serie)
        cdi_acumulado_pct = (fator_cdi - 1) * 100.0
        if indexador == PCT_CDI:
            fator = fator_cdi ** (taxa / 100.0)
            resumo = (f"{taxa:.1f}% do CDI acumulado em {du} dias úteis "
                     f"(CDI do período: {cdi_acumulado_pct:.2f}%).")
        else:
            fator = fator_cdi * _fator_prefixado(taxa, du)
            resumo = (f"CDI + {taxa:.2f}% a.a. em {du} dias úteis "
                     f"(CDI do período: {cdi_acumulado_pct:.2f}%).")
        valor_atual = valor_aplicado * fator
        return round(valor_atual, 2), resumo, True, {"dias_uteis": du,
                                                       "fator": round(fator, 6)}

    if indexador == IPCA_MAIS:
        serie = buscar_ipca(aplicacao, hoje)
        if not serie:
            return (round(valor_aplicado, 2),
                    "Série do IPCA indisponível no momento — valor mostrado "
                    "é o aplicado, correção ainda não apurada.", False, {})
        fator_ipca = 1.0
        for ponto in serie:
            fator_ipca *= (1 + ponto["valor"] / 100.0)
        ipca_acumulado_pct = (fator_ipca - 1) * 100.0
        fim_serie = _fim_do_mes(serie[-1]["data"])
        du_resto = _dias_uteis_aprox(fim_serie, hoje) if hoje > fim_serie else 0
        fator_taxa = _fator_prefixado(taxa, du_resto)
        fator = fator_ipca * fator_taxa
        valor_atual = valor_aplicado * fator
        resumo = (f"IPCA+{taxa:.2f}% a.a. — IPCA acumulado até "
                 f"{serie[-1]['data']} ({ipca_acumulado_pct:.2f}%), taxa "
                 f"pré-fixada aplicada aos {du_resto} dias úteis seguintes.")
        return round(valor_atual, 2), resumo, True, {"fator": round(fator, 6)}

    return round(valor_aplicado, 2), "Indexador desconhecido.", False, {}


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def _linha(registro, hoje=None, buscar_cdi=None, buscar_ipca=None):
    bruto = dict(registro)
    aplicacao = _converter_data(bruto["data_aplicacao"], "data_aplicacao")
    vencimento = (_converter_data(bruto["data_vencimento"], "data_vencimento")
                 if bruto.get("data_vencimento") else None)
    valor_aplicado = float(bruto["valor_aplicado"])
    taxa = float(bruto["taxa"])

    posicao = {"data_aplicacao": aplicacao, "valor_aplicado": valor_aplicado,
              "indexador": bruto["indexador"], "taxa": taxa}
    valor_atual, resumo, apurado, detalhes = marcar_na_curva(
        posicao, hoje=hoje, buscar_cdi=buscar_cdi, buscar_ipca=buscar_ipca)

    rentabilidade_pct = (round((valor_atual / valor_aplicado - 1) * 100.0, 2)
                         if valor_aplicado else None)
    hoje = hoje or _hoje()
    dias_para_vencer = (vencimento - hoje).days if vencimento else None

    return {
        "id": bruto["id"],
        "emissor": bruto["emissor"],
        "tipo": bruto["tipo"],
        "rotulo_tipo": ROTULOS_TIPO.get(bruto["tipo"], bruto["tipo"]),
        "indexador": bruto["indexador"],
        "rotulo_indexador": ROTULOS_INDEXADOR.get(bruto["indexador"], bruto["indexador"]),
        "taxa": taxa,
        "data_aplicacao": aplicacao.isoformat(),
        "data_vencimento": vencimento.isoformat() if vencimento else None,
        "dias_para_vencer": dias_para_vencer,
        "valor_aplicado": round(valor_aplicado, 2),
        "valor_atual": valor_atual,
        "rentabilidade_pct": rentabilidade_pct,
        "apurado": apurado,
        "marcacao": resumo,
        "detalhes": detalhes,
        "atualizado_em": bruto.get("atualizado_em"),
    }


def listar(usuario_id, hoje=None):
    """Posições marcadas na curva, valor aplicado x valor atual, e peso por
    posição sobre o valor atual (não sobre o aplicado — é o critério que
    reflete a rentabilidade acumulada de cada uma)."""
    contas.iniciar()
    with contas._conectar() as cx:
        linhas = cx.execute(
            "SELECT * FROM renda_fixa WHERE usuario_id = ? ORDER BY emissor",
            (usuario_id,)).fetchall()

    posicoes = [_linha(l, hoje=hoje) for l in linhas]
    valor_aplicado_total = sum(p["valor_aplicado"] for p in posicoes)
    valor_atual_total = sum(p["valor_atual"] for p in posicoes)
    for p in posicoes:
        p["peso_pct"] = (round(p["valor_atual"] / valor_atual_total * 100.0, 2)
                         if valor_atual_total else None)

    return {
        "posicoes": posicoes,
        "valor_aplicado_total": round(valor_aplicado_total, 2),
        "valor_atual_total": round(valor_atual_total, 2),
        "posicoes_total": len(posicoes),
        "nao_apurados": sum(1 for p in posicoes if not p["apurado"]),
    }


def adicionar(usuario_id, emissor, tipo, indexador, taxa, data_aplicacao,
             data_vencimento, valor_aplicado):
    emissor, tipo, indexador, taxa, aplicacao, vencimento, valor_aplicado = validar(
        emissor, tipo, indexador, taxa, data_aplicacao, data_vencimento,
        valor_aplicado)

    contas.iniciar()
    agora = _agora()
    with contas._conectar() as cx:
        quantas = cx.execute(
            "SELECT COUNT(*) FROM renda_fixa WHERE usuario_id = ?",
            (usuario_id,)).fetchone()[0]
        if quantas >= MAXIMO_POSICOES:
            raise ErroRendaFixa(
                f"Limite de {MAXIMO_POSICOES} posições de renda fixa atingido.")

        cursor = cx.execute(
            "INSERT INTO renda_fixa (usuario_id, emissor, tipo, indexador, "
            "taxa, data_aplicacao, data_vencimento, valor_aplicado, "
            "criado_em, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (usuario_id, emissor, tipo, indexador, taxa, aplicacao.isoformat(),
             vencimento.isoformat() if vencimento else None, valor_aplicado,
             agora, agora))
        linha = cx.execute("SELECT * FROM renda_fixa WHERE id = ?",
                           (cursor.lastrowid,)).fetchone()

    return _linha(linha)


def atualizar(usuario_id, posicao_id, emissor, tipo, indexador, taxa,
             data_aplicacao, data_vencimento, valor_aplicado):
    """Corrige a posição (substitui os campos). Devolve a linha ou None."""
    emissor, tipo, indexador, taxa, aplicacao, vencimento, valor_aplicado = validar(
        emissor, tipo, indexador, taxa, data_aplicacao, data_vencimento,
        valor_aplicado)

    contas.iniciar()
    with contas._conectar() as cx:
        alterou = cx.execute(
            "UPDATE renda_fixa SET emissor=?, tipo=?, indexador=?, taxa=?, "
            "data_aplicacao=?, data_vencimento=?, valor_aplicado=?, "
            "atualizado_em=? WHERE id = ? AND usuario_id = ?",
            (emissor, tipo, indexador, taxa, aplicacao.isoformat(),
             vencimento.isoformat() if vencimento else None, valor_aplicado,
             _agora(), posicao_id, usuario_id)).rowcount
        if not alterou:
            return None
        linha = cx.execute("SELECT * FROM renda_fixa WHERE id = ?",
                           (posicao_id,)).fetchone()

    return _linha(linha)


def remover(usuario_id, posicao_id):
    """True se removeu. `usuario_id` no WHERE: sem ele, saber o id de outra
    pessoa bastaria para apagar a posição dela."""
    contas.iniciar()
    with contas._conectar() as cx:
        apagou = cx.execute(
            "DELETE FROM renda_fixa WHERE id = ? AND usuario_id = ?",
            (posicao_id, usuario_id)).rowcount
    return apagou > 0
