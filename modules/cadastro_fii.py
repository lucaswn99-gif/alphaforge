"""Ticker de FII -> CNPJ do fundo.

Mesma ideia do cadastro de ações, para o outro lado do mercado: o Informe
Mensal da CVM é por CNPJ, e o radar trabalha com ticker.

    HGLG11 -> raiz HGLG -> CNPJ do fundo -> valor patrimonial da cota

Fonte primária é o sistema de fundos listados do B3, que devolve `acronym` (a
raiz de 4 letras) e `cnpj`. O resultado é gravado num JSON versionado, e em
runtime só esse arquivo é lido.

    python modules/cadastro_fii.py

VERIFICAÇÃO PENDENTE: não consegui confirmar o formato de resposta deste
endpoint antes de escrever — a chamada é feita de forma defensiva e o script
imprime o que recebeu. Se ele falhar, `cadastro_fii_manual.json` na raiz do
projeto permite mapear os fundos à mão (são poucos), no formato:

    {"HGLG": "11728688000147", "BTLG": "11839593000109"}
"""

import base64
import json
import os
import re
import time

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_BASE = ("https://sistemaswebb3-listados.b3.com.br/fundsProxy/fundsCall/"
            "GetListedFundsSIG/{payload}")
CABECALHOS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
}
TIMEOUT = 15
TIPO_FII = 7
TAMANHO_PAGINA = 100
MAX_PAGINAS = 40

PASTA = os.path.dirname(os.path.abspath(__file__))
ARQUIVO = os.path.join(PASTA, "cadastro_fii.json")
ARQUIVO_MANUAL = os.path.join(os.path.dirname(PASTA), "cadastro_fii_manual.json")

_RAIZ = re.compile(r"^([A-Z0-9]{4})\d{1,2}$")

# Campos onde o B3 pode devolver a sigla e o CNPJ; aceitamos qualquer um.
CHAVES_SIGLA = ("acronym", "issuingCompany", "tradingCode", "codeCVM")
CHAVES_CNPJ = ("cnpj", "companyCnpj", "cnpjFund")

VERBOSO = False


def raiz_do_ticker(ticker):
    casou = _RAIZ.match((ticker or "").upper().strip())
    return casou.group(1) if casou else None


def normalizar_cnpj(bruto):
    digitos = re.sub(r"\D", "", str(bruto or ""))
    if not digitos or set(digitos) == {"0"}:
        return None
    return digitos.zfill(14) if len(digitos) <= 14 else None


def _pagina(numero):
    parametros = {"typeFund": TIPO_FII, "pageNumber": numero, "pageSize": TAMANHO_PAGINA}
    payload = base64.b64encode(json.dumps(parametros).encode("utf-8")).decode("ascii")
    url = URL_BASE.format(payload=payload)
    for verificar_tls in (True, False):
        try:
            resposta = requests.get(url, headers=CABECALHOS, timeout=TIMEOUT,
                                    verify=verificar_tls)
            if resposta.status_code != 200:
                if VERBOSO:
                    print(f"   pagina {numero}: HTTP {resposta.status_code}")
                continue
            return resposta.json()
        except Exception as exc:  # noqa: BLE001
            if VERBOSO:
                print(f"   pagina {numero}: {type(exc).__name__}: {exc}")
            continue
    return None


def _extrair(item):
    sigla = ""
    for chave in CHAVES_SIGLA:
        valor = str(item.get(chave) or "").strip().upper()
        if valor and valor.isalnum() and 3 <= len(valor) <= 6:
            sigla = valor[:4]
            break
    cnpj = None
    for chave in CHAVES_CNPJ:
        cnpj = normalizar_cnpj(item.get(chave))
        if cnpj:
            break
    return sigla, cnpj


def baixar_cadastro():
    mapa = {}
    for numero in range(1, MAX_PAGINAS + 1):
        corpo = _pagina(numero)
        if corpo is None:
            break
        if isinstance(corpo, dict):
            resultados = corpo.get("results") or corpo.get("funds") or []
            pagina = corpo.get("page") or {}
        elif isinstance(corpo, list):
            resultados, pagina = corpo, {}
        else:
            break

        if VERBOSO and numero == 1 and resultados:
            print(f"   campos recebidos: {sorted(resultados[0])}")
        if not resultados:
            break

        for item in resultados:
            if not isinstance(item, dict):
                continue
            sigla, cnpj = _extrair(item)
            if sigla and cnpj:
                mapa.setdefault(sigla, {
                    "cnpj": cnpj,
                    "nome": str(item.get("companyName") or item.get("fundName") or "").strip(),
                })

        try:
            if numero >= int(pagina.get("totalPages", 0)):
                break
        except (TypeError, ValueError):
            pass
        time.sleep(0.3)
    return mapa


def gravar(mapa, caminho=None):
    caminho = caminho or ARQUIVO
    with open(caminho, "w", encoding="utf-8") as arquivo:
        json.dump({"gerado_em": time.strftime("%Y-%m-%d"), "fundos": mapa},
                  arquivo, ensure_ascii=False, indent=1, sort_keys=True)
    return caminho


_memoria = {"mapa": None}


def carregar(caminho=None):
    """Cadastro automático somado ao manual. O manual tem precedência."""
    if _memoria["mapa"] is not None:
        return _memoria["mapa"]

    mapa = {}
    try:
        with open(caminho or ARQUIVO, "r", encoding="utf-8") as arquivo:
            mapa = dict(json.load(arquivo).get("fundos") or {})
    except (OSError, ValueError, AttributeError):
        pass

    # Correção à mão sempre vence o cadastro automático.
    try:
        with open(ARQUIVO_MANUAL, "r", encoding="utf-8") as arquivo:
            for sigla, cnpj in (json.load(arquivo) or {}).items():
                normalizado = normalizar_cnpj(cnpj)
                if normalizado:
                    mapa[str(sigla).upper()[:4]] = {"cnpj": normalizado, "nome": "manual"}
    except (OSError, ValueError, AttributeError):
        pass

    _memoria["mapa"] = mapa
    return mapa


def cnpj_do_ticker(ticker, caminho=None):
    raiz = raiz_do_ticker(ticker)
    if not raiz:
        return None
    fundo = carregar(caminho).get(raiz)
    return fundo.get("cnpj") if fundo else None


def limpar_memoria():
    _memoria["mapa"] = None


if __name__ == "__main__":
    VERBOSO = True
    resultado = baixar_cadastro()
    if not resultado:
        raise SystemExit(
            "Nao consegui ler o cadastro de fundos do B3.\n"
            "Preencha cadastro_fii_manual.json com {\"HGLG\": \"<cnpj>\", ...}")
    print(f"{len(resultado)} fundos gravados em {gravar(resultado)}")
