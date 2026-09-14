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

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from modules import (alvos, carteira, diagnostico, importacao, planos,
                     rebalanceamento)

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


# O diagnóstico consulta perfil e balanço de cada posição — caro o bastante
# para não repetir a cada clique. Quinze minutos, o mesmo TTL do resto do
# projeto, por usuário (é a carteira dele que está sendo medida).
_CACHE_TTL = 900
_cache_diagnostico = {}


@router.get("/api/v1/carteira/diagnostico")
def diagnosticar_carteira(forcar: bool = False,
                          ctx: planos.Contexto = Depends(_vip)):
    """Cada posição medida contra a filosofia que cabe à classe dela.

    Quatro estados, não três: `nao_apurado` existe para o que não deu para
    medir, em vez de virar desconformidade — ver `modules/diagnostico.py`.
    """
    usuario_id = ctx.usuario["id"]
    dados = carteira.listar(usuario_id)

    # A chave inclui um carimbo das posições: mudou a carteira, o diagnóstico
    # velho não vale mais, mesmo dentro dos quinze minutos.
    assinatura = tuple(sorted((p["ticker"], p["quantidade"], p["preco_medio"])
                              for p in dados["posicoes"]))
    chave = (usuario_id, assinatura)

    agora = time.time()
    guardado = _cache_diagnostico.get(chave)
    if guardado and not forcar and agora - guardado[0] < _CACHE_TTL:
        return {**guardado[1], "cache": True,
                "idade_segundos": int(agora - guardado[0])}

    from routers import filosofias as rota_filosofias
    resultado = diagnostico.diagnosticar(rota_filosofias.motor(), dados["posicoes"])
    _cache_diagnostico[chave] = (time.time(), resultado)
    return {**resultado, "cache": False, "idade_segundos": 0}


# ------------------------------------------------------ Pilar 3: rebalanceamento

class DefinicaoAlvos(BaseModel):
    # dict e não campos fixos de propósito: `alvos.validar` recusa classe que
    # não existe, e um modelo com campos nomeados engoliria "cripto: 50" em
    # silêncio em vez de devolver o erro para a tela.
    alvos: dict[str, float]


@router.get("/api/v1/carteira/alvos")
def obter_alvos(ctx: planos.Contexto = Depends(_vip)):
    definidos = alvos.obter(ctx.usuario["id"])
    return {
        "alvos": definidos,
        "definido": definidos is not None,
        "classes": list(alvos.CLASSES_ALVO),
        "rotulos": alvos.ROTULOS,
    }


@router.put("/api/v1/carteira/alvos")
def definir_alvos(corpo: DefinicaoAlvos, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    try:
        gravados = alvos.definir(ctx.usuario["id"], corpo.alvos)
    except alvos.ErroAlvo as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "alvo_invalido", "motivo": str(erro)})
    return {"alvos": gravados, "definido": True}


def _precos_de_mercado(motor, tickers):
    """{ticker: preço} da B3. Papel que falhar volta ausente, não zerado.

    Consulta em paralelo com o mesmo teto do diagnóstico: é o `.info` do Yahoo
    que limita, e paralelismo demais devolve tabela vazia.
    """
    import concurrent.futures

    def buscar(ticker):
        try:
            perfil = motor.fonte.perfil(f"{ticker}.SA") or {}
            preco = perfil.get("preco")
            return ticker, (float(preco) if preco else None)
        except Exception:  # noqa: BLE001
            return ticker, None

    precos = {}
    if not tickers:
        return precos
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=diagnostico.MAX_WORKERS) as pool:
        for ticker, preco in pool.map(buscar, tickers):
            if preco:
                precos[ticker] = preco
    return precos


@router.get("/api/v1/carteira/rebalanceamento")
def rebalancear(aporte: float = 0.0, forcar: bool = False,
                ctx: planos.Contexto = Depends(_vip)):
    """Para onde mandar o próximo aporte, sem vender nada.

    `aporte=0` é caso de uso legítimo, não borda: devolve o retrato do desvio
    atual para a tela mostrar antes de o usuário digitar qualquer valor.
    """
    usuario_id = ctx.usuario["id"]
    definidos = alvos.obter(usuario_id)
    if definidos is None:
        raise HTTPException(status_code=422, detail={
            "erro": "alvo_nao_definido",
            "motivo": ("Defina o percentual-alvo de cada classe antes de "
                       "rebalancear. Não sugerimos uma alocação por você.")})

    dados = carteira.listar(usuario_id)
    if not dados["posicoes"]:
        raise HTTPException(status_code=422, detail={
            "erro": "carteira_vazia",
            "motivo": "Cadastre suas posições antes de rebalancear."})

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()

    # As posições em desconformidade não recebem aporte novo — foi a decisão
    # de produto do Pilar 3. Reaproveita o cache do diagnóstico: medir a
    # carteira inteira de novo a cada simulação de aporte seria caro e não
    # mudaria nada, já que o diagnóstico não depende do valor aportado.
    veredito = diagnosticar_carteira(forcar=forcar, ctx=ctx)
    bloqueados = {linha["ticker"] for linha in veredito.get("posicoes", [])
                  if linha.get("diagnostico", {}).get("estado") == diagnostico.DESCONFORME}

    precos = _precos_de_mercado(motor, [p["ticker"] for p in dados["posicoes"]])
    plano = rebalanceamento.planejar(
        dados["posicoes"], precos, definidos, aporte, bloqueados)

    return {**plano, "alvos": definidos, "rotulos": alvos.ROTULOS,
            "custo_total": dados["custo_total"]}


class LinhaImportada(BaseModel):
    ticker: str = Field(..., max_length=12)
    quantidade: float
    preco_medio: float


class Importacao(BaseModel):
    linhas: list[LinhaImportada] = Field(..., max_length=importacao.MAXIMO_LINHAS)
    # "substituir" trata a planilha como RETRATO da carteira (é o que um
    # extrato de corretora é); "somar" trata como lista de aportes. O padrão
    # é substituir porque importar extrato somando dobraria toda posição que
    # já existe — e dobrar quantidade calado é o pior erro possível aqui.
    modo: str = Field("substituir", pattern="^(substituir|somar)$")


def _planilha(arquivo: UploadFile):
    conteudo = arquivo.file.read(importacao.MAXIMO_BYTES + 1)
    try:
        return importacao.ler(conteudo, arquivo.filename)
    except importacao.ErroImportacao as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "planilha_invalida", "motivo": str(erro)})


@router.get("/api/v1/carteira/modelo")
def modelo_planilha(ctx: planos.Contexto = Depends(_vip)):
    """Planilha de exemplo com o formato aceito."""
    return Response(
        content=importacao.planilha_modelo(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="modelo-carteira.xlsx"'})


@router.post("/api/v1/carteira/importar/previa")
def previa_importacao(arquivo: UploadFile = File(...),
                      ctx: planos.Contexto = Depends(_vip)):
    """Lê a planilha e diz o que ACONTECERIA — sem gravar nada.

    A prévia não é cortesia: importar direto substituiria posições reais a
    partir de um arquivo que ninguém conferiu, e "desfazer" não existe. Aqui a
    pessoa vê linha a linha o que entra, o que muda e o que foi recusado, e só
    então confirma.
    """
    linhas = _planilha(arquivo)
    atuais = {p["ticker"]: p for p in carteira.listar(ctx.usuario["id"])["posicoes"]}

    for item in linhas:
        if item["erro"]:
            item["acao"] = "erro"
            continue
        atual = atuais.get(item["ticker"])
        item["acao"] = "atualiza" if atual else "novo"
        if atual:
            item["quantidade_atual"] = atual["quantidade"]
            item["preco_medio_atual"] = atual["preco_medio"]

    validas = [l for l in linhas if not l["erro"]]
    return {
        "linhas": linhas,
        "total": len(linhas),
        "validas": len(validas),
        "com_erro": len(linhas) - len(validas),
        "novos": sum(1 for l in validas if l["acao"] == "novo"),
        "existentes": sum(1 for l in validas if l["acao"] == "atualiza"),
    }


@router.post("/api/v1/carteira/importar")
def confirmar_importacao(dados: Importacao, ctx: planos.Contexto = Depends(_vip)):
    """Grava as linhas que a pessoa confirmou na prévia.

    Recebe as LINHAS, não o arquivo de novo: o que entra é exatamente o que
    foi mostrado na tela. Reenviar o arquivo abriria a porta para a prévia
    mostrar uma coisa e a gravação fazer outra.
    """
    _limitar_escrita(ctx)
    usuario_id = ctx.usuario["id"]
    gravadas, recusadas = [], []

    for linha in dados.linhas:
        try:
            if dados.modo == "substituir":
                # Substituir posição que não existe é criar: `atualizar`
                # devolve None nesse caso, e aí caímos em `adicionar`.
                posicao = carteira.atualizar(usuario_id, linha.ticker,
                                             linha.quantidade, linha.preco_medio)
                if posicao is None:
                    posicao, _ = carteira.adicionar(usuario_id, linha.ticker,
                                                    linha.quantidade, linha.preco_medio)
            else:
                posicao, _ = carteira.adicionar(usuario_id, linha.ticker,
                                                linha.quantidade, linha.preco_medio)
            gravadas.append(posicao["ticker"])
        except carteira.ErroCarteira as erro:
            # Uma linha ruim não derruba o lote: o resto entra e a tela diz
            # qual falhou. Abortar tudo por causa de uma linha obrigaria a
            # pessoa a corrigir a planilha e recomeçar do zero.
            recusadas.append({"ticker": linha.ticker, "motivo": str(erro)})

    return {"gravadas": len(gravadas), "tickers": gravadas,
            "recusadas": recusadas, "modo": dados.modo}


@router.delete("/api/v1/carteira/item/{ticker}")
def remover_item(ticker: str, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    if not carteira.remover(ctx.usuario["id"], ticker):
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado",
            "motivo": f"{carteira.normalizar_ticker(ticker)} não está na carteira."})
    return {"removido": carteira.normalizar_ticker(ticker)}
