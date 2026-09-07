import os
from dotenv import load_dotenv

# Precisa vir ANTES dos imports dos routers, porque fixed_income.py lê
# GEMINI_API_KEY do ambiente assim que o módulo é importado.
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from routers import fixed_income, equity, trading, wealth

app = FastAPI(
    title="Alphaforge Analytics",
    description="Terminal Institucional Quantamental.",
    version="2.0.0"
)
app.include_router(fixed_income.router)
app.include_router(equity.router)
app.include_router(trading.router)
app.include_router(wealth.router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "templates", "index.html")


@app.get("/", response_class=HTMLResponse)
def interface_dashboard():
    if os.path.exists(HTML_PATH):
        with open(HTML_PATH, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Erro: templates/index.html não encontrado.</h1>"