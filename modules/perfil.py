"""Perfil do investidor e objetivo da carteira: campo simples, atribuído pelo
assessor — sem questionário de suitability próprio.

O pedido foi explícito: nem replicar o questionário CVM/ANBIMA que a
Wisers/BTG já usa, nem inventar um segundo. O assessor certificado já fez a
triagem do cliente — o que faltava era **um campo** para guardar essa
classificação e o objetivo de vida que ela serve, para o backtest, a projeção
e o relatório usarem depois (Pilares 4, 5 e 6 desta leva).

**Perfil e objetivo são independentes, e cada um pode ficar sem valor.** Não
há "padrão" para nenhum dos dois — o mesmo motivo de sempre: atribuir um
perfil a quem não foi classificado seria inventar dado sobre o dinheiro de
outra pessoa. Sem perfil definido, a tela mostra que falta definir, não um
valor arbitrário.

**O objetivo carrega só os campos que ele precisa.** Renda passiva pede uma
meta de retirada mensal; aposentadoria pede horizonte (em anos) e, opcional,
uma meta de patrimônio ou de renda mensal na data-alvo. Trocar de objetivo
não arrasta campo do objetivo anterior — o `definir` sempre grava o conjunto
inteiro, para nunca sobrar um `meta_retirada_mensal` de uma renda passiva que
já foi trocada por aposentadoria.
"""

from datetime import datetime, timezone

from modules import contas

CONSERVADOR = "conservador"
MODERADO = "moderado"
ARROJADO = "arrojado"
PERFIS = (CONSERVADOR, MODERADO, ARROJADO)

ROTULOS_PERFIL = {
    CONSERVADOR: "Conservador",
    MODERADO: "Moderado",
    ARROJADO: "Arrojado",
}

RENDA_PASSIVA = "renda_passiva"
APOSENTADORIA = "aposentadoria"
OBJETIVOS = (RENDA_PASSIVA, APOSENTADORIA)

ROTULOS_OBJETIVO = {
    RENDA_PASSIVA: "Renda passiva",
    APOSENTADORIA: "Aposentadoria",
}

VALOR_MAXIMO = 1_000_000_000.0
HORIZONTE_MAXIMO_ANOS = 80


class ErroPerfil(ValueError):
    """Entrada recusada, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def validar_perfil(bruto):
    """Normalizado, ou None quando vazio — perfil é opcional até o assessor
    classificar."""
    if bruto in (None, ""):
        return None
    perfil = str(bruto).strip().lower()
    if perfil not in PERFIS:
        raise ErroPerfil(
            f"Perfil desconhecido: '{bruto}'. Use {', '.join(PERFIS)}.")
    return perfil


def _numero_positivo(bruto, campo):
    try:
        valor = float(bruto)
    except (TypeError, ValueError):
        raise ErroPerfil(f"{campo} precisa ser um número.")
    if not (0 < valor <= VALOR_MAXIMO):
        raise ErroPerfil(f"{campo} fora da faixa aceita.")
    return valor


def validar(perfil, objetivo, meta_retirada_mensal=None, horizonte_anos=None,
           meta_patrimonio=None, meta_renda_mensal=None):
    """(perfil, objetivo, meta_retirada_mensal, horizonte_anos,
    meta_patrimonio, meta_renda_mensal) normalizados.

    Campos que não pertencem ao objetivo escolhido voltam None — nunca
    sobra dado do objetivo anterior depois de uma troca.
    """
    perfil = validar_perfil(perfil)

    if objetivo in (None, ""):
        return perfil, None, None, None, None, None

    objetivo = str(objetivo).strip().lower()
    if objetivo not in OBJETIVOS:
        raise ErroPerfil(
            f"Objetivo desconhecido: '{objetivo}'. Use {', '.join(OBJETIVOS)}.")

    if objetivo == RENDA_PASSIVA:
        if meta_retirada_mensal in (None, ""):
            raise ErroPerfil(
                "Renda passiva precisa de uma meta de retirada mensal.")
        meta_retirada_mensal = _numero_positivo(
            meta_retirada_mensal, "Meta de retirada mensal")
        return perfil, objetivo, meta_retirada_mensal, None, None, None

    # APOSENTADORIA
    if horizonte_anos in (None, ""):
        raise ErroPerfil("Aposentadoria precisa de um horizonte em anos.")
    try:
        horizonte_anos = int(horizonte_anos)
    except (TypeError, ValueError):
        raise ErroPerfil("Horizonte precisa ser um número inteiro de anos.")
    if not (0 < horizonte_anos <= HORIZONTE_MAXIMO_ANOS):
        raise ErroPerfil(
            f"Horizonte fora da faixa aceita (1 a {HORIZONTE_MAXIMO_ANOS} anos).")

    meta_patrimonio = (_numero_positivo(meta_patrimonio, "Meta de patrimônio")
                       if meta_patrimonio not in (None, "") else None)
    meta_renda_mensal = (_numero_positivo(meta_renda_mensal, "Meta de renda mensal")
                         if meta_renda_mensal not in (None, "") else None)

    return perfil, objetivo, None, horizonte_anos, meta_patrimonio, meta_renda_mensal


def obter(usuario_id):
    """Registro atual, ou tudo None quando o assessor ainda não classificou
    nada — `definido` na saída da rota é o sinal que a tela usa."""
    contas.iniciar()
    with contas._conectar() as cx:
        linha = cx.execute(
            "SELECT * FROM perfil_carteira WHERE usuario_id = ?",
            (usuario_id,)).fetchone()
    if not linha:
        return {"perfil": None, "rotulo_perfil": None, "objetivo": None,
                "rotulo_objetivo": None, "meta_retirada_mensal": None,
                "horizonte_anos": None, "meta_patrimonio": None,
                "meta_renda_mensal": None, "atualizado_em": None}
    bruto = dict(linha)
    return {
        "perfil": bruto["perfil"],
        "rotulo_perfil": ROTULOS_PERFIL.get(bruto["perfil"]) if bruto["perfil"] else None,
        "objetivo": bruto["objetivo"],
        "rotulo_objetivo": ROTULOS_OBJETIVO.get(bruto["objetivo"]) if bruto["objetivo"] else None,
        "meta_retirada_mensal": bruto["meta_retirada_mensal"],
        "horizonte_anos": bruto["horizonte_anos"],
        "meta_patrimonio": bruto["meta_patrimonio"],
        "meta_renda_mensal": bruto["meta_renda_mensal"],
        "atualizado_em": bruto["atualizado_em"],
    }


def definir(usuario_id, perfil, objetivo, meta_retirada_mensal=None,
           horizonte_anos=None, meta_patrimonio=None, meta_renda_mensal=None):
    """Grava o registro inteiro (substitui, não mescla — ver docstring do
    módulo sobre por que a troca de objetivo não pode deixar resto)."""
    (perfil, objetivo, meta_retirada_mensal, horizonte_anos, meta_patrimonio,
     meta_renda_mensal) = validar(
        perfil, objetivo, meta_retirada_mensal, horizonte_anos,
        meta_patrimonio, meta_renda_mensal)

    contas.iniciar()
    with contas._conectar() as cx:
        cx.execute(
            "INSERT INTO perfil_carteira (usuario_id, perfil, objetivo, "
            "meta_retirada_mensal, horizonte_anos, meta_patrimonio, "
            "meta_renda_mensal, atualizado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(usuario_id) DO UPDATE SET "
            "perfil = excluded.perfil, objetivo = excluded.objetivo, "
            "meta_retirada_mensal = excluded.meta_retirada_mensal, "
            "horizonte_anos = excluded.horizonte_anos, "
            "meta_patrimonio = excluded.meta_patrimonio, "
            "meta_renda_mensal = excluded.meta_renda_mensal, "
            "atualizado_em = excluded.atualizado_em",
            (usuario_id, perfil, objetivo, meta_retirada_mensal, horizonte_anos,
             meta_patrimonio, meta_renda_mensal, _agora()))

    return obter(usuario_id)
