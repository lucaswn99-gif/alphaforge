"""P/VP de FII a partir do Informe Mensal da CVM.

Leitura apenas. A base vem de `atualizar_fundos_cvm.py`.

    ticker -> raiz (cadastro_fii) -> CNPJ -> VP da cota (CVM) -> P/VP

O P/VP é o gatilho de entrada de FII no radar. Vindo do `.info` do Yahoo ele
falhava em metade dos fundos e é bloqueado de datacenter; vindo do informe
mensal é dado que o próprio fundo declara ao regulador, e a competência viaja
junto para a tela dizer de quando é.
"""

import os
import sqlite3
import threading

from modules import cadastro_fii

BANCO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fundos_cvm.db"
)

# FII fora desta faixa de P/VP é dado corrompido, não oportunidade.
PVP_MINIMO = 0.05
PVP_MAXIMO = 10.0

_lock = threading.Lock()
_cache = {}


def _conectar(banco=None):
    caminho = banco or BANCO
    if not os.path.exists(caminho):
        return None
    try:
        conexao = sqlite3.connect(caminho, check_same_thread=False)
        conexao.row_factory = sqlite3.Row
        return conexao
    except sqlite3.Error:
        return None


def base_disponivel(banco=None):
    conexao = _conectar(banco)
    if conexao is None:
        return False
    try:
        conexao.execute("SELECT 1 FROM fundos LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def informe_por_cnpj(cnpj, banco=None):
    if not cnpj:
        return None
    with _lock:
        if cnpj in _cache:
            return _cache[cnpj]

    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute("SELECT * FROM fundos WHERE cnpj = ?", (cnpj,)).fetchone()
        if linha is None and len(cnpj) >= 8:
            # Mesma razão do lado das ações: CNPJ de filial no cadastro do B3.
            linha = conexao.execute(
                "SELECT * FROM fundos WHERE cnpj LIKE ? LIMIT 1", (cnpj[:8] + "%",)
            ).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()

    registro = dict(linha) if linha else None
    with _lock:
        _cache[cnpj] = registro
    return registro


def cnpj_pelo_informe(ticker, banco=None):
    """CNPJ pelo código de negociação, quando o próprio informe o publica.

    Quando esta tabela existe, o cadastro de fundos do B3 deixa de ser um
    ponto de falha: o vínculo ticker->CNPJ vem da mesma fonte que o valor
    patrimonial, e é exato em vez de inferido pela raiz de 4 letras.
    """
    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute(
            "SELECT cnpj FROM tickers WHERE ticker = ?",
            ((ticker or "").upper().strip(),)).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()
    return linha["cnpj"] if linha else None


def pvp_do_fii(ticker, preco, banco=None, caminho_cadastro=None):
    """{pvp, vp_por_cota, competencia, cnpj, disponivel}. Nunca levanta."""
    resultado = {"pvp": None, "vp_por_cota": None, "competencia": None,
                 "cnpj": None, "disponivel": False, "origem": "cvm-informe"}

    # Informe primeiro (exato), cadastro do B3 depois (por raiz).
    cnpj = cnpj_pelo_informe(ticker, banco) or cadastro_fii.cnpj_do_ticker(
        ticker, caminho_cadastro)
    if not cnpj:
        return resultado
    resultado["cnpj"] = cnpj

    informe = informe_por_cnpj(cnpj, banco)
    if not informe:
        return resultado

    resultado["disponivel"] = True
    resultado["competencia"] = informe.get("competencia")

    try:
        vp_cota = float(informe.get("vp_por_cota") or 0.0)
        preco = float(preco or 0.0)
    except (TypeError, ValueError):
        return resultado

    if vp_cota <= 0 or preco <= 0:
        return resultado

    resultado["vp_por_cota"] = vp_cota
    pvp = preco / vp_cota
    if PVP_MINIMO <= pvp <= PVP_MAXIMO:
        resultado["pvp"] = pvp
    return resultado


def limpar_cache():
    with _lock:
        _cache.clear()
