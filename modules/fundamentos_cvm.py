"""Fundamentos por ticker, a partir do balanço publicado na CVM.

Leitura apenas. A base é construída fora do serviço por
`atualizar_fundamentos_cvm.py` e versionada junto do código — o Render não
baixa nem processa a DFP em tempo de requisição.

Caminho do dado:

    ticker -> raiz (cadastro_b3) -> CNPJ -> balanço (CVM) -> múltiplos

Existe porque o Yahoo recusa o endpoint de múltiplos vindo de datacenter. Aqui
o número sai de demonstração auditada, e o exercício de referência viaja junto
para a tela poder dizer de quando ele é.
"""

import os
import sqlite3
import threading

from modules import cadastro_b3

BANCO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fundamentos_cvm.db"
)

# Mesmo teto usado nos fundamentos do Yahoo: acima disso é dado corrompido.
ROE_MAXIMO_PLAUSIVEL = 200.0
PL_MAXIMO_PLAUSIVEL = 1000.0
PVP_MAXIMO_PLAUSIVEL = 100.0

_lock = threading.Lock()
_cache = {}


def _conectar(banco=None):
    caminho = banco or BANCO
    if not os.path.exists(caminho):
        return None
    try:
        # check_same_thread=False: o FastAPI serve as rotas síncronas num pool.
        conexao = sqlite3.connect(caminho, check_same_thread=False)
        conexao.row_factory = sqlite3.Row
        return conexao
    except sqlite3.Error:
        return None


def base_disponivel(banco=None):
    caminho = banco or BANCO
    if not os.path.exists(caminho):
        return False
    conexao = _conectar(caminho)
    if conexao is None:
        return False
    try:
        conexao.execute("SELECT 1 FROM fundamentos LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def balanco_por_cnpj(cnpj, banco=None):
    """Exercício mais recente da companhia, ou None."""
    if not cnpj:
        return None
    with _lock:
        if cnpj in _cache:
            return _cache[cnpj]

    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute(
            "SELECT * FROM fundamentos WHERE cnpj = ? ORDER BY ano DESC LIMIT 1", (cnpj,)
        ).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()

    registro = dict(linha) if linha else None
    with _lock:
        _cache[cnpj] = registro
    return registro


def _positivo(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _numero(valor):
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero == numero else None


def multiplos_do_ticker(ticker, preco=None, banco=None, caminho_cadastro=None):
    """Múltiplos calculados a partir do balanço. Sempre devolve um dicionário.

    Campo que não fecha volta None — nunca zero. `preco` é necessário para P/L
    e P/VP; sem ele, ROE e margem ainda saem.
    """
    resultado = {"pl": None, "pvp": None, "roe": None, "margem_liq": None,
                 "origem": "cvm", "exercicio": None, "cnpj": None,
                 "denominacao": None, "disponivel": False}

    cnpj = cadastro_b3.cnpj_do_ticker(ticker, caminho_cadastro)
    if not cnpj:
        return resultado
    resultado["cnpj"] = cnpj

    balanco = balanco_por_cnpj(cnpj, banco)
    if not balanco:
        return resultado

    resultado["disponivel"] = True
    resultado["exercicio"] = balanco.get("ano")
    resultado["denominacao"] = balanco.get("denom_cia")

    patrimonio = _positivo(balanco.get("patrimonio_liquido"))
    lucro = _numero(balanco.get("lucro_liquido"))
    receita = _positivo(balanco.get("receita_liquida"))
    lpa = _numero(balanco.get("lpa_on"))
    preco = _positivo(preco)

    if lucro is not None and patrimonio:
        roe = lucro / patrimonio * 100.0
        if abs(roe) <= ROE_MAXIMO_PLAUSIVEL:
            resultado["roe"] = roe

    if lucro is not None and receita:
        resultado["margem_liq"] = lucro / receita * 100.0

    # P/L direto do lucro por ação publicado. Prejuízo não gera P/L: múltiplo
    # negativo não tem leitura útil.
    if preco and lpa and lpa > 0:
        pl = preco / lpa
        if 0 < pl <= PL_MAXIMO_PLAUSIVEL:
            resultado["pl"] = pl

    # P/VP = preço / VPA, com o número de ações implícito em lucro/LPA — a DFP
    # não publica a quantidade de ações diretamente.
    if preco and patrimonio and lpa and lucro:
        try:
            acoes = lucro / lpa
        except ZeroDivisionError:
            acoes = None
        if acoes and acoes > 0:
            vpa = patrimonio / acoes
            if vpa > 0:
                pvp = preco / vpa
                if 0 < pvp <= PVP_MAXIMO_PLAUSIVEL:
                    resultado["pvp"] = pvp

    return resultado


def limpar_cache():
    with _lock:
        _cache.clear()
