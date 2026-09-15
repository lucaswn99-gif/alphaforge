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
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from modules import (alvos, backtest, carteira, diagnostico, estresse,
                     fundos, importacao, mandato, perfil, planos, projecao,
                     radar, rebalanceamento, relatorio, renda_fixa, taxas)

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

    Cinco estados: `nao_apurado` existe para o que não deu para medir, em vez
    de virar desconformidade, e `sem_filosofia` existe para quem escolheu
    deliberadamente não ser julgado por nenhuma das três teses — os dois não
    são a mesma pendência. Ver `modules/diagnostico.py`.
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
    resultado = diagnostico.diagnosticar(
        rota_filosofias.motor(), dados["posicoes"],
        filosofia_carteira=mandato.obter(usuario_id))
    _cache_diagnostico[chave] = (time.time(), resultado)
    return {**resultado, "cache": False, "idade_segundos": 0}


# ------------------------------------------- filosofia declarada pelo investidor

class EscolhaFilosofia(BaseModel):
    filosofia: str = Field(..., max_length=32)


class FilosofiaDoPapel(BaseModel):
    # "herdar" e None voltam a seguir a carteira. São estados distintos de
    # "escolhi a mesma filosofia da carteira": trocar a da carteira depois
    # precisa arrastar quem herda.
    filosofia: str | None = Field(None, max_length=32)


@router.get("/api/v1/carteira/filosofia")
def obter_filosofia(ctx: planos.Contexto = Depends(_vip)):
    escolhida = mandato.obter(ctx.usuario["id"])
    return {
        "filosofia": escolhida,
        "definida": escolhida is not None,
        "opcoes": [{"chave": chave, "rotulo": mandato.ROTULOS[chave],
                    "resumo": mandato.RESUMOS[chave]}
                   for chave in mandato.FILOSOFIAS],
    }


@router.put("/api/v1/carteira/filosofia")
def definir_filosofia(corpo: EscolhaFilosofia,
                      ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    try:
        escolhida = mandato.definir(ctx.usuario["id"], corpo.filosofia)
    except mandato.ErroMandato as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "filosofia_invalida", "motivo": str(erro)})
    # Trocar a lente invalida todo veredito guardado: o cache é por assinatura
    # das posições, que não muda quando só a filosofia muda.
    _cache_diagnostico.clear()
    return {"filosofia": escolhida, "definida": True}


@router.put("/api/v1/carteira/item/{ticker}/filosofia")
def definir_filosofia_do_papel(ticker: str, corpo: FilosofiaDoPapel,
                               ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    try:
        achou = mandato.definir_do_papel(ctx.usuario["id"], ticker, corpo.filosofia)
    except mandato.ErroMandato as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "filosofia_invalida", "motivo": str(erro)})
    if not achou:
        raise HTTPException(status_code=404, detail={
            "erro": "posicao_inexistente",
            "motivo": f"{ticker.upper()} não está na sua carteira."})
    _cache_diagnostico.clear()
    return {"ticker": ticker.upper(), "filosofia": corpo.filosofia or None}


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


# ------------------------------------------------- Pilar 4: estresse macro

@router.get("/api/v1/carteira/estresse/selic")
def estressar_selic(taxa: float = None, ctx: planos.Contexto = Depends(_vip)):
    """Se a Selic for para `taxa`, o EBIT de cada companhia ainda paga juros?

    Rota barata de propósito: usa só o balanço já gravado em SQLite, sem tocar
    em rede. É ela que move o controle deslizante da tela, e uma consulta ao
    Yahoo por arrasto tornaria o controle inutilizável.
    """
    usuario_id = ctx.usuario["id"]
    dados = carteira.listar(usuario_id)
    if not dados["posicoes"]:
        raise HTTPException(status_code=422, detail={
            "erro": "carteira_vazia",
            "motivo": "Cadastre suas posições antes de simular."})

    meta = taxas.obter_selic_meta()
    atual = meta["valor"]
    nova = atual if taxa is None else taxa

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()
    balancos = {}
    for posicao in dados["posicoes"]:
        if (posicao.get("classe") or "") == "acao":
            balancos[posicao["ticker"]] = motor._balanco_cvm(posicao["ticker"]) or {}

    resultado = estresse.sensibilidade_selic(
        dados["posicoes"], balancos, atual, nova)
    return {**resultado, "selic_meta": meta}


# O histórico baixa a série INTEIRA de cada papel — a janela de 2008 exige
# dezoito anos de pregão. Caro o bastante para guardar por usuário, com o mesmo
# TTL do resto do projeto.
_cache_historico = {}


@router.get("/api/v1/carteira/estresse/historico")
def estressar_historico(forcar: bool = False,
                        ctx: planos.Contexto = Depends(_vip)):
    """Quanto a carteira de hoje teria caído em cada evento de cauda.

    É contrafactual declarado, não previsão: aplica os PESOS DE HOJE a preços
    do passado. Serve para medir a fragilidade da composição atual, não para
    dizer o que vai acontecer.
    """
    usuario_id = ctx.usuario["id"]
    dados = carteira.listar(usuario_id)
    if not dados["posicoes"]:
        raise HTTPException(status_code=422, detail={
            "erro": "carteira_vazia",
            "motivo": "Cadastre suas posições antes de simular."})

    assinatura = tuple(sorted((p["ticker"], p["quantidade"])
                              for p in dados["posicoes"]))
    chave = (usuario_id, assinatura)
    agora = time.time()
    guardado = _cache_historico.get(chave)
    if guardado and not forcar and agora - guardado[0] < _CACHE_TTL:
        return {**guardado[1], "cache": True,
                "idade_segundos": int(agora - guardado[0])}

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()
    series = _series_completas(motor, [p["ticker"] for p in dados["posicoes"]])

    # Peso por CUSTO, e não por valor de mercado: buscar a cotação de hoje de
    # cada papel dobraria o custo da rota, e a diferença de peso não muda a
    # leitura de "quanto esta composição caiu".
    pesos = {p["ticker"]: (p.get("custo_total") or 0.0) for p in dados["posicoes"]}

    janelas = []
    for evento in estresse.EVENTOS:
        recorte = {t: _recortar(s, evento["inicio"], evento["fim"])
                   for t, s in series.items()}
        janelas.append({**evento,
                        **estresse.estresse_historico(recorte, pesos)})

    resultado = {"eventos": janelas, "avaliadas": len(dados["posicoes"]),
                 "aviso": ("Contrafactual: aplica os pesos de HOJE a preços do "
                           "passado. Mede a fragilidade da composição atual, "
                           "não o que vai acontecer.")}
    _cache_historico[chave] = (time.time(), resultado)
    return {**resultado, "cache": False, "idade_segundos": 0}


def _series_completas(motor, tickers):
    """{ticker: série de fechamentos} com o histórico inteiro disponível."""
    import concurrent.futures

    def buscar(ticker):
        try:
            return ticker, motor.fonte.precos(f"{ticker}.SA", periodo="max")
        except Exception:  # noqa: BLE001
            return ticker, None

    series = {}
    if not tickers:
        return series
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=diagnostico.MAX_WORKERS) as pool:
        for ticker, serie in pool.map(buscar, tickers):
            if serie is not None and len(serie):
                series[ticker] = serie
    return series


# --------------------------------------------------- Backtest de 12 meses

_cache_backtest = {}


@router.get("/api/v1/carteira/backtest")
def backtest_carteira(forcar: bool = False, ctx: planos.Contexto = Depends(_vip)):
    """Série de valor da carteira INTEIRA (ação/FII/ETF + renda fixa + fundo)
    nos últimos 12 meses, e o retorno acumulado do período.

    Ação/FII/ETF entram pelo preço histórico; renda fixa pelo replay da
    fórmula do indexador contratado; fundo pela cota diária da CVM (Etapa B
    de `modules/fundos.py`) — nunca por leitura de mercado. Só entra quem tem
    histórico para a janela inteira (mesmo critério do Pilar 4); quem fica de
    fora aparece em `sem_historico`, sem sumir calado.
    """
    usuario_id = ctx.usuario["id"]
    posicoes_acao = carteira.listar(usuario_id)["posicoes"]
    posicoes_rf = renda_fixa.listar(usuario_id)["posicoes"]
    posicoes_fundos = fundos.listar(usuario_id)["posicoes"]

    if not posicoes_acao and not posicoes_rf and not posicoes_fundos:
        raise HTTPException(status_code=422, detail={
            "erro": "carteira_vazia",
            "motivo": "Cadastre ao menos uma posição — ação, FII, ETF, renda fixa ou fundo."})

    assinatura = (tuple(sorted((p["ticker"], p["quantidade"]) for p in posicoes_acao)),
                 tuple(sorted((p["id"], p["valor_aplicado"]) for p in posicoes_rf)),
                 tuple(sorted((p["id"], p["valor_atual"]) for p in posicoes_fundos)))
    chave = (usuario_id, assinatura)
    agora = time.time()
    guardado = _cache_backtest.get(chave)
    if guardado and not forcar and agora - guardado[0] < _CACHE_TTL:
        return {**guardado[1], "cache": True, "idade_segundos": int(agora - guardado[0])}

    hoje = date.today()
    datas = backtest.pontos_mensais(hoje, meses=12)

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()
    series_precos = _series_completas(motor, [p["ticker"] for p in posicoes_acao])
    precos_por_ticker = {ticker: _recortar_com_datas(serie, datas[0], hoje)
                         for ticker, serie in series_precos.items()}

    # Peso por CUSTO para ação/FII/ETF (mesmo critério do Pilar 4 — buscar a
    # cotação de hoje de cada papel só para isso dobraria o custo da rota);
    # peso por VALOR ATUAL (marcação na curva) para renda fixa, que já foi
    # calculado sem custo extra por `renda_fixa.listar`.
    acoes_entrada = [{"ticker": p["ticker"], "peso": p.get("custo_total") or 0.0}
                     for p in posicoes_acao]
    rf_entrada = [{"identificador": p["emissor"], "peso": p["valor_atual"],
                  "data_aplicacao": date.fromisoformat(p["data_aplicacao"]),
                  "indexador": p["indexador"], "taxa": p["taxa"]}
                 for p in posicoes_rf]
    # Peso por VALOR ATUAL — mesmo critério de renda fixa acima; sem cota
    # coletada ainda vira o aplicado (fundos.listar já resolve isso).
    fundos_entrada = [{"identificador": p["nome_fundo"], "peso": p["valor_atual"],
                      "cnpj": p["cnpj"]}
                     for p in posicoes_fundos]

    resultado = backtest.carteira_12_meses(datas, acoes_entrada, precos_por_ticker,
                                           rf_entrada, posicoes_fundos=fundos_entrada)
    resultado["aviso"] = (
        "Simulação, não cotação: ação/FII/ETF pelo preço histórico, renda "
        "fixa pelo replay da fórmula do indexador contratado, fundo pela "
        "cota diária da CVM. Rentabilidade passada não garante rentabilidade "
        "futura.")
    _cache_backtest[chave] = (time.time(), resultado)
    return {**resultado, "cache": False, "idade_segundos": 0}


# ------------------------------------------------- Pilar 5: radar de 5 eixos

_cache_radar = {}


@router.get("/api/v1/carteira/radar")
def radar_da_carteira(forcar: bool = False, ctx: planos.Contexto = Depends(_vip)):
    """Cinco eixos por papel e o agregado da carteira.

    Só ação entra. FII não tem ROE nem produto P/L × P/VP, e ETF é cesta de
    índice — dar nota a eles em eixos de companhia produziria número com cara
    de medida e conteúdo de ruído.
    """
    usuario_id = ctx.usuario["id"]
    dados = carteira.listar(usuario_id)
    acoes = [p for p in dados["posicoes"] if (p.get("classe") or "") == "acao"]
    if not acoes:
        raise HTTPException(status_code=422, detail={
            "erro": "sem_acoes",
            "motivo": ("O radar mede companhias. Cadastre ao menos uma ação — "
                       "FII e ETF não têm ROE nem múltiplo de empresa.")})

    assinatura = tuple(sorted(p["ticker"] for p in acoes))
    chave = (usuario_id, assinatura)
    agora = time.time()
    guardado = _cache_radar.get(chave)
    if guardado and not forcar and agora - guardado[0] < _CACHE_TTL:
        return {**guardado[1], "cache": True,
                "idade_segundos": int(agora - guardado[0])}

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()

    def medir(ticker):
        try:
            graham = motor._avaliar_graham(ticker, aplicar_momentum=False)
        except Exception:  # noqa: BLE001
            graham = None
        try:
            bazin = motor._avaliar_bazin(ticker)
        except Exception:  # noqa: BLE001
            bazin = None
        try:
            tendencia = motor.momentum.avaliar(f"{ticker}.SA")
        except Exception:  # noqa: BLE001
            tendencia = None
        balanco = motor._balanco_cvm(ticker) or {}
        lucros, anos = motor._historico_de_lucro(ticker)
        com_lucro = sum(1 for v in (lucros or []) if v is not None and v > 0)
        return radar.radar_do_papel(
            ticker, graham=graham, balanco=balanco, bazin=bazin,
            momentum=tendencia, exercicios_com_lucro=com_lucro,
            exercicios_apurados=anos)

    import concurrent.futures
    radares = []
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=diagnostico.MAX_WORKERS) as pool:
        for papel in pool.map(medir, [p["ticker"] for p in acoes]):
            radares.append(papel)

    pesos = {p["ticker"]: (p.get("custo_total") or 0.0) for p in acoes}
    agregado = radar.radar_da_carteira(radares, pesos)

    resultado = {**agregado, "papeis_detalhe": radares, "eixos_ordem": list(radar.EIXOS)}
    _cache_radar[chave] = (time.time(), resultado)
    return {**resultado, "cache": False, "idade_segundos": 0}


def _recortar(serie, inicio, fim):
    """Fechamentos da janela, em ordem. Lista vazia quando não há histórico."""
    try:
        import pandas as pd
        indice = pd.to_datetime(serie.index)
        if getattr(indice, "tz", None) is not None:
            indice = indice.tz_localize(None)
        dentro = (indice >= pd.Timestamp(inicio)) & (indice <= pd.Timestamp(fim))
        return [float(v) for v in serie.values[dentro]]
    except Exception:  # noqa: BLE001
        return []


def _recortar_com_datas(serie, inicio, fim):
    """[(data, fechamento), ...] da janela, em ordem — como `_recortar`, mas
    com a data de cada ponto (o backtest precisa localizar o fechamento mais
    próximo de cada mês, não só a lista de valores)."""
    try:
        import pandas as pd
        indice = pd.to_datetime(serie.index)
        if getattr(indice, "tz", None) is not None:
            indice = indice.tz_localize(None)
        dentro = (indice >= pd.Timestamp(inicio)) & (indice <= pd.Timestamp(fim))
        datas = indice[dentro]
        valores = serie.values[dentro]
        return [(d.date(), float(v)) for d, v in zip(datas, valores)]
    except Exception:  # noqa: BLE001
        return []


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


# --------------------------------------------------------------------------
# Renda fixa privada (CDB, LCI, LCA, CRI, CRA, debênture, LF, LCD)
#
# Não tem ticker nem cotação: tabela própria (`renda_fixa`, em contas.py),
# não uma linha em `carteiras`. "Valor atual" é marcação na curva — calculado
# por `modules/renda_fixa.py` a partir do indexador contratado — e não uma
# leitura de mercado. Ver a docstring daquele módulo para o porquê.
# --------------------------------------------------------------------------

class ItemRendaFixa(BaseModel):
    emissor: str = Field(..., max_length=120)
    tipo: str
    indexador: str
    taxa: float
    data_aplicacao: str
    data_vencimento: Optional[str] = None
    valor_aplicado: float


@router.get("/api/v1/carteira/renda-fixa/parametros")
def parametros_renda_fixa():
    """Tipos e indexadores aceitos, para a tela montar os seletores sem
    cravar a lista duas vezes (front e back divergindo é bug de digitação)."""
    return {
        "tipos": [{"chave": t, "rotulo": renda_fixa.ROTULOS_TIPO[t]}
                 for t in renda_fixa.TIPOS],
        "indexadores": [{"chave": i, "rotulo": renda_fixa.ROTULOS_INDEXADOR[i]}
                        for i in renda_fixa.INDEXADORES],
    }


@router.get("/api/v1/carteira/renda-fixa")
def listar_renda_fixa(ctx: planos.Contexto = Depends(_vip)):
    """Posições marcadas na curva: valor aplicado, valor atual e a conta que
    levou de um ao outro, escrita por extenso em `marcacao`."""
    return renda_fixa.listar(ctx.usuario["id"])


@router.post("/api/v1/carteira/renda-fixa", status_code=201)
def criar_renda_fixa(item: ItemRendaFixa, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    try:
        linha = renda_fixa.adicionar(
            ctx.usuario["id"], item.emissor, item.tipo, item.indexador,
            item.taxa, item.data_aplicacao, item.data_vencimento,
            item.valor_aplicado)
    except renda_fixa.ErroRendaFixa as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    return {"posicao": linha}


@router.patch("/api/v1/carteira/renda-fixa/{posicao_id}")
def editar_renda_fixa(posicao_id: int, item: ItemRendaFixa,
                      ctx: planos.Contexto = Depends(_vip)):
    """Corrige a posição — SUBSTITUI os valores, mesma lógica de `editar_item`."""
    _limitar_escrita(ctx)
    try:
        linha = renda_fixa.atualizar(
            ctx.usuario["id"], posicao_id, item.emissor, item.tipo,
            item.indexador, item.taxa, item.data_aplicacao,
            item.data_vencimento, item.valor_aplicado)
    except renda_fixa.ErroRendaFixa as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    if linha is None:
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado", "motivo": "Posição não encontrada."})
    return {"posicao": linha}


@router.delete("/api/v1/carteira/renda-fixa/{posicao_id}")
def remover_renda_fixa(posicao_id: int, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    if not renda_fixa.remover(ctx.usuario["id"], posicao_id):
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado", "motivo": "Posição não encontrada."})
    return {"removido": posicao_id}


# --------------------------------------------------------------------------
# Fundos de investimento — Etapa A (ver modules/fundos.py). Posição simples
# por cotas x valor da cota; sem cota diária ainda, então "valor atual" é
# sempre o aplicado — nunca uma rentabilidade inventada.
# --------------------------------------------------------------------------

class ItemFundo(BaseModel):
    nome_fundo: str = Field(..., max_length=150)
    cnpj: str
    classe: str
    numero_cotas: float
    valor_cota_aplicacao: float
    data_aplicacao: str


@router.get("/api/v1/carteira/fundos/parametros")
def parametros_fundos():
    """Classes aceitas, para a tela montar o seletor sem cravar a lista
    duas vezes."""
    return {"classes": [{"chave": c, "rotulo": fundos.ROTULOS_CLASSE[c]}
                        for c in fundos.CLASSES]}


@router.get("/api/v1/carteira/fundos")
def listar_fundos(ctx: planos.Contexto = Depends(_vip)):
    return fundos.listar(ctx.usuario["id"])


@router.post("/api/v1/carteira/fundos", status_code=201)
def criar_fundo(item: ItemFundo, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    try:
        linha = fundos.adicionar(
            ctx.usuario["id"], item.nome_fundo, item.cnpj, item.classe,
            item.numero_cotas, item.valor_cota_aplicacao, item.data_aplicacao)
    except fundos.ErroFundo as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    return {"posicao": linha}


@router.patch("/api/v1/carteira/fundos/{posicao_id}")
def editar_fundo(posicao_id: int, item: ItemFundo,
                ctx: planos.Contexto = Depends(_vip)):
    """Corrige a posição — SUBSTITUI os valores, mesma lógica de `editar_item`."""
    _limitar_escrita(ctx)
    try:
        linha = fundos.atualizar(
            ctx.usuario["id"], posicao_id, item.nome_fundo, item.cnpj,
            item.classe, item.numero_cotas, item.valor_cota_aplicacao,
            item.data_aplicacao)
    except fundos.ErroFundo as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})
    if linha is None:
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado", "motivo": "Posição não encontrada."})
    return {"posicao": linha}


@router.delete("/api/v1/carteira/fundos/{posicao_id}")
def remover_fundo(posicao_id: int, ctx: planos.Contexto = Depends(_vip)):
    _limitar_escrita(ctx)
    if not fundos.remover(ctx.usuario["id"], posicao_id):
        raise HTTPException(status_code=404, detail={
            "erro": "nao_encontrado", "motivo": "Posição não encontrada."})
    return {"removido": posicao_id}


# --------------------------------------------------------------------------
# Perfil do investidor e objetivo
#
# Campo simples, atribuído pelo assessor — sem questionário de suitability
# próprio (ver `modules/perfil.py`). Uma linha por usuário, igual à
# filosofia; sem padrão para perfil nem para objetivo.
# --------------------------------------------------------------------------

class DefinicaoPerfil(BaseModel):
    perfil: Optional[str] = None
    objetivo: Optional[str] = None
    meta_retirada_mensal: Optional[float] = None
    horizonte_anos: Optional[int] = None
    meta_patrimonio: Optional[float] = None
    meta_renda_mensal: Optional[float] = None


@router.get("/api/v1/carteira/perfil")
def obter_perfil(ctx: planos.Contexto = Depends(_vip)):
    """Perfil e objetivo atuais — tudo `None` quando o assessor ainda não
    classificou nada."""
    return perfil.obter(ctx.usuario["id"])


@router.put("/api/v1/carteira/perfil")
def definir_perfil(corpo: DefinicaoPerfil, ctx: planos.Contexto = Depends(_vip)):
    """Grava perfil e objetivo inteiros — troca de objetivo não deixa resto
    do objetivo anterior (ver docstring de `modules/perfil.definir`)."""
    _limitar_escrita(ctx)
    try:
        return perfil.definir(
            ctx.usuario["id"], corpo.perfil, corpo.objetivo,
            corpo.meta_retirada_mensal, corpo.horizonte_anos,
            corpo.meta_patrimonio, corpo.meta_renda_mensal)
    except perfil.ErroPerfil as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})


# --------------------------------------------------- Projeção de capital

class PremissasProjecao(BaseModel):
    """Nenhum campo tem padrão — a tela sempre manda os três, nunca herda um
    valor invisível do servidor (ver docstring de `modules/projecao`)."""
    taxa_anual_pct: float
    aporte_mensal: float
    horizonte_anos: int


@router.post("/api/v1/carteira/projecao")
def projetar_carteira(corpo: PremissasProjecao, ctx: planos.Contexto = Depends(_vip)):
    """Projeção hipotética de capital a partir do patrimônio ATUAL da
    carteira (ação/FII/ETF pelo custo, renda fixa pela marcação na curva) —
    nunca escreve nada, só calcula em cima das premissas que a tela mandou.
    """
    usuario_id = ctx.usuario["id"]
    try:
        taxa_anual_pct, aporte_mensal, horizonte_anos = projecao.validar(
            corpo.taxa_anual_pct, corpo.aporte_mensal, corpo.horizonte_anos)
    except projecao.ErroProjecao as erro:
        raise HTTPException(status_code=422, detail={
            "erro": "entrada_invalida", "motivo": str(erro)})

    # Fundos entram pelo valor aplicado (Etapa A, sem cota diária ainda — ver
    # modules/fundos.py): "entra no patrimônio total" é literal, no sentido
    # do documento de escopo, mesmo sem render próprio no backtest ainda.
    valor_inicial = (carteira.listar(usuario_id)["custo_total"]
                     + renda_fixa.listar(usuario_id)["valor_atual_total"]
                     + fundos.listar(usuario_id)["valor_atual_total"])

    if valor_inicial <= 0 and aporte_mensal <= 0:
        raise HTTPException(status_code=422, detail={
            "erro": "nada_para_projetar",
            "motivo": ("Cadastre uma posição ou informe um aporte mensal — "
                      "sem nenhum dos dois não há o que projetar.")})

    resultado = projecao.projetar(valor_inicial, taxa_anual_pct, aporte_mensal,
                                  horizonte_anos)

    perfil_atual = perfil.obter(usuario_id)
    gaps = projecao.calcular_gaps(
        perfil_atual["objetivo"], perfil_atual["meta_retirada_mensal"],
        perfil_atual["meta_patrimonio"], perfil_atual["meta_renda_mensal"],
        resultado["valor_final"], resultado["renda_mensal_sustentavel_final"])

    return {**resultado, "gaps": gaps, "aviso": projecao.AVISO_HIPOTETICO,
           "objetivo": perfil_atual["objetivo"],
           "rotulo_objetivo": perfil_atual["rotulo_objetivo"]}


# --------------------------------------------------------------------------
# Relatório em PDF — junta composição, diagnóstico, backtest, estresse e
# projeção num único documento (ver modules/relatorio.py). Nada aqui é
# calculado de novo: cada seção reaproveita a MESMA função que já serve a
# tela, para nunca haver dois lugares com regra diferente para o mesmo
# número.
# --------------------------------------------------------------------------

class PremissasRelatorio(BaseModel):
    taxa_anual_pct: float
    aporte_mensal: float
    horizonte_anos: int


class PedidoRelatorio(BaseModel):
    # Lista vazia é uma escolha válida (nenhum cenário) — não um padrão: a
    # tela sempre manda a seleção explícita da tela de checkboxes.
    eventos_estresse: list[str] = Field(default_factory=list)
    # None: a tela de projeção não tinha premissa preenchida — a seção fica
    # de fora do PDF, nunca com taxa inventada (ver modules/projecao.py).
    projecao: Optional[PremissasRelatorio] = None


@router.get("/api/v1/carteira/relatorio/parametros")
def parametros_relatorio():
    """Cenários de estresse disponíveis, para a tela de checkboxes antes de
    gerar — mesma lista de `/estresse/historico`, sem recalcular nada."""
    return {"eventos_estresse": [{"chave": e["chave"], "nome": e["nome"]}
                                 for e in estresse.EVENTOS]}


@router.post("/api/v1/carteira/relatorio")
def gerar_relatorio(corpo: PedidoRelatorio, ctx: planos.Contexto = Depends(_vip)):
    usuario_id = ctx.usuario["id"]

    dados_acao = carteira.listar(usuario_id)
    dados_rf = renda_fixa.listar(usuario_id)
    dados_fundos = fundos.listar(usuario_id)
    posicoes_acao = dados_acao["posicoes"]

    patrimonio_total = (dados_acao["custo_total"] + dados_rf["valor_atual_total"]
                        + dados_fundos["valor_atual_total"])

    if (patrimonio_total <= 0 and not posicoes_acao and not dados_rf["posicoes"]
            and not dados_fundos["posicoes"]
            and (corpo.projecao is None or corpo.projecao.aporte_mensal <= 0)):
        raise HTTPException(status_code=422, detail={
            "erro": "nada_para_relatar",
            "motivo": ("Cadastre ao menos uma posição, ou informe um aporte "
                      "mensal na projeção, antes de gerar o relatório.")})

    filosofia_atual = mandato.obter(usuario_id)
    filosofia_dados = (
        {"chave": filosofia_atual, "rotulo": mandato.ROTULOS.get(filosofia_atual)}
        if filosofia_atual and filosofia_atual != mandato.NENHUMA else None)

    from routers import filosofias as rota_filosofias
    motor = rota_filosofias.motor()

    diagnostico_dados = (
        diagnostico.diagnosticar(motor, posicoes_acao,
                                 filosofia_carteira=filosofia_atual)
        if posicoes_acao else None)

    # Backtest: mesma conta de `backtest_carteira`, sem cache — o relatório é
    # gerado sob pedido, não vale a pena guardar o resultado por 15 minutos.
    backtest_dados = None
    if posicoes_acao or dados_rf["posicoes"] or dados_fundos["posicoes"]:
        hoje = date.today()
        datas = backtest.pontos_mensais(hoje, meses=12)
        series_precos = _series_completas(motor, [p["ticker"] for p in posicoes_acao])
        precos_por_ticker = {ticker: _recortar_com_datas(serie, datas[0], hoje)
                             for ticker, serie in series_precos.items()}
        acoes_entrada = [{"ticker": p["ticker"], "peso": p.get("custo_total") or 0.0}
                         for p in posicoes_acao]
        rf_entrada = [{"identificador": p["emissor"], "peso": p["valor_atual"],
                      "data_aplicacao": date.fromisoformat(p["data_aplicacao"]),
                      "indexador": p["indexador"], "taxa": p["taxa"]}
                     for p in dados_rf["posicoes"]]
        fundos_entrada = [{"identificador": p["nome_fundo"], "peso": p["valor_atual"],
                          "cnpj": p["cnpj"]}
                         for p in dados_fundos["posicoes"]]
        backtest_dados = backtest.carteira_12_meses(
            datas, acoes_entrada, precos_por_ticker, rf_entrada,
            posicoes_fundos=fundos_entrada)

    # Estresse: só os eventos que a tela de checkboxes mandou, e só se houver
    # ação para medir (FII e ETF não têm dívida de companhia no balanço).
    chaves_validas = {e["chave"] for e in estresse.EVENTOS}
    chaves_selecionadas = [c for c in corpo.eventos_estresse if c in chaves_validas]
    if not posicoes_acao:
        estresse_dados = {"eventos": [],
                          "motivo": "Nenhuma posição em ação para medir estresse."}
    elif not chaves_selecionadas:
        estresse_dados = {"eventos": [],
                          "motivo": "Nenhum cenário de estresse selecionado."}
    else:
        series = _series_completas(motor, [p["ticker"] for p in posicoes_acao])
        pesos = {p["ticker"]: (p.get("custo_total") or 0.0) for p in posicoes_acao}
        janelas = []
        for evento in estresse.EVENTOS:
            if evento["chave"] not in chaves_selecionadas:
                continue
            recorte = {t: _recortar(s, evento["inicio"], evento["fim"])
                      for t, s in series.items()}
            janelas.append({**evento, **estresse.estresse_historico(recorte, pesos)})
        estresse_dados = {"eventos": janelas}

    # Projeção: só entra com premissa explícita da própria tela de projeção —
    # nunca uma taxa calculada por este endpoint (ver modules/projecao.py).
    projecao_dados = None
    if corpo.projecao is not None:
        try:
            taxa, aporte, horizonte = projecao.validar(
                corpo.projecao.taxa_anual_pct, corpo.projecao.aporte_mensal,
                corpo.projecao.horizonte_anos)
        except projecao.ErroProjecao as erro:
            raise HTTPException(status_code=422, detail={
                "erro": "entrada_invalida", "motivo": str(erro)})
        resultado_projecao = projecao.projetar(patrimonio_total, taxa, aporte, horizonte)
        perfil_atual = perfil.obter(usuario_id)
        gaps = projecao.calcular_gaps(
            perfil_atual["objetivo"], perfil_atual["meta_retirada_mensal"],
            perfil_atual["meta_patrimonio"], perfil_atual["meta_renda_mensal"],
            resultado_projecao["valor_final"],
            resultado_projecao["renda_mensal_sustentavel_final"])
        projecao_dados = {
            **resultado_projecao, "gaps": gaps,
            "taxa_anual_pct": taxa, "aporte_mensal": aporte,
        }

    dados_pdf = {
        "cliente_email": ctx.usuario["email"],
        "gerado_em": datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC"),
        "filosofia": filosofia_dados,
        "composicao": {
            "acoes": posicoes_acao,
            "renda_fixa": dados_rf["posicoes"],
            "fundos": dados_fundos["posicoes"],
            "custo_total_acoes": dados_acao["custo_total"],
            "valor_atual_renda_fixa": dados_rf["valor_atual_total"],
            "valor_atual_fundos": dados_fundos["valor_atual_total"],
            "patrimonio_total": patrimonio_total,
        },
        "diagnostico": diagnostico_dados,
        "backtest": backtest_dados,
        "estresse": estresse_dados,
        "projecao": projecao_dados,
    }

    pdf_bytes = relatorio.montar(dados_pdf)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="relatorio-alphaforge.pdf"'})
