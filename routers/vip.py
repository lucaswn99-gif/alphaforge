"""Console VIP: tela paralela e API da carteira do assinante.

**Tela paralela, porta única.** `/vip/login` tem interface e rota próprias,
como o produto pede, mas o formulário posta no mesmo `/conta/entrar` que já
existe. Duplicar a autenticação seria duplicar hash de senha, resistência a
timing attack, rate limit e política de cookie — e o caminho novo quase sempre
nasce mais fraco que a porta da frente. O que o VIP tem de próprio é
AUTORIZAÇÃO: cada rota daqui exige `ctx.premium`, verificado no servidor.

**Por que `/api/v1`.** É o namespace da camada de portfólio: os pilares
seguintes (diagnóstico, rebalanceamento, stress, radar) moram todos nele, e o
app Android é consumidor externo — versionar desde o primeiro dia é barato
agora e caro depois. As rotas antigas ficam onde estão; isto é fronteira
deliberada, não convenção acidental.
"""

import os
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from modules import carteira, planos

router = APIRouter(tags=["VIP & Carteira"])

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGINA_VIP = os.path.join(BASE_DIR, "templates", "vip.html")

# Escrita na carteira por usuário. Não é cota de plano (assinante não tem
# cota): é guarda-corpo contra laço acidental do cliente, que numa tela com
# envio assíncrono é fácil de disparar sem querer.
_ESCRITAS_MAX = 120
_ESCRITAS_JANELA_S = 60
_escritas = defaultdict(deque)


class ItemCarteira(BaseModel):
    ticker: str = Field(..., max_length=12)
    quantidade: float
    preco_medio: float = Field(..., alias="preco_medio")


def _vip(request: Request) -> planos.Contexto:
    """Dependência das rotas de dado: exige sessão premium.

    401 para quem não entrou e 402 para quem entrou sem assinar são coisas
    diferentes, e a tela reage diferente a cada uma — mandar para o login quem
    só precisa assinar seria um beco sem saída.
    """
    ctx = planos.resolver_contexto(request)
    if not ctx.autenticado:
        raise HTTPException(status_code=401, detail={
            "erro": "nao_autenticado",
            "motivo": "Entre com a sua conta para acessar o console VIP."})
    if not ctx.premium:
        raise HTTPException(status_code=402, detail={
            "erro": "limite_plano",
            "recurso": "vip_carteira",
            "motivo": "O console de carteira é do plano Premium.",
            "plano_atual": ctx.plano,
            "autenticado": True})
    return ctx


def _limitar_escrita(ctx: planos.Contexto):
    agora = time.time()
    fila = _escritas[ctx.identidade]
    while fila and agora - fila[0] > _ESCRITAS_JANELA_S:
        fila.popleft()
    if len(fila) >= _ESCRITAS_MAX:
        raise HTTPException(status_code=429, detail={
            "erro": "excesso_escritas",
            "motivo": "Muitas alterações seguidas. Espere um instante."})
    fila.append(agora)


def _pagina():
    if not os.path.exists(PAGINA_VIP):
        return HTMLResponse("<h1>templates/vip.html não encontrado.</h1>",
                            status_code=500)
    with open(PAGINA_VIP, "r", encoding="utf-8") as arquivo:
        return HTMLResponse(arquivo.read())


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------

@router.get("/vip/login", response_class=HTMLResponse)
def pagina_login():
    """Entrada do console. Pública de propósito: é onde se entra."""
    return _pagina()


@router.get("/vip", response_class=HTMLResponse)
def pagina_console(request: Request):
    """Console da carteira.

    Serve o MESMO arquivo do login; quem decide o que aparece é o JavaScript,
    depois de perguntar ao servidor quem está falando. Dois arquivos HTML para
    dois estados da mesma sessão dobrariam o CSS e a chance de divergirem.

    Quem não está autenticado é mandado para o login. Quem está mas não é
    assinante fica aqui e vê o convite — mandar essa pessoa para a tela de
    login seria pedir que ela entre numa conta em que já está.
    """
    ctx = planos.resolver_contexto(request)
    if not ctx.autenticado:
        return RedirectResponse("/vip/login", status_code=303)
    return _pagina()


# --------------------------------------------------------------------------
# API da carteira
# --------------------------------------------------------------------------

@router.get("/api/v1/carteira")
def listar_carteira(ctx: planos.Contexto = Depends(_vip)):
    """Posições, custo total e peso de cada uma."""
    return carteira.listar(ctx.usuario["id"])


@router.post("/api/v1/carteira/item", status_code=201)
def criar_item(item: ItemCarteira, ctx: planos.Contexto = Depends(_vip)):
    """Cria a posição, ou incorpora o aporte por média ponderada."""
    _limitar_escrita(ctx)
    try:
        linha, foi_merge = carteira.adicionar(
            ctx.usuario["id"], item.ticker, item.quantidade, item.preco_medio)
    except carteira.ErroCarteira as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    return {"posicao": linha, "aporte_incorporado": foi_merge}


@router.patch("/api/v1/carteira/item/{ticker}")
def editar_item(ticker: str, item: ItemCarteira,
                ctx: planos.Contexto = Depends(_vip)):
    """Corrige a posição — SUBSTITUI os valores, não soma.

    Aporte e correção são intenções opostas, e por isso são verbos diferentes:
    um POST que às vezes soma e às vezes substitui seria um campo booleano
    decidindo o que acontece com o dinheiro de alguém.
    """
    _limitar_escrita(ctx)
    try:
        linha = carteira.atualizar(ctx.usuario["id"], ticker,
                                   item.quantidade, item.preco_medio)
    except carteira.ErroCarteira as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    if linha is None:
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado",
            "motivo": f"{carteira.normalizar_ticker(ticker)} não está na carteira."})
    return {"posicao": linha}


@router.delete("/api/v1/carteira/item/{ticker}")
def remover_item(ticker: str, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    if not carteira.remover(ctx.usuario["id"], ticker):
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado",
            "motivo": f"{carteira.normalizar_ticker(ticker)} não está na carteira."})
    return {"removido": carteira.normalizar_ticker(ticker)}
