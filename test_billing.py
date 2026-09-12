"""Prova o caminho da assinatura sem tocar no Google.

`modules.play` é substituído por um dublê que responde como a API do Play
responderia, e a Digital Goods API é injetada no navegador antes da página
carregar. O que se testa é o que é nosso: se o token vira acesso só depois da
confirmação no servidor, se compra não reconhecida é reconhecida, se a
notificação repetida não é reprocessada, se cancelada mantém o acesso até o
vencimento e se expirada tira.
"""

if __name__ != "__main__":  # pragma: no cover
    # Isto é um script de verificação, não uma suíte pytest: sobe servidor (e,
    # no caso do billing, navegador) no corpo do módulo. Coletado pelo
    # `python -m pytest` que guarda o deploy, tudo isso rodaria durante a
    # COLETA — e uma máquina de CI sem navegador derrubaria o portão inteiro.
    # `allow_module_level` corta a coleta antes de qualquer linha abaixo.
    import pytest

    pytest.skip("script de verificação; rode com: python test_billing.py",
                allow_module_level=True)

import os
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

_tmp = tempfile.mkdtemp()
os.environ["AF_PLAY_SEGREDO"] = "segredo-play"

from modules import contas, play  # noqa: E402

contas.CAMINHO_BANCO = os.path.join(_tmp, "contas_billing.db")

# ------------------------------------------------------------------ dublê --
FUTURO = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
PASSADO = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

BANCO_PLAY = {
    "tok-ativo": {"subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
                  "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
                  "latestOrderId": "GPA.1", "lineItems": [
                      {"productId": "alphaforge_premium_mensal", "expiryTime": FUTURO}]},
    "tok-cancelada": {"subscriptionState": "SUBSCRIPTION_STATE_CANCELED",
                      "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
                      "latestOrderId": "GPA.2", "lineItems": [
                          {"productId": "alphaforge_premium_mensal", "expiryTime": FUTURO}]},
    "tok-expirada": {"subscriptionState": "SUBSCRIPTION_STATE_EXPIRED",
                     "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
                     "latestOrderId": "GPA.3", "lineItems": [
                         {"productId": "alphaforge_premium_mensal", "expiryTime": PASSADO}]},
}
reconhecidos = []

play.configurado = lambda: True
play.consultar = lambda token: BANCO_PLAY.get(token)
play.reconhecer = lambda token, produto: reconhecidos.append(token) or True

import uvicorn  # noqa: E402
from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402

from modules import planos  # noqa: E402
from routers import conta  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
PORTA = 8742

app = FastAPI()
app.include_router(conta.router)


@app.get("/", response_class=HTMLResponse)
def pagina():
    with open(os.path.join(BASE, "templates", "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/mercado/{resto:path}")
def mercado(resto: str):
    return {"grupos": {}, "obtidos": 0, "esperados": 0, "noticias": [],
            "altas": [], "baixas": []}


@app.get("/renda-variavel/ticker-tape")
def tape():
    return {"itens": []}


threading.Thread(
    target=lambda: uvicorn.run(app, host="127.0.0.1", port=PORTA, log_level="error"),
    daemon=True).start()
time.sleep(2.5)

import base64  # noqa: E402
import json  # noqa: E402

import httpx  # noqa: E402

falhas = []


def checar(rotulo, condicao, extra=""):
    if condicao:
        print(f"  ok   {rotulo}")
    else:
        print(f"  FALHA {rotulo} {extra}")
        falhas.append(rotulo)


def aviso(token, tipo=4, id_mensagem="msg-1"):
    dados = base64.b64encode(json.dumps({
        "version": "1.0", "packageName": "br.api.alphaforge",
        "subscriptionNotification": {
            "version": "1.0", "notificationType": tipo, "purchaseToken": token},
    }).encode()).decode()
    return {"message": {"data": dados, "messageId": id_mensagem}}


base = f"http://127.0.0.1:{PORTA}"
cliente = httpx.Client(base_url=base, timeout=15)

print("\n[servidor: confirmação de compra]")
cliente.post("/conta/registrar", json={"email": "assina@teste.com", "senha": "senha-boa-123"})
checar("nasce free", cliente.get("/conta/eu").json()["plano"] == "free")

r = cliente.post("/conta/assinatura/play/confirmar", json={"token": "tok-inexistente"})
checar("token desconhecido é recusado", r.status_code == 402, r.status_code)
checar("continua free depois da recusa", cliente.get("/conta/eu").json()["plano"] == "free")

r = cliente.post("/conta/assinatura/play/confirmar", json={"token": "tok-ativo"})
checar("token válido é aceito", r.status_code == 200, r.text[:200])
checar("virou premium", r.json()["plano"] == "premium", r.json().get("plano"))
checar("compra pendente foi reconhecida", "tok-ativo" in reconhecidos, reconhecidos)
checar("premium some do limite", cliente.get("/conta/eu").json()["cotas"]["rv_auditoria"]["limite"] is None)

print("\n[servidor: quem não entrou não assina]")
anon = httpx.Client(base_url=base, timeout=15)
r = anon.post("/conta/assinatura/play/confirmar", json={"token": "tok-ativo"})
checar("anônimo recebe 401", r.status_code == 401, r.status_code)

print("\n[servidor: notificações do Play]")
r = cliente.post("/conta/assinatura/play/notificar?segredo=errado", json=aviso("tok-ativo"))
checar("segredo errado é barrado", r.status_code == 401, r.status_code)

r = cliente.post("/conta/assinatura/play/notificar?segredo=segredo-play",
                 json=aviso("tok-ativo", 2, "msg-renovou"))
checar("renovação aceita", r.status_code == 200, r.text[:150])
r = cliente.post("/conta/assinatura/play/notificar?segredo=segredo-play",
                 json=aviso("tok-ativo", 2, "msg-renovou"))
checar("mesma notificação não repete", r.json().get("repetido") is True, r.json())

# Cancelar não é perder acesso: vale até o fim do ciclo pago.
BANCO_PLAY["tok-ativo"] = BANCO_PLAY["tok-cancelada"]
cliente.post("/conta/assinatura/play/notificar?segredo=segredo-play",
             json=aviso("tok-ativo", 3, "msg-cancelou"))
checar("cancelada mantém premium até vencer",
       cliente.get("/conta/eu").json()["plano"] == "premium",
       cliente.get("/conta/eu").json()["plano"])

BANCO_PLAY["tok-ativo"] = BANCO_PLAY["tok-expirada"]
cliente.post("/conta/assinatura/play/notificar?segredo=segredo-play",
             json=aviso("tok-ativo", 13, "msg-expirou"))
checar("expirada volta para free",
       cliente.get("/conta/eu").json()["plano"] == "free",
       cliente.get("/conta/eu").json()["plano"])

r = cliente.post("/conta/assinatura/play/notificar?segredo=segredo-play",
                 json={"message": {"data": base64.b64encode(
                     json.dumps({"testNotification": {"version": "1.0"}}).encode()).decode(),
                     "messageId": "msg-teste"}})
checar("notificação de teste responde ok", r.json().get("teste") is True, r.json())

print("\n[navegador: compra pela tela]")
BANCO_PLAY["tok-novo"] = {
    "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
    "acknowledgementState": "ACKNOWLEDGEMENT_STATE_PENDING",
    "latestOrderId": "GPA.9",
    "lineItems": [{"productId": "alphaforge_premium_anual", "expiryTime": FUTURO}]}

from playwright.sync_api import sync_playwright  # noqa: E402

DUBLE_JS = """
window.getDigitalGoodsService = async function (metodo) {
  return {
    getDetails: async (ids) => ids.map((id) => ({
      itemId: id,
      title: id === 'alphaforge_premium_anual' ? 'Premium anual' : 'Premium mensal',
      price: { currency: 'BRL', value: id === 'alphaforge_premium_anual' ? '399.00' : '39.90' },
    })),
    listPurchases: async () => window.__comprasPendentes || [],
  };
};
window.PaymentRequest = function (metodos, detalhes) {
  this.__sku = metodos[0].data.sku;
  this.show = async () => {
    window.__skuPedido = this.__sku;
    if (window.__usuarioCancela) { const e = new Error('cancelou'); e.name = 'AbortError'; throw e; }
    return { details: { purchaseToken: 'tok-novo' },
             complete: async (r) => { window.__resultadoFolha = r; } };
  };
};
"""

with sync_playwright() as p:
    navegador = p.chromium.launch()
    pag = navegador.new_page()
    erros = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.add_init_script(DUBLE_JS)

    pag.goto(f"{base}/", wait_until="domcontentloaded")
    pag.wait_for_timeout(1200)
    checar("tela detecta Play Billing", pag.evaluate("AF.temPlayBilling()") is True)

    pag.evaluate("""AF.mostrarPaywall({motivo:'teste', autenticado:false})""")
    pag.click("text=Ver o Premium")
    pag.wait_for_timeout(400)
    checar("sem conta, manda entrar antes",
           "Entre na sua conta" in pag.locator("#afPaywallMotivo").inner_text(),
           pag.locator("#afPaywallMotivo").inner_text())

    pag.evaluate("AF.fechar()")
    pag.click("#btnConta")
    pag.wait_for_timeout(250)
    pag.fill("#afEmail", "tela-assina@teste.com")
    pag.fill("#afSenha", "senha-da-tela-1")
    pag.click("text=Não tenho conta — criar uma")
    pag.wait_for_timeout(150)
    pag.click("#afLoginBtn")
    pag.wait_for_timeout(1300)

    pag.evaluate("""AF.mostrarPaywall({motivo:'teste', autenticado:true})""")
    pag.click("text=Ver o Premium")
    pag.wait_for_timeout(900)
    planos_txt = pag.locator("#afPaywallPlanos").inner_text()
    checar("lista os dois planos",
           "Premium mensal" in planos_txt and "Premium anual" in planos_txt, repr(planos_txt))
    checar("preço formatado em real", "39,90" in planos_txt, repr(planos_txt))

    print("\n[navegador: usuário desiste]")
    pag.evaluate("window.__usuarioCancela = true")
    pag.click("text=Premium anual")
    pag.wait_for_timeout(700)
    checar("cancelar não vira erro na tela",
           "Falha" not in pag.locator("#afPaywallPlanos").inner_text(),
           pag.locator("#afPaywallPlanos").inner_text()[:80])
    checar("segue free depois de desistir",
           pag.evaluate("AF.estado.plano") == "free", pag.evaluate("AF.estado.plano"))

    print("\n[navegador: compra concluída]")
    pag.evaluate("window.__usuarioCancela = false")
    pag.evaluate("""AF.mostrarPaywall({motivo:'teste', autenticado:true})""")
    pag.click("text=Ver o Premium")
    pag.wait_for_timeout(800)
    pag.click("text=Premium anual")
    pag.wait_for_timeout(1600)
    checar("sku enviado é o do botão",
           pag.evaluate("window.__skuPedido") == "alphaforge_premium_anual",
           pag.evaluate("window.__skuPedido"))
    checar("folha concluída com sucesso",
           pag.evaluate("window.__resultadoFolha") == "success",
           pag.evaluate("window.__resultadoFolha"))
    checar("tela agora é premium", pag.evaluate("AF.estado.plano") == "premium",
           pag.evaluate("AF.estado.plano"))
    rotulo = pag.locator("#btnConta").inner_text()
    checar("botão mostra PREMIUM", "PREMIUM" in rotulo, repr(rotulo))
    checar("modal fechou",
           "hidden" in (pag.locator("#afModal").get_attribute("class") or ""))
    checar("compra nova foi reconhecida", "tok-novo" in reconhecidos, reconhecidos)

    print("\n[navegador: recupera compra que não confirmou]")
    pag2 = navegador.new_page()
    pag2.add_init_script(DUBLE_JS)
    pag2.add_init_script("window.__comprasPendentes = [{purchaseToken: 'tok-orfao'}];")
    BANCO_PLAY["tok-orfao"] = {
        "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
        "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        "latestOrderId": "GPA.77",
        "lineItems": [{"productId": "alphaforge_premium_mensal", "expiryTime": FUTURO}]}
    pag2.goto(f"{base}/", wait_until="domcontentloaded")
    pag2.wait_for_timeout(300)
    pag2.click("#btnConta")
    pag2.wait_for_timeout(250)
    pag2.fill("#afEmail", "orfao@teste.com")
    pag2.fill("#afSenha", "senha-orfao-123")
    pag2.click("text=Não tenho conta — criar uma")
    pag2.wait_for_timeout(150)
    pag2.click("#afLoginBtn")
    pag2.wait_for_timeout(1200)
    checar("entrou como free", pag2.evaluate("AF.estado.plano") == "free")
    pag2.reload(wait_until="domcontentloaded")
    pag2.wait_for_timeout(2200)
    checar("compra pendente foi recuperada sozinha",
           pag2.evaluate("AF.estado.plano") == "premium",
           pag2.evaluate("AF.estado.plano"))

    graves = [e for e in erros if "tailwind" not in e.lower() and "font" not in e.lower()]
    print("\n[console]")
    checar("sem erro de JavaScript", not graves, graves[:2])
    navegador.close()

print()
if falhas:
    print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    raise SystemExit(1)
print("Tudo passou.")
