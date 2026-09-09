"""Manchetes de mercado, commodities e macro, por RSS público.

O QUE ESTE MÓDULO MOSTRA, E O QUE ELE NÃO MOSTRA
------------------------------------------------
Mostra: manchete, veículo, horário e o link para a matéria no site do veículo.
Não mostra, não guarda e não resume: o texto da matéria. Isso é deliberado —
conteúdo de veículo é do veículo, e um terminal que você usa com cliente não
pode redistribuir matéria. O painel funciona como índice: você lê a manchete
aqui e clica para ler no dono do conteúdo.

FONTES
------
RSS aberto, publicado pelo próprio veículo ou órgão. Bancos centrais entram
como fonte primária (BCB e Fed publicam comunicado, ata e nota de imprensa em
feed próprio), o que para decisão de juros vale mais que a cobertura de
segunda mão.

VERIFICAÇÃO
-----------
Endereço de RSS muda sem aviso e eu não consigo testá-los do ambiente onde
escrevo. Por isso:

    python -m modules.noticias --verificar

lista quais feeds responderam, com quantos itens e o mais recente de cada um.
Feed que não responde é marcado como indisponível e aparece assim na tela, em
vez de sumir — o mesmo princípio do resto do projeto: falha visível, nunca
silenciosa.
"""

import concurrent.futures
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

CACHE_TTL = 600          # 10 min; manchete não é cotação
TIMEOUT = 8
MAX_POR_FONTE = 8
MAX_TOTAL = 40

CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}

# (chave, veículo, url, categoria). Categoria organiza a tela.
FONTES = [
    # --- macro oficial: a fonte primária, sem intermediário ---
    ("bcb", "Banco Central", "https://www.bcb.gov.br/rss/noticias", "Macro"),
    ("fed", "Federal Reserve",
     "https://www.federalreserve.gov/feeds/press_monetary.xml", "Macro"),
    ("fed_all", "Fed (todos os comunicados)",
     "https://www.federalreserve.gov/feeds/press_all.xml", "Macro"),
    ("ibge", "IBGE", "https://agenciadenoticias.ibge.gov.br/agencia-noticias/rss.html",
     "Macro"),
    # --- mercado ---
    ("infomoney", "InfoMoney", "https://www.infomoney.com.br/feed/", "Mercado"),
    ("moneytimes", "Money Times", "https://www.moneytimes.com.br/feed/", "Mercado"),
    ("seudinheiro", "Seu Dinheiro", "https://www.seudinheiro.com/feed/", "Mercado"),
    ("agenciabrasil", "Agência Brasil",
     "https://agenciabrasil.ebc.com.br/rss/economia/feed.xml", "Mercado"),
    # --- commodities e agro ---
    ("noticiasagricolas", "Notícias Agrícolas",
     "https://www.noticiasagricolas.com.br/rss/noticias.xml", "Commodities"),
    ("canalrural", "Canal Rural", "https://www.canalrural.com.br/feed/", "Commodities"),
]

CATEGORIAS = ("Macro", "Mercado", "Commodities")

_lock = threading.Lock()
_cache = {"payload": None, "carimbo": 0.0}

_TAGS = re.compile(r"<[^>]+>")


def _texto(valor):
    """Manchete limpa. Alguns feeds mandam HTML dentro do <title>."""
    limpo = _TAGS.sub("", str(valor or "")).strip()
    limpo = (limpo.replace("&amp;", "&").replace("&quot;", '"')
             .replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&nbsp;", " "))
    return " ".join(limpo.split())


def _quando(item):
    """Horário do item em ISO-8601 UTC, ou None. Aceita RSS e Atom."""
    for campo in ("pubDate", "published", "updated", "{http://www.w3.org/2005/Atom}updated"):
        bruto = item.findtext(campo)
        if not bruto:
            continue
        try:
            momento = parsedate_to_datetime(bruto)
        except (TypeError, ValueError):
            try:
                momento = datetime.fromisoformat(bruto.replace("Z", "+00:00"))
            except ValueError:
                continue
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        return momento.astimezone(timezone.utc).isoformat()
    return None


def _link(item):
    direto = item.findtext("link")
    if direto and direto.strip():
        return direto.strip()
    # Atom guarda o endereço num atributo, não no texto do elemento.
    for elemento in item.iter():
        if elemento.tag.endswith("link") and elemento.get("href"):
            return elemento.get("href").strip()
    return None


def _itens_do_xml(corpo):
    raiz = ET.fromstring(corpo)
    itens = raiz.findall(".//item")
    if not itens:
        itens = raiz.findall("{http://www.w3.org/2005/Atom}entry")
    return itens


def ler_fonte(chave, veiculo, url, categoria):
    """Uma fonte. Devolve (itens, diagnóstico) — o diagnóstico sempre existe."""
    diagnostico = {"chave": chave, "veiculo": veiculo, "url": url,
                   "categoria": categoria, "ok": False, "itens": 0, "motivo": None}
    try:
        resposta = requests.get(url, headers=CABECALHOS, timeout=TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        diagnostico["motivo"] = f"{type(exc).__name__}"
        return [], diagnostico

    if resposta.status_code != 200:
        diagnostico["motivo"] = f"HTTP {resposta.status_code}"
        return [], diagnostico

    try:
        itens = _itens_do_xml(resposta.content)
    except ET.ParseError as exc:
        diagnostico["motivo"] = f"XML inválido: {exc}"
        return [], diagnostico

    manchetes = []
    for item in itens[:MAX_POR_FONTE]:
        titulo = _texto(item.findtext("title") or
                        item.findtext("{http://www.w3.org/2005/Atom}title"))
        if not titulo:
            continue
        manchetes.append({
            "titulo": titulo,          # manchete apenas — nunca o corpo da matéria
            "link": _link(item),
            "veiculo": veiculo,
            "categoria": categoria,
            "quando": _quando(item),
        })

    diagnostico["ok"] = bool(manchetes)
    diagnostico["itens"] = len(manchetes)
    if not manchetes:
        diagnostico["motivo"] = "feed respondeu sem itens legíveis"
    return manchetes, diagnostico


def coletar(forcar=False):
    """{manchetes, fontes, fontes_ok, fontes_total, coletado_em}.

    As fontes são lidas em paralelo: uma lenta não pode segurar a tela, e o
    timeout curto garante que o painel apareça mesmo com metade fora do ar.
    """
    agora = time.time()
    with _lock:
        payload = _cache["payload"]
        idade = agora - _cache["carimbo"]
    if payload is not None and not forcar and idade < CACHE_TTL:
        return {**payload, "cache": True, "idade_segundos": int(idade)}

    manchetes = []
    diagnosticos = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futuros = [pool.submit(ler_fonte, *fonte) for fonte in FONTES]
        for futuro in concurrent.futures.as_completed(futuros):
            try:
                itens, diagnostico = futuro.result()
            except Exception as exc:  # noqa: BLE001
                diagnosticos.append({"chave": "?", "ok": False,
                                     "motivo": type(exc).__name__})
                continue
            manchetes.extend(itens)
            diagnosticos.append(diagnostico)

    # Mais recente primeiro; item sem data vai para o fim em vez de para o topo.
    manchetes.sort(key=lambda m: m["quando"] or "", reverse=True)

    resultado = {
        "manchetes": manchetes[:MAX_TOTAL],
        "fontes": sorted(diagnosticos, key=lambda d: (not d["ok"], d.get("chave") or "")),
        "fontes_ok": sum(1 for d in diagnosticos if d["ok"]),
        "fontes_total": len(FONTES),
        "coletado_em": datetime.now(timezone.utc).isoformat(),
    }

    # Só substitui o cache se veio alguma coisa: uma falha geral de rede não
    # pode apagar as manchetes que já estavam na tela.
    if resultado["manchetes"]:
        with _lock:
            _cache["payload"] = resultado
            _cache["carimbo"] = agora
        return {**resultado, "cache": False, "idade_segundos": 0}

    if payload is not None:
        return {**payload, "cache": True, "idade_segundos": int(idade),
                "aviso": "nenhuma fonte respondeu agora; mostrando a última coleta"}
    return {**resultado, "cache": False, "idade_segundos": 0}


def limpar_cache():
    with _lock:
        _cache["payload"] = None
        _cache["carimbo"] = 0.0


if __name__ == "__main__":
    import sys

    if "--verificar" not in sys.argv:
        raise SystemExit("uso: python -m modules.noticias --verificar")

    print(f"Testando {len(FONTES)} fontes...\n")
    vivos, mortos = [], []
    for fonte in FONTES:
        itens, diagnostico = ler_fonte(*fonte)
        marca = "OK " if diagnostico["ok"] else "FALHOU"
        print(f"{marca:7} {diagnostico['veiculo']:<28} "
              f"{diagnostico['itens']:>2} itens  {diagnostico['motivo'] or ''}")
        if itens:
            print(f"        ultima: {itens[0]['titulo'][:90]}")
        (vivos if diagnostico["ok"] else mortos).append(diagnostico["chave"])

    print(f"\n{len(vivos)} de {len(FONTES)} fontes responderam")
    if mortos:
        print("sem resposta: " + ", ".join(mortos))
