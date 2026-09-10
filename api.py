"""Alphaforge Analytics — terminal quantamental.

Ponto de entrada da API. Sobe com:

    uvicorn api:app --host 0.0.0.0 --port $PORT
"""

import os

from dotenv import load_dotenv

# Antes dos imports dos routers: fixed_income lê GEMINI_API_KEY do ambiente no
# momento em que o módulo é importado.
load_dotenv()

from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from modules import contas  # noqa: E402
from routers import (conta, equity, fixed_income, legal, mercado,  # noqa: E402
                     opcoes, quantitativo, wealth)

# O router `trading` está fora da aplicação de propósito. Ele dependia do
# MetaTrader 5 (Windows-only, inerte no servidor) e expunha /executar-ordem,
# que devolvia "ORDEM_ENVIADA_AO_ROTEADOR" sem enviar ordem a lugar nenhum.
# O arquivo continua no repositório; para reativar, reinclua o import e o
# include_router abaixo — e devolva ccxt ao requirements.txt.

app = FastAPI(
    title="Alphaforge Analytics",
    description="Terminal Institucional Quantamental.",
    version="3.5.0",
)

app.include_router(fixed_income.router)
app.include_router(equity.router)
app.include_router(wealth.router)
app.include_router(mercado.router)
app.include_router(quantitativo.router)
app.include_router(opcoes.router)
app.include_router(legal.router)
app.include_router(conta.router)


@app.on_event("startup")
def preparar_contas():
    """Cria o esquema e varre sessões vencidas. Falhar aqui não pode
    impedir a API de subir: sem banco de contas, todo mundo é gratuito."""
    try:
        contas.iniciar()
        contas.limpar_expirados()
    except Exception as erro:  # noqa: BLE001
        print(f"[contas] esquema indisponível: {erro}")


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ESTATICOS = os.path.join(BASE_DIR, "static")
if os.path.isdir(ESTATICOS):
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")


@app.get("/sw.js")
def serviço_worker():
    """O service worker precisa ser servido da RAIZ.

    Um SW só controla o escopo a partir da própria pasta: servido de
    /static/sw.js ele controlaria apenas /static, e o app instalado não abriria
    offline. Por isso o arquivo mora em static/ e é publicado aqui em /."""
    caminho = os.path.join(ESTATICOS, "sw.js")
    if not os.path.exists(caminho):
        return {"erro": "service worker não encontrado"}
    return FileResponse(caminho, media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})
HTML_PATH = os.path.join(BASE_DIR, "templates", "index.html")


@app.get("/", response_class=HTMLResponse)
def interface_dashboard():
    if os.path.exists(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as arquivo:
            return arquivo.read()
    return "<h1>Erro: templates/index.html não encontrado.</h1>"


@app.get("/health")
def health():
    """Liveness para o serviço de deploy. Não toca em fonte externa: health
    check que depende do Yahoo derruba o serviço quando o Yahoo oscila."""
    return {"status": "ok", "versao": app.version,
            "gemini_configurado": bool(os.environ.get("GEMINI_API_KEY"))}
