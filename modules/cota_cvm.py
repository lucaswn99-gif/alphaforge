"""Cota diária de fundos de investimento — leitura apenas.

A base vem de `atualizar_cota_fundos_cvm.py` (Etapa B do item 3 do escopo —
ver a docstring de `modules/fundos.py` para o que essa etapa muda). Este
módulo só lê `cota_fundos_cvm.db`; quem escreve nele é o coletor, que roda
fora do serviço web, no mesmo desenho de `atualizar_fundos_cvm.py` (FII) e
`fundamentos_fii.py` (leitura).

**Sem a base, ou sem o fundo nela: tudo aqui devolve `None`, nunca um valor
inventado.** Quem decide o que fazer com `None` é `modules/fundos.py`
(mantém `apurado=False`, valor mostrado é o aplicado) e `modules/backtest.py`
(fundo sem cota no início da janela vai para `sem_historico`).
"""

import os
import sqlite3
from datetime import date, datetime, timedelta

BANCO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cota_fundos_cvm.db"
)

# Cota diária de verdade não pula pregão por mais que uma folga curta de
# feriado prolongado — 10 dias cobre isso com folga. Além disso, "a cota mais
# próxima" deixaria de significar a data pedida.
TOLERANCIA_DIAS = 10


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


def _converter_data(bruto):
    if isinstance(bruto, date):
        return bruto
    return datetime.strptime(str(bruto), "%Y-%m-%d").date()


def base_disponivel(banco=None):
    conexao = _conectar(banco)
    if conexao is None:
        return False
    try:
        conexao.execute("SELECT 1 FROM cotas LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def cota_na_data(cnpj, data, banco=None, tolerancia_dias=TOLERANCIA_DIAS):
    """Cota do pregão mais próximo, NA DATA OU ANTES dela — nunca depois (usar
    cota futura para marcar um ponto passado seria viés de leitura adiantada).
    `None` se a base não existir, o fundo não tiver sido coletado, ou não
    houver pregão dentro de `tolerancia_dias` anteriores a `data`."""
    if not cnpj:
        return None
    data = _converter_data(data)
    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        limite = (data - timedelta(days=tolerancia_dias)).isoformat()
        linha = conexao.execute(
            "SELECT valor_cota FROM cotas WHERE cnpj = ? AND data <= ? AND data >= ? "
            "ORDER BY data DESC LIMIT 1",
            (cnpj, data.isoformat(), limite)).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()
    return float(linha["valor_cota"]) if linha else None


def cota_mais_recente(cnpj, banco=None):
    """(valor_cota, data_iso) da cota mais nova conhecida do fundo, ou
    `(None, None)`. Sem tolerância de data: é "a mais recente que existe",
    não "a mais recente perto de uma data-alvo"."""
    if not cnpj:
        return None, None
    conexao = _conectar(banco)
    if conexao is None:
        return None, None
    try:
        linha = conexao.execute(
            "SELECT valor_cota, data FROM cotas WHERE cnpj = ? "
            "ORDER BY data DESC LIMIT 1", (cnpj,)).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()
    if not linha:
        return None, None
    return float(linha["valor_cota"]), linha["data"]
