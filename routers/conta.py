"""Conta, sessão e estado do plano.

Conta aqui é opcional por decisão de produto, não por descuido: a ficha do app
na Play Store declara que nenhuma parte exige login, e a revisão testa isso.
Quem não entra usa o plano gratuito com a cota contada por IP. A conta serve
para levar a cota entre aparelhos, e para assinar.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from modules import contas, planos, play

router = APIRouter(prefix="/conta", tags=["conta"])

# Tentativas de login por IP: 10 a cada 15 minutos. Em memória de propósito —
# some no restart, que é aceitável para travar força bruta, e não paga ida ao
# banco em toda tentativa.
_JANELA_S = 900
_MAX_TENTATIVAS = 10
_tentativas = defaultdict(deque)


class Credenciais(BaseModel):
    email: str = Field(..., max_length=254)
    senha: str = Field(..., max_length=200)


def _cookie_seguro(request: Request):
    """HTTPS atrás do nginx chega como http no processo; o cabeçalho de
    encaminhamento é o que diz a verdade."""
    if request.headers.get("x-forwarded-proto", "").lower() == "https":
        return True
    return request.url.scheme == "https"


def _gravar_cookie(resposta: Response, token: str, request: Request):
    resposta.set_cookie(
        key=contas.NOME_COOKIE, value=token,
        max_age=contas.DIAS_SESSAO * 86400,
        httponly=True, samesite="lax", secure=_cookie_seguro(request), path="/",
    )


def _limitar(request: Request):
    ip = planos._ip_do_pedido(request)
    agora = time.time()
    fila = _tentativas[ip]
    while fila and agora - fila[0] > _JANELA_S:
        fila.popleft()
    if len(fila) >= _MAX_TENTATIVAS:
        raise HTTPException(status_code=429, detail={
            "erro": "excesso_tentativas",
            "motivo": "Muitas tentativas. Tente de novo em alguns minutos.",
        })
    fila.append(agora)


def _publico(usuario, ctx):
    """O que a tela pode ver da conta. Sem hash, sem salt, sem id interno."""
    return {
        "autenticado": usuario is not None,
        "email": usuario["email"] if usuario else None,
        "plano": ctx.plano,
        "plano_ate": usuario.get("plano_ate") if usuario else None,
        "cotas": planos.saldo(ctx),
        "limites_free": planos.LIMITES_FREE,
        "bloqueados_free": sorted(planos.BLOQUEADOS_FREE),
    }


@router.post("/registrar")
def registrar(dados: Credenciais, request: Request, resposta: Response):
    """Cria a conta e já entra — pedir para digitar a senha de novo logo depois
    de criá-la não protege nada."""
    _limitar(request)
    usuario, motivo = contas.criar_usuario(dados.email, dados.senha)
    if motivo:
        raise HTTPException(status_code=400, detail={"erro": "cadastro", "motivo": motivo})

    token = contas.abrir_sessao(usuario["id"])
    _gravar_cookie(resposta, token, request)
    ctx = planos.Contexto(plano=contas.plano_do_usuario(usuario),
                          identidade=f"u:{usuario['id']}", usuario=usuario)
    return _publico(usuario, ctx)


@router.post("/entrar")
def entrar(dados: Credenciais, request: Request, resposta: Response):
    _limitar(request)
    usuario = contas.autenticar(dados.email, dados.senha)
    if usuario is None:
        # Mesma mensagem para e-mail inexistente e senha errada: distinguir os
        # dois entrega a lista de quem tem conta aqui.
        raise HTTPException(status_code=401, detail={
            "erro": "credenciais", "motivo": "E-mail ou senha incorretos."})

    token = contas.abrir_sessao(usuario["id"])
    _gravar_cookie(resposta, token, request)
    ctx = planos.Contexto(plano=contas.plano_do_usuario(usuario),
                          identidade=f"u:{usuario['id']}", usuario=usuario)
    return _publico(usuario, ctx)


@router.post("/sair")
def sair(request: Request, resposta: Response):
    contas.fechar_sessao(request.cookies.get(contas.NOME_COOKIE))
    resposta.delete_cookie(contas.NOME_COOKIE, path="/")
    return {"autenticado": False, "plano": "free"}


@router.get("/eu")
def eu(request: Request, ctx: planos.Contexto = Depends(planos.acesso())):
    """Estado atual: quem é, que plano tem, quanto sobrou de cota hoje.

    Responde igual para anônimo — a tela precisa saber o saldo do gratuito
    mesmo sem conta, senão não tem como avisar antes de esbarrar no limite.
    """
    return _publico(ctx.usuario, ctx)


# --------------------------------------------------------------------------
# Assinatura
# --------------------------------------------------------------------------

class EventoAssinatura(BaseModel):
    email: str
    plano: str = Field(..., pattern="^(free|premium)$")
    ate: str | None = None


@router.post("/assinatura/webhook")
def webhook_assinatura(evento: EventoAssinatura, request: Request):
    """Ponto de entrada para o provedor de pagamento marcar a conta.

    Inerte até `AF_WEBHOOK_SEGREDO` existir no ambiente. Sem o segredo, a rota
    responde 503 em vez de aceitar qualquer chamada — um webhook aberto é um
    botão de "me dê Premium" exposto na internet.

    Quem chama: o Google Play (via Pub/Sub, no Android) e a adquirente do site.
    Os dois traduzem o próprio formato para este corpo antes de chamar aqui.
    """
    segredo = os.environ.get("AF_WEBHOOK_SEGREDO", "")
    if not segredo:
        raise HTTPException(status_code=503, detail={
            "erro": "webhook_desligado",
            "motivo": "AF_WEBHOOK_SEGREDO não configurado no servidor."})

    enviado = request.headers.get("x-af-segredo", "")
    if not enviado or not _igual(enviado, segredo):
        raise HTTPException(status_code=401, detail={"erro": "segredo_invalido"})

    contas.iniciar()
    email = contas.normalizar_email(evento.email)
    with contas._conectar() as cx:
        linha = cx.execute("SELECT id FROM usuarios WHERE email = ?", (email,)).fetchone()
    if linha is None:
        raise HTTPException(status_code=404, detail={
            "erro": "conta_inexistente",
            "motivo": f"Nenhuma conta com o e-mail {email}."})

    contas.definir_plano(linha["id"], evento.plano, evento.ate)
    return {"ok": True, "email": email, "plano": evento.plano, "ate": evento.ate}


def _igual(a, b):
    import secrets
    return secrets.compare_digest(a, b)


# --------------------------------------------------------------------------
# Google Play Billing
# --------------------------------------------------------------------------

class CompraPlay(BaseModel):
    token: str = Field(..., max_length=1024)


@router.get("/assinatura/produtos")
def produtos_disponiveis():
    """O que a tela pode oferecer. Os preços vêm do Play, não daqui: preço
    escrito no servidor e preço cobrado pelo Google divergem no dia em que
    alguém mexer em um sem lembrar do outro."""
    return {
        "disponivel": play.configurado(),
        "produtos": [{"id": chave, **valor} for chave, valor in play.PRODUTOS.items()],
    }


def _aplicar_compra(token, usuario_id):
    """Consulta o Play, reconhece se preciso, grava e recalcula o plano.

    Caminho único: tudo que muda assinatura passa por aqui, venha da tela ou
    da notificação. Duas rotas capazes de conceder Premium por caminhos
    diferentes é como uma delas fica para trás numa correção.
    """
    leitura = play.interpretar(play.consultar(token))

    # Troca de plano: o token novo aponta para o antigo, que para de valer.
    anterior = leitura.get("token_anterior")
    if anterior:
        contas.encerrar_assinatura(anterior)

    dono = contas.salvar_assinatura(token, usuario_id, leitura)

    # Reconhecer é obrigatório e tem prazo de três dias — depois disso o Google
    # estorna sozinho. Falhar aqui não pode derrubar a concessão do acesso: o
    # usuário pagou. Se o reconhecimento falhar, a notificação seguinte tenta
    # de novo.
    if leitura["ativo"] and not leitura["reconhecida"] and leitura["produto"]:
        try:
            play.reconhecer(token, leitura["produto"])
            leitura["reconhecida"] = True
            contas.salvar_assinatura(token, dono, leitura)
        except play.PlayIndisponivel as erro:
            print(f"[play] falha ao reconhecer {token[:12]}…: {erro}")

    if dono:
        contas.recalcular_plano(dono)
    return leitura, dono


@router.post("/assinatura/play/confirmar")
def confirmar_compra(dados: CompraPlay, request: Request,
                     ctx: planos.Contexto = Depends(planos.acesso())):
    """Recebe o `purchaseToken` da tela e o troca por acesso — se o Google
    concordar.

    O corpo traz só o token. Qual produto foi comprado, se está pago e até
    quando vale são perguntas respondidas pelo Google, nunca pelo cliente.
    """
    if not play.configurado():
        raise HTTPException(status_code=503, detail={
            "erro": "play_desligado",
            "motivo": "A assinatura pelo aplicativo ainda não está configurada."})

    if not ctx.autenticado:
        raise HTTPException(status_code=401, detail={
            "erro": "sem_conta",
            "motivo": "Entre na sua conta antes de assinar, para a assinatura "
                      "ficar ligada a ela."})

    try:
        leitura, _ = _aplicar_compra(dados.token, ctx.usuario["id"])
    except play.PlayIndisponivel as erro:
        raise HTTPException(status_code=502, detail={
            "erro": "play_indisponivel", "motivo": str(erro)})

    if not leitura["ativo"]:
        raise HTTPException(status_code=402, detail={
            "erro": "compra_invalida",
            "motivo": "O Google não reconhece essa compra como ativa.",
            "estado": leitura["estado"]})

    usuario = contas.buscar_usuario(ctx.usuario["id"])
    novo = planos.Contexto(plano=contas.plano_do_usuario(usuario),
                           identidade=ctx.identidade, usuario=usuario)
    return {**_publico(usuario, novo), "assinatura": {
        "produto": leitura["produto"], "expira_em": leitura["expira_em"],
        "estado": leitura["estado"]}}


@router.post("/assinatura/play/notificar")
def notificacao_play(corpo: dict, request: Request):
    """Recebe as Real-time Developer Notifications, via push do Pub/Sub.

    O aviso diz apenas que **algo mudou** e entrega o token. O tipo do evento é
    deliberadamente ignorado como fonte de verdade: quem confia nele acaba
    liberando Premium para assinatura cancelada. O que vale é reconsultar.

    Responde 200 mesmo para o que não interessa. Erro devolvido ao Pub/Sub vira
    reentrega, e reentrega infinita de um aviso que nunca vamos usar é ruído
    que esconde a falha real.
    """
    segredo = os.environ.get("AF_PLAY_SEGREDO", "")
    if not segredo:
        raise HTTPException(status_code=503, detail={
            "erro": "webhook_desligado",
            "motivo": "AF_PLAY_SEGREDO não configurado no servidor."})
    if not _igual(request.query_params.get("segredo", ""), segredo):
        raise HTTPException(status_code=401, detail={"erro": "segredo_invalido"})

    try:
        tipo, token, id_mensagem = play.decodificar_notificacao(corpo)
    except ValueError as erro:
        return {"ok": False, "motivo": str(erro)}

    if contas.aviso_ja_visto(id_mensagem):
        return {"ok": True, "repetido": True}
    if tipo == "TESTE":
        return {"ok": True, "teste": True}
    if not token:
        return {"ok": True, "ignorado": True}

    try:
        leitura, dono = _aplicar_compra(token, None)
    except play.PlayIndisponivel as erro:
        # 502 faz o Pub/Sub tentar de novo, que é o certo: a falha é nossa ou
        # do Google, e o aviso não pode ser perdido.
        raise HTTPException(status_code=502, detail={"erro": "play_indisponivel",
                                                     "motivo": str(erro)})

    return {"ok": True, "tipo": tipo, "estado": leitura["estado"],
            "conta_encontrada": bool(dono)}

from fastapi import HTTPException

# Rota temporária para te dar o Premium na nuvem
@router.get("/forcar-premium-admin")
def forcar_premium(senha_secreta: str):
    # Uma senha simples só para ninguém curioso acessar o link
    if senha_secreta != "abrete_sesamo":
        raise HTTPException(status_code=403, detail="Acesso negado.")
    
    # Tenta puxar a sua conta (você já deve ter criado ela no site oficial)
    from modules import contas
    u = contas.autenticar("lucaswn99@gmail.com", "Marley17?")
    
    if not u:
        return {"erro": "Você precisa criar a conta lucaswn99@gmail.com no site oficial primeiro!"}
    
    # Libera o Premium
    contas.definir_plano(u["id"], "premium")
    return {"status": "SUCESSO", "mensagem": "Bem-vindo de volta, chefe! Seu Premium está ativo."}
