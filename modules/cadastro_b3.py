"""Ticker do B3 -> CNPJ da companhia.

É o elo entre o que o scanner varre (tickers) e o que a CVM publica (balanços
por CNPJ). A fonte é o mesmo sistema de listadas do B3 que alimenta a página
pública: cada empresa traz `issuingCompany` (a raiz de 4 letras do ticker) e
`cnpj`.

    VALE3 -> raiz VALE -> CNPJ 33592510000154

O resultado é gravado num JSON versionado no repositório. Em runtime lemos só
esse arquivo: o serviço não pode depender do B3 estar de pé para saber a que
empresa um ticker pertence, e o cadastro muda devagar.
"""

import base64
import json
import os
import re
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_BASE = ("https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/"
            "CompanyCall/GetInitialCompanies/{payload}")
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 15
TAMANHO_PAGINA = 200
MAX_PAGINAS = 40

ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cadastro_b3.json")

# A raiz do ticker são as letras iniciais; o resto é a classe (3 ON, 4 PN,
# 11 unit, 5/6 PNA/PNB).
_RAIZ = re.compile(r"^([A-Z]{4})\d{1,2}$")


def raiz_do_ticker(ticker):
    """VALE3 -> VALE. Devolve None para código fora do padrão."""
    casou = _RAIZ.match((ticker or "").upper().strip())
    return casou.group(1) if casou else None


def normalizar_cnpj(bruto):
    """Só dígitos, com 14 posições. '0' e vazio viram None."""
    digitos = re.sub(r"\D", "", str(bruto or ""))
    if not digitos or set(digitos) == {"0"}:
        return None
    return digitos.zfill(14) if len(digitos) <= 14 else None


def _pagina(numero):
    parametros = {"language": "pt-br", "pageNumber": numero, "pageSize": TAMANHO_PAGINA}
    payload = base64.b64encode(json.dumps(parametros).encode("utf-8")).decode("ascii")
    url = URL_BASE.format(payload=payload)
    for verificar_tls in (True, False):
        try:
            resposta = requests.get(url, headers=CABECALHOS, timeout=TIMEOUT, verify=verificar_tls)
            if resposta.status_code != 200:
                continue
            return resposta.json()
        except Exception:  # noqa: BLE001
            continue
    return None


def baixar_cadastro():
    """Percorre as páginas do B3. Devolve {raiz: {cnpj, nome, nome_pregao}}."""
    mapa = {}
    for numero in range(1, MAX_PAGINAS + 1):
        corpo = _pagina(numero)
        if not isinstance(corpo, dict):
            break
        resultados = corpo.get("results") or []
        if not resultados:
            break

        for item in resultados:
            raiz = (item.get("issuingCompany") or "").upper().strip()
            cnpj = normalizar_cnpj(item.get("cnpj"))
            if not raiz or not cnpj:
                continue
            # Primeira ocorrência vence: o B3 repete a empresa em segmentos.
            mapa.setdefault(raiz, {
                "cnpj": cnpj,
                "nome": (item.get("companyName") or "").strip(),
                "nome_pregao": (item.get("tradingName") or "").strip(),
            })

        pagina = corpo.get("page") or {}
        try:
            if numero >= int(pagina.get("totalPages", 0)):
                break
        except (TypeError, ValueError):
            pass
        time.sleep(0.3)  # o sistema do B3 não gosta de rajada

    return mapa


def gravar(mapa, caminho=None):
    caminho = caminho or ARQUIVO
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump({"gerado_em": time.strftime("%Y-%m-%d"), "empresas": mapa},
                  arquivo, ensure_ascii=False, indent=1, sort_keys=True)
    return caminho


_memoria = {"mapa": None}


def carregar(caminho=None):
    """Cadastro do arquivo versionado. {} se ele não existir."""
    if _memoria["mapa"] is not None:
        return _memoria["mapa"]
    caminho = caminho or ARQUIVO
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
        mapa = dados.get("empresas") or {}
    except (OSError, ValueError, AttributeError):
        mapa = {}
    _memoria["mapa"] = mapa
    return mapa


def cnpj_do_ticker(ticker, caminho=None):
    """CNPJ da companhia por trás do ticker, ou None."""
    raiz = raiz_do_ticker(ticker)
    if not raiz:
        return None
    empresa = carregar(caminho).get(raiz)
    return empresa.get("cnpj") if empresa else None


def limpar_memoria():
    _memoria["mapa"] = None


if __name__ == "__main__":
    mapa = baixar_cadastro()
    if not mapa:
        raise SystemExit("Nao consegui ler o cadastro de listadas do B3.")
    caminho = gravar(mapa)
    print(f"{len(mapa)} empresas gravadas em {caminho}")
