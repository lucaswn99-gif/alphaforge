"""Páginas públicas obrigatórias e Digital Asset Links do app Android.

Quatro rotas:

    GET /termos                      página pública
    GET /privacidade                 página pública — exigida pelo Google Play
    GET /api/legal                   o aviso em JSON, para o app consumir
    GET /.well-known/assetlinks.json prova de domínio para o TWA

As páginas são geradas em Python, e não com template, de propósito: o texto
mora em `modules.legal` e não pode divergir entre a tela e a página. Duplicar
o aviso num .html é como ele começa a divergir.
"""

import json
import os

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse, JSONResponse

from modules import legal

router = APIRouter(tags=["legal"])

# Preenchido pelo `keytool`/Play Console depois que a chave de assinatura
# existir. Sem isso, o TWA abre com barra de navegador — o app "funciona",
# mas parece um site embrulhado, que é justamente o que se quer evitar.
PACOTE_ANDROID = os.environ.get("ANDROID_PACOTE", "br.api.alphaforge.twa").strip()
SHA256_ANDROID = os.environ.get("ANDROID_SHA256", "").strip()

_ESTILO = """
:root{--fundo:#050806;--painel:#0a0f0c;--borda:#16211b;--verde:#00e57a;
--claro:#e8efea;--suave:#7d8f85}
*{box-sizing:border-box}
body{margin:0;background:var(--fundo);color:var(--claro);
font:15px/1.65 Inter,system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.env{max-width:820px;margin:0 auto;padding:40px 20px 72px}
a{color:var(--verde);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:22px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:13px;text-transform:uppercase;letter-spacing:.09em;
color:var(--verde);margin:32px 0 10px}
p{margin:0 0 12px;color:#c8d4cc}
.meta{font:11px/1.5 'IBM Plex Mono',ui-monospace,monospace;color:var(--suave);
margin:0 0 28px}
.caixa{background:var(--painel);border:1px solid var(--borda);
border-radius:8px;padding:18px 20px;margin:22px 0}
.topo{border-bottom:1px solid var(--borda);padding:14px 20px;background:#000;
font:600 12px 'IBM Plex Mono',ui-monospace,monospace;letter-spacing:.12em;
text-transform:uppercase}
.rodape{border-top:1px solid var(--borda);margin-top:40px;padding-top:18px;
font:11px/1.6 'IBM Plex Mono',ui-monospace,monospace;color:var(--suave)}
"""


def _pagina(titulo, blocos, chamada=None):
    partes = [
        "<!DOCTYPE html><html lang=\"pt-BR\"><head><meta charset=\"UTF-8\">",
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">",
        f"<title>{titulo} · AlphaForge</title>",
        "<meta name=\"theme-color\" content=\"#050806\">",
        "<link rel=\"icon\" type=\"image/png\" sizes=\"192x192\" href=\"/static/icone-192.png\">",
        f"<style>{_ESTILO}</style></head><body>",
        "<div class=\"topo\">AlphaForge Terminal</div><div class=\"env\">",
        f"<h1>{titulo}</h1>",
        f"<p class=\"meta\">Versão {legal.VERSAO} · atualizado em "
        f"{legal.ATUALIZADO_EM}</p>",
    ]
    if chamada:
        partes.append("<div class=\"caixa\">" +
                      "".join(f"<p>{p}</p>" for p in chamada) + "</div>")
    for subtitulo, paragrafos in blocos:
        partes.append(f"<h2>{subtitulo}</h2>")
        partes.extend(f"<p>{p}</p>" for p in paragrafos)
    partes.append(
        "<div class=\"rodape\">"
        "<a href=\"/\">← voltar ao terminal</a> · "
        "<a href=\"/termos\">termos de uso</a> · "
        "<a href=\"/privacidade\">política de privacidade</a>"
        "</div></div></body></html>")
    return "".join(partes)


@router.get("/termos", response_class=HTMLResponse)
def termos():
    """Termos de uso. O aviso de não-recomendação abre a página, e não fica
    escondido no meio: aviso que só aparece depois do quinto parágrafo não
    cumpre a função de avisar."""
    return _pagina(legal.TERMOS_TITULO, legal.secoes(legal.TERMOS),
                   chamada=legal.aviso()["paragrafos"][:2])


@router.get("/privacidade", response_class=HTMLResponse)
def privacidade():
    """Exigida pelo Google Play em URL pública e estável."""
    return _pagina(legal.PRIVACIDADE_TITULO, legal.secoes(legal.PRIVACIDADE))


@router.get("/api/legal")
def api_legal():
    """O aviso em JSON. O app Android lê daqui em vez de embutir uma cópia —
    assim o texto atualiza sem precisar publicar versão nova na Play Store."""
    return legal.aviso()


@router.get("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links: prova ao Chrome que este domínio autoriza o APK.

    Sem o SHA-256 configurado devolve 503 com instrução, em vez de uma lista
    vazia — lista vazia é válida como JSON e faz o TWA falhar em silêncio,
    que é o pior modo de falhar.
    """
    if not SHA256_ANDROID:
        return JSONResponse(
            status_code=503,
            content={
                "erro": "ANDROID_SHA256 não configurado no ambiente.",
                "como_obter": "keytool -list -v -keystore alphaforge.keystore "
                              "-alias alphaforge",
                "formato": "AA:BB:CC:... (65 caracteres, com dois-pontos)",
                "pacote_esperado": PACOTE_ANDROID,
            })
    impressoes = [d.strip().upper() for d in SHA256_ANDROID.split(",") if d.strip()]
    conteudo = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": PACOTE_ANDROID,
            "sha256_cert_fingerprints": impressoes,
        },
    }]
    # Content-Type exato: o verificador do Google recusa text/plain.
    return Response(content=json.dumps(conteudo, indent=2),
                    media_type="application/json")
