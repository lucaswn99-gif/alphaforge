"""Fundos de investimento (não FII) — Etapa A (posição) + Etapa B (cota CVM).

**Etapa A: o fundo entra na carteira.** CNPJ, nome, classe ampla (ação/renda
fixa/multimercado/cambial/outros, taxonomia ANBIMA/CVM), número de cotas e
valor da cota na data de aplicação — não o valor aplicado diretamente, porque
é dessas duas partes que se calcula rentabilidade contra a cota atual.

**Etapa B: a cota atual vem do Informe Diário da CVM.** `modules/cota_cvm.py`
lê `cota_fundos_cvm.db`, escrito por `atualizar_cota_fundos_cvm.py` (roda
fora do serviço web — mesmo desenho de `atualizar_fundos_cvm.py` para FII).
Quando o CNPJ do fundo tem cota coletada, `apurado=True`: valor atual é
cotas × cota mais recente, e a rentabilidade é essa cota contra a cota da
aplicação que o assessor já digitou no cadastro — não é preciso (nem
possível, em geral) buscar a cota histórica exata da data de aplicação, ela
já está no cadastro.

**Sem cota coletada para aquele CNPJ, "valor atual" é o valor aplicado,
nunca uma rentabilidade inventada** — o mesmo princípio de `renda_fixa.py`.
Isso pode ser por fundo (typo no CNPJ, fundo fechado, ou simplesmente ainda
não coletado): `apurado=False` é por POSIÇÃO, não uma bandeira única para a
etapa inteira.
"""

from datetime import date, datetime, timezone

from modules import contas, cota_cvm

RENDA_FIXA = "renda_fixa"
ACOES = "acoes"
MULTIMERCADO = "multimercado"
CAMBIAL = "cambial"
OUTROS = "outros"

CLASSES = (RENDA_FIXA, ACOES, MULTIMERCADO, CAMBIAL, OUTROS)

ROTULOS_CLASSE = {
    RENDA_FIXA: "Renda fixa",
    ACOES: "Ações",
    MULTIMERCADO: "Multimercado",
    CAMBIAL: "Cambial",
    OUTROS: "Outros",
}

# Guarda-corpo, mesmo espírito do limite de posições em ação/renda fixa.
MAXIMO_POSICOES = 200
VALOR_COTA_MAXIMO = 1_000_000.0
NUMERO_COTAS_MAXIMO = 1_000_000_000.0

RESUMO_NAO_APURADO = (
    "Cota diária da CVM ainda não integrada a este fundo — valor mostrado é "
    "o aplicado (cotas × valor da cota na aplicação), correção ainda não "
    "apurada.")


class ErroFundo(ValueError):
    """Entrada recusada, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def _hoje():
    return date.today()


def validar_classe(bruto):
    classe = str(bruto or "").strip().lower()
    if classe not in CLASSES:
        raise ErroFundo(f"Classe desconhecida: '{bruto}'. Use {', '.join(CLASSES)}.")
    return classe


def validar_cnpj(bruto):
    """Só dígitos, exatamente 14 — sem cálculo de dígito verificador (o
    cadastro é do assessor, não uma porta pública; o risco é erro de
    digitação, não fraude, e o formato já pega o erro mais comum)."""
    digitos = "".join(c for c in str(bruto or "") if c.isdigit())
    if len(digitos) != 14:
        raise ErroFundo("CNPJ precisa ter 14 dígitos.")
    return digitos


def formatar_cnpj(digitos):
    if not digitos or len(digitos) != 14:
        return digitos
    return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"


def _converter_data(bruto, campo):
    if isinstance(bruto, date):
        return bruto
    try:
        return datetime.strptime(str(bruto).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError, AttributeError):
        raise ErroFundo(f"{campo} inválida: '{bruto}'. Use AAAA-MM-DD.")


def validar(nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao,
           data_aplicacao):
    nome_fundo = str(nome_fundo or "").strip()
    if not nome_fundo:
        raise ErroFundo("Informe o nome do fundo.")
    if len(nome_fundo) > 150:
        raise ErroFundo("Nome do fundo muito longo (máximo 150 caracteres).")

    cnpj = validar_cnpj(cnpj)
    classe = validar_classe(classe)

    try:
        numero_cotas = float(numero_cotas)
    except (TypeError, ValueError):
        raise ErroFundo("Número de cotas precisa ser um número.")
    if not (0 < numero_cotas <= NUMERO_COTAS_MAXIMO):
        raise ErroFundo("Número de cotas fora da faixa aceita.")

    try:
        valor_cota_aplicacao = float(valor_cota_aplicacao)
    except (TypeError, ValueError):
        raise ErroFundo("Valor da cota precisa ser um número.")
    if not (0 < valor_cota_aplicacao <= VALOR_COTA_MAXIMO):
        raise ErroFundo("Valor da cota fora da faixa aceita.")

    aplicacao = _converter_data(data_aplicacao, "Data de aplicação")
    if aplicacao > _hoje():
        raise ErroFundo("Data de aplicação não pode ser no futuro.")

    return nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao, aplicacao


# --------------------------------------------------------------------------
# CRUD
# --------------------------------------------------------------------------

def _linha(registro, buscar_cota_recente=None):
    bruto = dict(registro)
    aplicacao = _converter_data(bruto["data_aplicacao"], "data_aplicacao")
    numero_cotas = float(bruto["numero_cotas"])
    valor_cota_aplicacao = float(bruto["valor_cota_aplicacao"])
    valor_aplicado = round(numero_cotas * valor_cota_aplicacao, 2)
    cnpj = bruto["cnpj"]

    buscar_cota_recente = buscar_cota_recente or cota_cvm.cota_mais_recente
    cota_atual, data_cota = buscar_cota_recente(cnpj)

    if cota_atual is not None:
        valor_atual = round(numero_cotas * cota_atual, 2)
        rentabilidade_pct = (round((cota_atual / valor_cota_aplicacao - 1) * 100.0, 2)
                             if valor_cota_aplicacao else None)
        apurado = True
        marcacao = (f"Cota de {data_cota} pelo Informe Diário da CVM "
                   f"(R$ {cota_atual:.6f} por cota) contra a cota de "
                   f"R$ {valor_cota_aplicacao:.6f} na aplicação.")
    else:
        valor_atual = valor_aplicado
        rentabilidade_pct = 0.0
        apurado = False
        marcacao = RESUMO_NAO_APURADO

    return {
        "id": bruto["id"],
        "nome_fundo": bruto["nome_fundo"],
        "cnpj": bruto["cnpj"],
        "cnpj_formatado": formatar_cnpj(bruto["cnpj"]),
        "classe": bruto["classe"],
        "rotulo_classe": ROTULOS_CLASSE.get(bruto["classe"], bruto["classe"]),
        "numero_cotas": numero_cotas,
        "valor_cota_aplicacao": valor_cota_aplicacao,
        "data_aplicacao": aplicacao.isoformat(),
        "valor_aplicado": valor_aplicado,
        "valor_atual": valor_atual,
        "rentabilidade_pct": rentabilidade_pct,
        "apurado": apurado,
        "marcacao": marcacao,
        "atualizado_em": bruto.get("atualizado_em"),
    }


def listar(usuario_id, buscar_cota_recente=None):
    """Posições em fundos. Cada uma é apurada pela cota mais recente da CVM
    quando o CNPJ tem coleta (Etapa B); sem coleta para aquele CNPJ, valor
    atual é o aplicado — ver docstring do módulo. Peso por posição sobre o
    valor ATUAL (mesmo critério de `renda_fixa.listar`), não sobre o
    aplicado: reflete a rentabilidade já apurada de cada uma."""
    contas.iniciar()
    with contas._conectar() as cx:
        linhas = cx.execute(
            "SELECT * FROM fundos WHERE usuario_id = ? ORDER BY nome_fundo",
            (usuario_id,)).fetchall()

    posicoes = [_linha(l, buscar_cota_recente=buscar_cota_recente) for l in linhas]
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


def adicionar(usuario_id, nome_fundo, cnpj, classe, numero_cotas,
             valor_cota_aplicacao, data_aplicacao):
    nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao, aplicacao = validar(
        nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao, data_aplicacao)

    contas.iniciar()
    agora = _agora()
    with contas._conectar() as cx:
        quantas = cx.execute(
            "SELECT COUNT(*) FROM fundos WHERE usuario_id = ?",
            (usuario_id,)).fetchone()[0]
        if quantas >= MAXIMO_POSICOES:
            raise ErroFundo(f"Limite de {MAXIMO_POSICOES} posições em fundos atingido.")

        cursor = cx.execute(
            "INSERT INTO fundos (usuario_id, nome_fundo, cnpj, classe, "
            "numero_cotas, valor_cota_aplicacao, data_aplicacao, criado_em, "
            "atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (usuario_id, nome_fundo, cnpj, classe, numero_cotas,
             valor_cota_aplicacao, aplicacao.isoformat(), agora, agora))
        linha = cx.execute("SELECT * FROM fundos WHERE id = ?",
                           (cursor.lastrowid,)).fetchone()

    return _linha(linha)


def atualizar(usuario_id, posicao_id, nome_fundo, cnpj, classe, numero_cotas,
             valor_cota_aplicacao, data_aplicacao):
    nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao, aplicacao = validar(
        nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao, data_aplicacao)

    contas.iniciar()
    with contas._conectar() as cx:
        alterou = cx.execute(
            "UPDATE fundos SET nome_fundo=?, cnpj=?, classe=?, numero_cotas=?, "
            "valor_cota_aplicacao=?, data_aplicacao=?, atualizado_em=? "
            "WHERE id = ? AND usuario_id = ?",
            (nome_fundo, cnpj, classe, numero_cotas, valor_cota_aplicacao,
             aplicacao.isoformat(), _agora(), posicao_id, usuario_id)).rowcount
        if not alterou:
            return None
        linha = cx.execute("SELECT * FROM fundos WHERE id = ?",
                           (posicao_id,)).fetchone()

    return _linha(linha)


def remover(usuario_id, posicao_id):
    """True se removeu. `usuario_id` no WHERE: sem ele, saber o id de outra
    pessoa bastaria para apagar a posição dela."""
    contas.iniciar()
    with contas._conectar() as cx:
        apagou = cx.execute(
            "DELETE FROM fundos WHERE id = ? AND usuario_id = ?",
            (posicao_id, usuario_id)).rowcount
    return apagou > 0
