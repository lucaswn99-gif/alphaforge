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
from fastapi.responses import HTMLResponse  # noqa: E402

from routers import equity, fixed_income, wealth  # noqa: E402

# O router `trading` está fora da aplicação de propósito. Ele dependia do
# MetaTrader 5 (Windows-only, inerte no servidor) e expunha /executar-ordem,
# que devolvia "ORDEM_ENVIADA_AO_ROTEADOR" sem enviar ordem a lugar nenhum.
# O arquivo continua no repositório; para reativar, reinclua o import e o
# include_router abaixo — e devolva ccxt ao requirements.txt.

app = FastAPI(
    title="Alphaforge Analytics",
    description="Terminal Institucional Quantamental.",
    version="3.0.0",
)

app.include_router(fixed_income.router)
app.include_router(equity.router)
app.include_router(wealth.router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
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
