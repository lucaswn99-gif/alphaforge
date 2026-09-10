"""Planos, cotas e corte de resposta.

O corte acontece **aqui, no servidor**. Se a API devolvesse a lista inteira e a
tela escondesse o excesso com CSS, o dado já teria sido entregue: qualquer um
abre o painel de rede do navegador e lê tudo. Borrão é enfeite, não limite.

A régua do gratuito é **volume, não função**. O usuário sem assinatura vê os
cinco primeiros papéis do ranking com número real e completo — ele confia no
motor e sente falta dos outros setenta e cinco. Esconder o módulo inteiro faz
desinstalar; mostrar cinco linhas boas faz querer o resto.

Duas exceções são bloqueio de verdade, e por um motivo só: são o produto.
Recomendar a estrutura de opções e otimizar a carteira não são consultas — são
a decisão pronta. É o que alguém paga para ter.
"""

from dataclasses import dataclass
from typing import Optional

from fastapi import HTTPException, Request

from modules import contas

# Quantas linhas o plano gratuito recebe de cada listagem.
SCANNER_FREE_LINHAS = 5
QUANT_FREE_LINHAS = 3
FUNDOS_FREE_LINHAS = 4

# Consultas por dia no plano gratuito.
LIMITES_FREE = {
    "rv_auditoria": 3,
    "quant_papel": 3,
    "opcoes_precificar": 5,
    "credito_calcular": 2,
}

# Sem equivalente gratuito.
BLOQUEADOS_FREE = {
    "opcoes_recomendar": "A recomendação de estruturas é do plano Premium.",
    "wealth_otimizar": "A otimização de carteira é do plano Premium.",
}

# Chaves de detalhe que só o Premium recebe: são o "porquê" por trás da nota.
DETALHE_SCORE = ("alertas_risco", "pontos_positivos", "nao_apurados",
                 "componentes", "criterios")


@dataclass
class Contexto:
    """Quem está chamando e com que direito."""
    plano: str
    identidade: str
    usuario: Optional[dict] = None

    @property
    def premium(self):
        return self.plano == "premium"

    @property
    def autenticado(self):
        return self.usuario is not None


# --------------------------------------------------------------------------
# Identidade
# --------------------------------------------------------------------------

def _ip_do_pedido(request: Request):
    """IP real do cliente. Atrás do nginx, `request.client.host` é sempre
    127.0.0.1 — o endereço de quem chamou está no primeiro salto do
    X-Forwarded-For."""
    encaminhado = request.headers.get("x-forwarded-for", "")
    if encaminhado:
        return encaminhado.split(",")[0].strip()
    real = request.headers.get("x-real-ip", "")
    if real:
        return real.strip()
    return request.client.host if request.client else "desconhecido"


def resolver_contexto(request: Request) -> Contexto:
    """Descobre plano e identidade. Nunca levanta: falha de banco aqui não
    pode derrubar um endpoint de dado."""
    try:
        token = request.cookies.get(contas.NOME_COOKIE)
        usuario = contas.usuario_da_sessao(token) if token else None
    except Exception:  # noqa: BLE001
        usuario = None

    if usuario:
        return Contexto(plano=contas.plano_do_usuario(usuario),
                        identidade=f"u:{usuario['id']}", usuario=usuario)
    return Contexto(plano="free", identidade=f"ip:{_ip_do_pedido(request)}")


# --------------------------------------------------------------------------
# Cobrança de cota
# --------------------------------------------------------------------------

def _recusar(recurso, motivo, limite=None, usado=None, autenticado=False):
    """402 com corpo estruturado — a tela monta o aviso a partir disto, em vez
    de adivinhar pelo texto do erro."""
    raise HTTPException(status_code=402, detail={
        "erro": "limite_plano",
        "recurso": recurso,
        "motivo": motivo,
        "limite": limite,
        "usado": usado,
        "plano_atual": "free",
        "autenticado": autenticado,
        "reinicia_em": contas.proxima_virada() if limite else None,
    })


def acesso(recurso: Optional[str] = None):
    """Dependência de rota.

    Sem `recurso`, só identifica quem chama — para os endpoints que o gratuito
    acessa com a resposta cortada. Com `recurso`, aplica bloqueio ou cota.
    """
    def dependencia(request: Request) -> Contexto:
        ctx = resolver_contexto(request)

        if recurso is None or ctx.premium:
            return ctx

        if recurso in BLOQUEADOS_FREE:
            _recusar(recurso, BLOQUEADOS_FREE[recurso], autenticado=ctx.autenticado)

        limite = LIMITES_FREE.get(recurso)
        if limite is None:
            return ctx

        try:
            usados = contas.consultar_uso(ctx.identidade, recurso)
        except Exception:  # noqa: BLE001 — banco fora do ar não vira paywall
            return ctx

        if usados >= limite:
            _recusar(recurso,
                     f"Você usou as {limite} consultas gratuitas de hoje.",
                     limite=limite, usado=usados, autenticado=ctx.autenticado)

        try:
            contas.registrar_uso(ctx.identidade, recurso)
        except Exception:  # noqa: BLE001
            pass
        return ctx

    return dependencia


def saldo(ctx: Contexto):
    """Quanto sobrou de cada cota hoje. Vai no /conta/eu e no rodapé da tela."""
    if ctx.premium:
        return {chave: {"limite": None, "usado": 0, "resta": None}
                for chave in LIMITES_FREE}
    resultado = {}
    for chave, limite in LIMITES_FREE.items():
        try:
            usado = contas.consultar_uso(ctx.identidade, chave)
        except Exception:  # noqa: BLE001
            usado = 0
        resultado[chave] = {"limite": limite, "usado": usado,
                            "resta": max(0, limite - usado)}
    return resultado


# --------------------------------------------------------------------------
# Corte de resposta
# --------------------------------------------------------------------------

def _marcar(payload, total_real, exibidos, motivo):
    """Carimbo que a tela lê para desenhar o convite de assinatura. Explicitar
    o que foi omitido é mais honesto — e converte melhor — que entregar uma
    lista curta sem dizer que é curta."""
    payload["plano"] = "free"
    payload["truncado"] = True
    payload["total_disponivel"] = total_real
    payload["exibidos"] = exibidos
    payload["ocultos"] = max(0, total_real - exibidos)
    payload["motivo_corte"] = motivo
    return payload


def _sem_detalhe(linha):
    return {k: v for k, v in linha.items() if k not in DETALHE_SCORE}


def cortar_scanner(payload, ctx: Contexto):
    """Scanner de renda variável: cinco papéis e sem os fatores do score."""
    if ctx.premium or not isinstance(payload, dict):
        return payload
    lista = payload.get("oportunidades") or []
    payload = dict(payload)
    payload["oportunidades"] = [_sem_detalhe(l) for l in lista[:SCANNER_FREE_LINHAS]]
    # As listas de diagnóstico descrevem o universo inteiro; com o ranking
    # cortado elas passariam a falar de papéis que a resposta não contém.
    payload["falhas"] = []
    payload["sem_fundamentos"] = []
    return _marcar(payload, len(lista), len(payload["oportunidades"]),
                   "O plano gratuito mostra os cinco primeiros do ranking.")


def cortar_quant(payload, ctx: Contexto):
    """Radar quantitativo: três papéis de amostra."""
    if ctx.premium or not isinstance(payload, dict):
        return payload
    lista = payload.get("papeis") or []
    payload = dict(payload)
    payload["papeis"] = [_sem_detalhe(l) for l in lista[:QUANT_FREE_LINHAS]]
    return _marcar(payload, len(lista), len(payload["papeis"]),
                   "O plano gratuito mostra três papéis do radar.")


def cortar_fundos(payload, ctx: Contexto):
    """FIIs e ETFs: a lista aparece, a recomendação por P/VP não.

    Ver o desconto instiga; saber o que fazer com ele é o que se assina.
    """
    if ctx.premium or not isinstance(payload, dict):
        return payload
    payload = dict(payload)
    total = 0
    for bloco in ("tijolo", "papel", "etfs"):
        lista = payload.get(bloco) or []
        total += len(lista)
        payload[bloco] = [
            {k: v for k, v in linha.items() if k != "recomendacao"}
            for linha in lista[:FUNDOS_FREE_LINHAS]
        ]
    exibidos = sum(len(payload.get(b) or []) for b in ("tijolo", "papel", "etfs"))
    return _marcar(payload, total, exibidos,
                   "O plano gratuito mostra parte da lista, sem a recomendação.")
