"""Prova que a trava faz o que promete.

Monta uma app mínima com rotas que imitam as reais — mesmas dependências,
mesmos cortes — sem arrastar yfinance e pandas para o teste. O que está sob
teste é a regra de plano, não a coleta de dado.
"""

if __name__ != "__main__":  # pragma: no cover
    # Isto é um script de verificação, não uma suíte pytest: sobe servidor (e,
    # no caso do billing, navegador) no corpo do módulo. Coletado pelo
    # `python -m pytest` que guarda o deploy, tudo isso rodaria durante a
    # COLETA — e uma máquina de CI sem navegador derrubaria o portão inteiro.
    # `allow_module_level` corta a coleta antes de qualquer linha abaixo.
    import pytest

    pytest.skip("script de verificação; rode com: python test_planos.py",
                allow_module_level=True)

import os
import tempfile

# Banco temporário: o teste não pode encostar no contas.db de produção.
_tmp = tempfile.mkdtemp()
os.environ["AF_WEBHOOK_SEGREDO"] = "segredo-de-teste"

from modules import contas  # noqa: E402

contas.CAMINHO_BANCO = os.path.join(_tmp, "contas_teste.db")

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from modules import planos  # noqa: E402
from routers import conta  # noqa: E402

app = FastAPI()
app.include_router(conta.router)


@app.get("/falso/scanner")
def falso_scanner(ctx: planos.Contexto = Depends(planos.acesso())):
    oportunidades = [
        {"ticker": f"AAA{i}", "score_geral": 90 - i, "pl": 5.0,
         "alertas_risco": ["algo"], "pontos_positivos": ["outro"]}
        for i in range(80)
    ]
    return planos.cortar_scanner(
        {"total": 80, "oportunidades": oportunidades,
         "falhas": [{"ticker": "XPTO"}], "sem_fundamentos": []}, ctx)


@app.get("/falso/quant")
def falso_quant(ctx: planos.Contexto = Depends(planos.acesso())):
    papeis = [{"ticker": f"BBB{i}", "score_quant": 70 - i,
               "componentes": {"x": 1}} for i in range(40)]
    return planos.cortar_quant({"papeis": papeis, "exibidos": 40}, ctx)


@app.get("/falso/fundos")
def falso_fundos(ctx: planos.Contexto = Depends(planos.acesso())):
    fii = [{"ticker": f"FII{i}", "pvp": 0.9, "recomendacao": "COMPRAR"}
           for i in range(10)]
    return planos.cortar_fundos(
        {"tijolo": fii, "papel": fii, "etfs": fii}, ctx)


@app.get("/falso/auditoria")
def falso_auditoria(ctx: planos.Contexto = Depends(planos.acesso("rv_auditoria"))):
    return {"ok": True, "premium": ctx.premium}


@app.get("/falso/recomendar")
def falso_recomendar(ctx: planos.Contexto = Depends(planos.acesso("opcoes_recomendar"))):
    return {"ok": True}


c = TestClient(app)
falhas = []


def checar(rotulo, condicao, extra=""):
    if condicao:
        print(f"  ok   {rotulo}")
    else:
        print(f"  FALHA {rotulo} {extra}")
        falhas.append(rotulo)


print("\n[anônimo — corte de resposta]")
r = c.get("/falso/scanner").json()
checar("scanner devolve 5 papéis", len(r["oportunidades"]) == 5, len(r["oportunidades"]))
checar("scanner marca truncado", r.get("truncado") is True)
checar("scanner informa 75 ocultos", r.get("ocultos") == 75, r.get("ocultos"))
checar("scanner esconde alertas_risco",
       "alertas_risco" not in r["oportunidades"][0])
checar("scanner zera lista de falhas", r["falhas"] == [])

r = c.get("/falso/quant").json()
checar("quant devolve 3 papéis", len(r["papeis"]) == 3, len(r["papeis"]))
checar("quant esconde componentes", "componentes" not in r["papeis"][0])

r = c.get("/falso/fundos").json()
checar("fundos corta em 4 por bloco", len(r["tijolo"]) == 4, len(r["tijolo"]))
checar("fundos remove recomendação", "recomendacao" not in r["tijolo"][0])

print("\n[anônimo — bloqueio]")
r = c.get("/falso/recomendar")
checar("recomendar devolve 402", r.status_code == 402, r.status_code)
checar("402 traz corpo estruturado",
       r.json()["detail"]["erro"] == "limite_plano")

print("\n[anônimo — cota diária]")
codigos = [c.get("/falso/auditoria").status_code for _ in range(4)]
checar("3 auditorias passam", codigos[:3] == [200, 200, 200], codigos)
checar("a 4ª devolve 402", codigos[3] == 402, codigos)
corpo = c.get("/falso/auditoria").json()["detail"]
checar("402 informa limite e uso",
       corpo["limite"] == 3 and corpo["usado"] >= 3, corpo)
checar("402 informa quando reinicia", bool(corpo["reinicia_em"]))

print("\n[conta]")
r = c.post("/conta/registrar", json={"email": "Lucas@Teste.com ", "senha": "senha-boa-123"})
checar("registrar devolve 200", r.status_code == 200, r.text[:200])
checar("e-mail normalizado", r.json()["email"] == "lucas@teste.com", r.json().get("email"))
checar("nasce no plano free", r.json()["plano"] == "free")
checar("cookie de sessão gravado", contas.NOME_COOKIE in r.cookies)

r = c.post("/conta/registrar", json={"email": "lucas@teste.com", "senha": "outra-senha-123"})
checar("e-mail duplicado recusado", r.status_code == 400, r.status_code)

r = c.post("/conta/registrar", json={"email": "curto@teste.com", "senha": "1234"})
checar("senha curta recusada", r.status_code == 400, r.status_code)

r = c.get("/conta/eu").json()
checar("eu diz autenticado", r["autenticado"] is True)
checar("eu traz saldo de cota", "rv_auditoria" in r["cotas"])

print("\n[cota segue a conta, não o IP]")
codigos = [c.get("/falso/auditoria").status_code for _ in range(4)]
checar("conta nova tem cota própria", codigos[:3] == [200, 200, 200], codigos)
checar("e também esbarra na 4ª", codigos[3] == 402, codigos)

print("\n[login]")
c.post("/conta/sair")
checar("depois de sair, é anônimo", c.get("/conta/eu").json()["autenticado"] is False)
r = c.post("/conta/entrar", json={"email": "lucas@teste.com", "senha": "errada-mesmo"})
checar("senha errada devolve 401", r.status_code == 401, r.status_code)
r = c.post("/conta/entrar", json={"email": "lucas@teste.com", "senha": "senha-boa-123"})
checar("senha certa entra", r.status_code == 200, r.status_code)

print("\n[webhook de assinatura]")
r = c.post("/conta/assinatura/webhook",
           json={"email": "lucas@teste.com", "plano": "premium"})
checar("webhook sem segredo recusa", r.status_code == 401, r.status_code)
r = c.post("/conta/assinatura/webhook",
           json={"email": "lucas@teste.com", "plano": "premium"},
           headers={"x-af-segredo": "segredo-de-teste"})
checar("webhook com segredo aceita", r.status_code == 200, r.text[:200])
r = c.post("/conta/assinatura/webhook",
           json={"email": "ninguem@teste.com", "plano": "premium"},
           headers={"x-af-segredo": "segredo-de-teste"})
checar("webhook recusa conta inexistente", r.status_code == 404, r.status_code)

print("\n[premium]")
r = c.get("/conta/eu").json()
checar("plano virou premium", r["plano"] == "premium", r["plano"])
r = c.get("/falso/scanner").json()
checar("premium vê os 80 papéis", len(r["oportunidades"]) == 80, len(r["oportunidades"]))
checar("premium não vem truncado", "truncado" not in r)
checar("premium vê alertas_risco", "alertas_risco" in r["oportunidades"][0])
checar("premium vê recomendar", c.get("/falso/recomendar").status_code == 200)
checar("premium ignora a cota estourada",
       c.get("/falso/auditoria").status_code == 200)
r = c.get("/falso/fundos").json()
checar("premium vê recomendação de FII", "recomendacao" in r["tijolo"][0])

print("\n[plano vencido volta a free]")
contas.definir_plano(
    contas.autenticar("lucas@teste.com", "senha-boa-123")["id"],
    "premium", "2020-01-01T00:00:00+00:00")
checar("premium vencido é tratado como free",
       c.get("/conta/eu").json()["plano"] == "free")

print()
if falhas:
    print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
    raise SystemExit(1)
print("Tudo passou.")
