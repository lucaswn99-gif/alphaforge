"""Confere se o APK, o ambiente e o assetlinks.json publicado concordam.

Este script existe por causa de um dia inteiro perdido. O Bolhas abria com
barra de navegador e a suspeita passou por cache, BOM, chave errada e estado
do aparelho — quando a causa era um nome de pacote com um segmento a mais no
`assetlinks.json` do que o compilado dentro do APK. Nada no caminho normal
avisa: o Chrome só deixa de verificar, em silêncio.

São três fatos que precisam bater, e cada um mora num lugar diferente:

    nome do pacote   →  dentro do APK   ==  no assetlinks.json publicado
    impressão SHA-256 →  dentro do APK   ==  no assetlinks.json publicado
    impressão do Google (Play App Signing) → só existe depois do 1º upload

Rodar:

    python verificar_twa.py app-release-signed.apk
    python verificar_twa.py app-release-signed.apk --dominio alphaforge.api.br
"""

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import urllib.request

PADRAO = "alphaforge.api.br"


def cor(texto, codigo):
    if os.name == "nt" and not os.environ.get("WT_SESSION"):
        return texto
    return f"\033[{codigo}m{texto}\033[0m"


def ok(msg):
    print("  " + cor("ok", "32") + "   " + msg)


def erro(msg, detalhe=""):
    print("  " + cor("FALHA", "31") + " " + msg + (f"\n         {detalhe}" if detalhe else ""))


def aviso(msg):
    print("  " + cor("aviso", "33") + " " + msg)


def achar_ferramenta(nome):
    """Procura aapt/apksigner no SDK que o Bubblewrap baixou."""
    candidatos = []
    for raiz in (os.path.expanduser("~/.bubblewrap/android_sdk"),
                 os.environ.get("ANDROID_HOME", ""),
                 os.environ.get("ANDROID_SDK_ROOT", "")):
        if not raiz:
            continue
        for sufixo in (".bat", ".exe", ""):
            candidatos += glob.glob(os.path.join(raiz, "build-tools", "*", nome + sufixo))
    if not candidatos:
        return None
    # Maior versão primeiro.
    candidatos.sort(key=lambda c: c.split(os.sep)[-2], reverse=True)
    return candidatos[0]


def rodar(comando):
    return subprocess.run(comando, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def pacote_do_apk(apk):
    ferramenta = achar_ferramenta("aapt")
    if not ferramenta:
        return None, "aapt não encontrado — o SDK do Bubblewrap já foi baixado?"
    saida = rodar([ferramenta, "dump", "badging", apk])
    achado = re.search(r"package: name='([^']+)'", saida.stdout)
    if not achado:
        return None, (saida.stderr or saida.stdout)[:200]
    return achado.group(1), None


def impressao_do_apk(apk):
    ferramenta = achar_ferramenta("apksigner")
    if not ferramenta:
        return None, "apksigner não encontrado no SDK."
    saida = rodar([ferramenta, "verify", "--print-certs", apk])
    achado = re.search(r"SHA-256 digest:\s*([0-9a-fA-F]{64})", saida.stdout)
    if not achado:
        return None, (saida.stderr or saida.stdout)[:200]
    return achado.group(1).lower(), None


def normalizar(impressao):
    """`AA:BB:CC…` e `aabbcc…` viram a mesma coisa. A diferença de formato
    entre o keytool e o apksigner já fez muita gente comparar errado."""
    return re.sub(r"[^0-9a-f]", "", (impressao or "").lower())


def baixar_assetlinks(dominio):
    url = f"https://{dominio}/.well-known/assetlinks.json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resposta:
            bruto = resposta.read()
            tipo = resposta.headers.get("Content-Type", "")
    except Exception as falha:  # noqa: BLE001
        return None, None, None, f"não consegui buscar {url}: {falha}"

    # BOM no começo do arquivo é JSON inválido para o verificador do Google —
    # e some da vista em qualquer editor. Vale conferir os bytes crus.
    tem_bom = bruto[:3] == b"\xef\xbb\xbf"
    try:
        conteudo = json.loads(bruto.decode("utf-8-sig"))
    except Exception as falha:  # noqa: BLE001
        return None, tipo, tem_bom, f"resposta não é JSON válido: {falha}"
    return conteudo, tipo, tem_bom, None


def ler_env(chave):
    valor = os.environ.get(chave, "").strip()
    if valor:
        return valor
    caminho = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(caminho):
        return ""
    with open(caminho, encoding="utf-8") as arquivo:
        for linha in arquivo:
            if linha.strip().startswith(chave + "="):
                return linha.split("=", 1)[1].strip()
    return ""


def main():
    analisador = argparse.ArgumentParser(description=__doc__)
    analisador.add_argument("apk", nargs="?", default="app-release-signed.apk")
    analisador.add_argument("--dominio", default=PADRAO)
    args = analisador.parse_args()

    problemas = []

    print(f"\nAPK: {args.apk}")
    print(f"Domínio: {args.dominio}\n")

    # ---------------------------------------------------------------- APK --
    print("[dentro do APK]")
    if not os.path.exists(args.apk):
        erro(f"{args.apk} não existe", "rode `bubblewrap build` antes.")
        return 1

    pacote, falha = pacote_do_apk(args.apk)
    if falha:
        erro("não consegui ler o nome do pacote", falha)
        problemas.append("pacote")
    else:
        ok(f"nome do pacote: {pacote}")

    impressao, falha = impressao_do_apk(args.apk)
    if falha:
        erro("não consegui ler a assinatura", falha)
        problemas.append("assinatura")
    else:
        ok(f"assinado com: {impressao[:16]}…")

    # -------------------------------------------------------------- .env --
    print("\n[ambiente do servidor]")
    pacote_env = ler_env("ANDROID_PACOTE") or "br.api.alphaforge.twa"
    sha_env = ler_env("ANDROID_SHA256")
    print(f"  ANDROID_PACOTE = {pacote_env}")
    if not sha_env:
        aviso("ANDROID_SHA256 vazio aqui — normal se só estiver no droplet.")
    if pacote and pacote_env != pacote:
        erro("ANDROID_PACOTE diverge do APK",
             f"APK diz {pacote}, ambiente diz {pacote_env}")
        problemas.append("pacote-env")

    # -------------------------------------------------------- assetlinks --
    print("\n[assetlinks.json publicado]")
    conteudo, tipo, tem_bom, falha = baixar_assetlinks(args.dominio)
    if falha:
        erro("assetlinks indisponível", falha)
        problemas.append("assetlinks")
    else:
        if tem_bom:
            erro("o arquivo começa com BOM (EF BB BF)",
                 "o verificador do Google recusa como JSON inválido.")
            problemas.append("bom")
        else:
            ok("sem BOM")

        if "application/json" not in (tipo or ""):
            erro(f"Content-Type é {tipo!r}", "precisa ser application/json.")
            problemas.append("content-type")
        else:
            ok("Content-Type: application/json")

        pacotes = set()
        impressoes = set()
        for entrada in conteudo if isinstance(conteudo, list) else []:
            alvo = entrada.get("target", {})
            if alvo.get("namespace") != "android_app":
                continue
            pacotes.add(alvo.get("package_name"))
            for digital in alvo.get("sha256_cert_fingerprints", []):
                impressoes.add(normalizar(digital))

        if not pacotes:
            erro("nenhuma entrada android_app no assetlinks")
            problemas.append("vazio")
        else:
            ok(f"declara: {', '.join(sorted(p for p in pacotes if p))}")

            if pacote and pacote not in pacotes:
                erro("o pacote do APK NÃO está no assetlinks",
                     f"APK: {pacote}  ·  publicado: {', '.join(sorted(p for p in pacotes if p))}")
                problemas.append("pacote-assetlinks")
            elif pacote:
                ok("o pacote do APK confere")

            if impressao and normalizar(impressao) not in impressoes:
                erro("a assinatura do APK NÃO está no assetlinks",
                     f"APK: {impressao[:24]}…  ·  publicadas: "
                     + ", ".join(sorted(i[:16] + '…' for i in impressoes)))
                problemas.append("sha-assetlinks")
            elif impressao:
                ok("a assinatura do APK confere")

            if len(impressoes) < 2:
                aviso("só uma impressão publicada. Depois do primeiro envio ao "
                      "Play, some a chave do Play App Signing (Console → "
                      "Proteção → Gerencie a Assinatura) — sem ela, quem "
                      "instalar pela loja vê a barra do navegador.")
            else:
                ok(f"{len(impressoes)} impressões publicadas (upload + Play)")

    # -------------------------------------------------------- veredicto --
    print()
    if problemas:
        print(cor(f"{len(problemas)} problema(s): " + ", ".join(problemas), "31"))
        print("\nEnquanto houver divergência, o app abre com barra de navegador.")
        return 1
    print(cor("Tudo confere. O TWA deve abrir sem barra de navegador.", "32"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
