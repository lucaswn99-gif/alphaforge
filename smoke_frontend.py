"""Smoke test: sobe a API com motores falsos e navega a aba Filosofias de
verdade num Chromium headless, verificando que as quatro sub-abas renderizam
sem erro de console e com os dados esperados.
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["AF_WEBHOOK_SEGREDO"] = "segredo-de-teste"
from modules import contas
contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_smoke.db")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from routers import conta as rota_conta
from routers import filosofias as rota_filo
from modules import filosofias as mod_filo


class MotorFalso:
    def satelite_barsi(self):
        return {
            "filosofia": "barsi", "gerado_em_legivel": "12/09/2026 10:00",
            "aprovados": [{
                "ticker": "TAEE11", "setor": "energia", "preco": 30.0,
                "preco_teto": 36.67, "margem_seguranca": 0.182,
                "payout": 70.3, "divida_liquida_ebit": 2.1,
                "tendencia_dpa": {"classificacao": "crescente", "cagr_aa": 0.095,
                                  "anos_considerados": 3, "consistencia": "sempre_subiu",
                                  "motivo": None},
                "momentum": {"veredito": "favoravel", "momentum_12m_1m": 0.18, "motivo": "Momentum de 18%."},
                "aprovado": True, "motivos": [],
            }],
            "reprovados": [{
                "ticker": "EGIE3", "setor": "energia", "preco": 36.0,
                "preco_teto": 36.67, "margem_seguranca": 0.018,
                "payout": 70.3, "divida_liquida_ebit": 2.1,
                "tendencia_dpa": {"classificacao": "estavel", "cagr_aa": 0.0,
                                  "anos_considerados": 3, "consistencia": "com_oscilacao",
                                  "motivo": None},
                "momentum": {"veredito": "rebaixar", "momentum_12m_1m": -0.05, "motivo": "Momentum negativo."},
                "aprovado": False, "motivos": ["Margem de segurança de 1.8% — mínimo 10%."],
            }],
            "ressalvas": [], "criterios": {},
        }

    def satelite_greenblatt(self, universo=None):
        return {
            "filosofia": "greenblatt",
            "ranking": [{
                "ticker": "BOA", "setor": "Technology", "preco": 100.0,
                "ev_ebit": 5.5, "roic": 100.0, "shareholder_yield": 7.0,
                "posto_combinado": 2, "penalizado_por_momentum": False,
                "momentum": {"veredito": "favoravel", "momentum_12m_1m": 0.12, "motivo": "Momentum de 12%."},
            }],
            "descartados": [{"ticker": "X"}], "ressalvas": [],
            "avaliados": len(universo or []),
        }

    def nucleo_bogle(self, carteira, alvo=None, banda=0.05):
        return {
            "filosofia": "bogle", "patrimonio_brl": 50000.0, "dolar": 5.0,
            "precisa_rebalancear": True,
            "posicoes": [
                {"ticker": "VOO", "peso_atual": 60.0, "peso_alvo": 50.0,
                 "desvio_pp": 10.0, "acao": "vender", "ajuste_brl": -5000.0},
                {"ticker": "WRLD11.SA", "peso_atual": 40.0, "peso_alvo": 50.0,
                 "desvio_pp": -10.0, "acao": "comprar", "ajuste_brl": 5000.0},
            ],
            "ressalvas": [],
        }


class PainelFalso:
    def painel_json(self, lista=None):
        return [{
            "ativo": "AAPL", "bdr": "AAPL34", "preco_usd": 200.0, "preco_brl": 100.0,
            "var_pct_usd": 1.2, "var_pct_brl": 0.8, "preco_justo_brl": 100.0,
            "spread_pct": -0.19, "confiavel": True, "alertas": [],
        }]

    def carimbo(self):
        return {"gerado_em": "2026-09-12T10:00:00", "tabela_de_razoes_verificada_em": "2026-09-12"}


rota_filo.motor = lambda: MotorFalso()
rota_filo.painel = lambda: PainelFalso()

app = FastAPI()
app.include_router(rota_conta.router)
app.include_router(rota_filo.router)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ESTATICOS = os.path.join(BASE_DIR, "static")
if os.path.isdir(ESTATICOS):
    app.mount("/static", StaticFiles(directory=ESTATICOS), name="static")

HTML_PATH = os.path.join(BASE_DIR, "templates", "index.html")


@app.get("/", response_class=HTMLResponse)
def raiz():
    with open(HTML_PATH, "r", encoding="utf-8") as f:
        return f.read()


def rodar_servidor():
    uvicorn.run(app, host="127.0.0.1", port=8998, log_level="warning")


if __name__ == "__main__":
    t = threading.Thread(target=rodar_servidor, daemon=True)
    t.start()
    time.sleep(1.5)

    from playwright.sync_api import sync_playwright

    erros_console = []
    falhas = []

    def checar(rotulo, condicao, extra=""):
        if condicao:
            print(f"  ok   {rotulo}")
        else:
            print(f"  FALHA {rotulo} {extra}")
            falhas.append(rotulo)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
        page = browser.new_page()
        page.on("console", lambda msg: erros_console.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda exc: erros_console.append(str(exc)))

        page.goto("http://127.0.0.1:8998/", wait_until="networkidle")
        checar("página carregou (título)", "AlphaForge" in page.title(), page.title())

        # Premium: sem isso `planos.cortar_barsi` esconde os reprovados e
        # corta a lista, e o teste estaria medindo o corte, não a renderização.
        # `page.request` compartilha cookies com a página — por isso o registro
        # sai daqui, não de `requests` solto (que não veria a sessão).
        page.request.post("http://127.0.0.1:8998/conta/registrar",
                          data={"email": "smoke@teste.com", "senha": "senha-boa-123"})
        from modules import contas as _contas
        _contas.definir_plano(_contas.autenticar("smoke@teste.com", "senha-boa-123")["id"], "premium")
        page.reload(wait_until="networkidle")

        page.click("#btn-tab-filosofias")
        checar("aba Filosofias fica visível",
               page.eval_on_selector("#tab-filosofias", "el => !el.classList.contains('hidden')"))

        # -------- Bogle (sub-aba padrão) --------
        page.fill("#bogleposicoes", "VOO:10,WRLD11.SA:250")
        page.click("#btnBogle")
        page.wait_for_selector("#painelBogleResultado:not(.hidden)", timeout=5000)
        linhas_bogle = page.eval_on_selector_all("#tabelaBogle tr", "els => els.length")
        checar("Bogle renderizou 2 linhas", linhas_bogle == 2, linhas_bogle)
        checar("Bogle mostra patrimônio", "50.000" in page.inner_text("#boglePatrimonio")
               or "50,000" in page.inner_text("#boglePatrimonio"), page.inner_text("#boglePatrimonio"))

        # -------- Barsi --------
        page.click("#btn-sub-barsi")
        page.wait_for_selector("#tabelaBarsi tr", timeout=5000)
        linhas_barsi = page.eval_on_selector_all("#tabelaBarsi tr", "els => els.length")
        checar("Barsi renderizou aprovado + reprovado", linhas_barsi == 2, linhas_barsi)
        texto_barsi = page.inner_text("#tabelaBarsi")
        checar("Barsi mostra o ticker aprovado", "TAEE11" in texto_barsi)
        checar("Barsi mostra badge de tendência DPA", "DPA ↑" in texto_barsi, texto_barsi)

        # -------- Greenblatt --------
        page.click("#btn-sub-greenblatt")
        page.wait_for_selector("#tabelaGreenblatt tr", timeout=5000)
        texto_gb = page.inner_text("#tabelaGreenblatt")
        checar("Greenblatt mostra o ticker do ranking", "BOA" in texto_gb, texto_gb)

        # -------- BDR --------
        page.click("#btn-sub-bdr")
        page.wait_for_selector("#tabelaBDR tr", timeout=5000)
        texto_bdr = page.inner_text("#tabelaBDR")
        checar("BDR mostra o ativo", "AAPL" in texto_bdr, texto_bdr)
        checar("BDR mostra a data de verificação da tabela",
               page.inner_text("#bdrVerificadoEm") == "2026-09-12", page.inner_text("#bdrVerificadoEm"))

        # Filtra ruído deste harness mínimo: ele só sobe os routers de conta e
        # filosofias, então mercado/ticker-tape/ícones 404 de propósito — e o
        # Tailwind/Google Fonts não respondem porque o sandbox não tem rede
        # externa. Nenhum desses vem do código de Filosofias. O que importa é
        # não ter erro de execução JS de verdade (ReferenceError, TypeError…).
        ruido = ("ERR_TUNNEL_CONNECTION_FAILED", "status of 404",
                "bad HTTP response code (404)")
        erros_reais = [e for e in erros_console if not any(r in e for r in ruido)]
        checar("nenhum erro de execução JS", len(erros_reais) == 0, erros_reais)

        browser.close()

    print()
    if falhas:
        print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
        sys.exit(1)
    print("Tudo passou (smoke de frontend).")
