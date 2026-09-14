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


def _candidatos_cnpj(ticker, banco, caminho_cadastro):
    """CNPJs a tentar para um ticker, do mais confiável para o menos.

    1. `cadastro_fii_manual.json` — conferido por um humano, vence tudo.
    2. Tabela `tickers` do informe — vínculo publicado pela CVM... exceto que,
       quando o informe não traz código de negociação (é o caso hoje), esse
       vínculo é DEDUZIDO do ISIN, e dedução erra.
    3. Cadastro de fundos listados do B3, por raiz de 4 letras.

    Ordem só decide o desempate: `pvp_do_fii` testa todos e fica com o
    primeiro que produzir um P/VP plausível. Foi assim que o XPML11 apareceu:
    o ISIN apontava para um CNPJ cujo valor patrimonial dava P/VP de 0,004 —
    dado de outro fundo, não desconto de 99,6%.
    """
    candidatos = []
    for origem, cnpj in (
        ("manual", cadastro_fii.cnpj_manual_do_ticker(ticker)),
        ("informe-isin", cnpj_pelo_informe(ticker, banco)),
        ("cadastro-b3", cadastro_fii.cnpj_do_ticker(ticker, caminho_cadastro)),
    ):
        if cnpj and not any(cnpj == existente for _, existente in candidatos):
            candidatos.append((origem, cnpj))
    return candidatos


def pvp_do_fii(ticker, preco, banco=None, caminho_cadastro=None):
    """{pvp, vp_por_cota, competencia, cnpj, disponivel}. Nunca levanta."""
    resultado = {"pvp": None, "vp_por_cota": None, "competencia": None,
                 "cnpj": None, "disponivel": False, "origem": "cvm-informe"}

    try:
        preco = float(preco or 0.0)
    except (TypeError, ValueError):
        return resultado

    primeiro_com_informe = None
    for origem_cnpj, cnpj in _candidatos_cnpj(ticker, banco, caminho_cadastro):
        informe = informe_por_cnpj(cnpj, banco)
        if not informe:
            continue

        try:
            vp_cota = float(informe.get("vp_por_cota") or 0.0)
        except (TypeError, ValueError):
            continue

        parcial = {
            "pvp": None,
            "vp_por_cota": vp_cota if vp_cota > 0 else None,
            "competencia": informe.get("competencia"),
            "cnpj": cnpj,
            # O nome do fundo viaja junto para a tela poder dizer de QUAL
            # fundo veio o valor patrimonial — é o que flagra um vínculo
            # ticker->CNPJ errado sem abrir o banco. None em base antiga,
            # gravada antes desta coluna existir.
            "nome_fundo": informe.get("nome"),
            "disponivel": True,
            "origem": f"cvm-informe ({origem_cnpj})",
        }
        if primeiro_com_informe is None:
            primeiro_com_informe = parcial

        if vp_cota <= 0 or preco <= 0:
            continue

        pvp = preco / vp_cota
        if PVP_MINIMO <= pvp <= PVP_MAXIMO:
            # Candidato plausível: é este. Um P/VP fora da faixa não é
            # "oportunidade extrema", é quase sempre CNPJ trocado — seguimos
            # tentando o próximo em vez de publicar o número.
            parcial["pvp"] = pvp
            return parcial

    # Nenhum candidato plausível: devolvemos o primeiro que ao menos tinha
    # informe, com `pvp` None. A tela mostra "sem dados", que é a verdade —
    # temos um valor patrimonial, mas nenhum que case com o preço de mercado.
    return primeiro_com_informe or resultado


def limpar_cache():
    with _lock:
        _cache.clear()
