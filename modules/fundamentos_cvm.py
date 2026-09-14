"""Fundamentos por ticker, a partir do balanço publicado na CVM.

Leitura apenas. A base é construída fora do serviço por
`atualizar_fundamentos_cvm.py` e versionada junto do código — o Render não
baixa nem processa a DFP em tempo de requisição.

Caminho do dado:

    ticker -> raiz (cadastro_b3) -> CNPJ -> balanço (CVM) -> múltiplos

Duas tabelas, dois documentos. `fundamentos` é a DFP: exercício fechado, é o
que responde pergunta que só existe em ano encerrado. `balanco_itr` é o saldo
patrimonial trimestral, usado no P/VP para que o denominador acompanhe o preço
com que é dividido. Base gerada por coletor antigo não tem a segunda tabela, e
o módulo trata isso como ausência — o P/VP volta a sair só da DFP, como antes.

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

# Quanto o patrimônio do trimestre pode se afastar do patrimônio do exercício
# antes de deixar de ser notícia e passar a ser suspeita de mapeamento errado.
# Patrimônio líquido não triplica em um trimestre; o plano de contas de banco,
# lido errado, já fez o patrimônio do Itaú aparecer como o ativo dele. Se isso
# entrar pelo ITR o P/VP despenca e o papel sobe ao topo dos descontados — o
# tipo de erro que o usuário não tem como perceber. Fora da faixa, o trimestre
# é descartado e vale a DFP, que é auditada.
VARIACAO_MAXIMA_PATRIMONIO = 3.0

# Quanto a quantidade de ações declarada no FRE pode divergir da deduzida de
# lucro/LPA antes de uma das duas ser considerada errada. As duas medem coisas
# ligeiramente diferentes — o FRE é o emitido numa data, lucro/LPA é a média
# ponderada do exercício — então divergência pequena é esperada e não é erro.
# Ordem de grandeza diferente é coluna trocada, e o sintoma seria um VPA
# deslocado por um fator de dez, que produz um P/VP plausível e falso.
DIVERGENCIA_MAXIMA_ACOES = 5.0

_lock = threading.Lock()
_cache = {}
_cache_itr = {}
_cache_acoes = {}


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
        if linha is None and len(cnpj) >= 8:
            # O B3 às vezes cadastra o CNPJ de uma filial e a CVM publica pelo
            # da matriz: TUPY3 é 84683374/0003-00 no B3 e /0001-49 na CVM. Os 8
            # primeiros dígitos são a raiz da empresa, e na base coletada
            # nenhuma raiz é compartilhada por duas companhias.
            linha = conexao.execute(
                "SELECT * FROM fundamentos WHERE cnpj LIKE ? ORDER BY ano DESC LIMIT 1",
                (cnpj[:8] + "%",)
            ).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()

    registro = dict(linha) if linha else None
    with _lock:
        _cache[cnpj] = registro
    return registro


def base_itr_disponivel(banco=None):
    """A base pode ter sido gerada por um coletor anterior ao ITR."""
    conexao = _conectar(banco)
    if conexao is None:
        return False
    try:
        conexao.execute("SELECT 1 FROM balanco_itr LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def balanco_itr_por_cnpj(cnpj, banco=None):
    """Balanço trimestral mais recente da companhia, ou None.

    Devolve None — e não um dicionário vazio — quando a tabela não existe, para
    que uma base gerada antes do ITR siga funcionando exatamente como antes em
    vez de passar a calcular P/VP com patrimônio ausente.
    """
    if not cnpj:
        return None
    with _lock:
        if cnpj in _cache_itr:
            return _cache_itr[cnpj]

    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute(
            "SELECT * FROM balanco_itr WHERE cnpj = ? "
            "ORDER BY data_ref DESC LIMIT 1", (cnpj,)
        ).fetchone()
        if linha is None and len(cnpj) >= 8:
            # Mesma raiz de CNPJ que `balanco_por_cnpj`: o B3 às vezes cadastra
            # a filial e a CVM publica pela matriz.
            linha = conexao.execute(
                "SELECT * FROM balanco_itr WHERE cnpj LIKE ? "
                "ORDER BY data_ref DESC LIMIT 1", (cnpj[:8] + "%",)
            ).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()

    registro = dict(linha) if linha else None
    with _lock:
        _cache_itr[cnpj] = registro
    return registro


def base_acoes_disponivel(banco=None):
    conexao = _conectar(banco)
    if conexao is None:
        return False
    try:
        conexao.execute("SELECT 1 FROM acoes_cia LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        conexao.close()


def acoes_por_cnpj(cnpj, banco=None):
    """Quantidade de ações declarada no FRE, ou None."""
    if not cnpj:
        return None
    with _lock:
        if cnpj in _cache_acoes:
            return _cache_acoes[cnpj]

    conexao = _conectar(banco)
    if conexao is None:
        return None
    try:
        linha = conexao.execute(
            "SELECT * FROM acoes_cia WHERE cnpj = ?", (cnpj,)).fetchone()
        if linha is None and len(cnpj) >= 8:
            linha = conexao.execute(
                "SELECT * FROM acoes_cia WHERE cnpj LIKE ? "
                "ORDER BY data_ref DESC LIMIT 1", (cnpj[:8] + "%",)).fetchone()
    except sqlite3.Error:
        linha = None
    finally:
        conexao.close()

    registro = dict(linha) if linha else None
    with _lock:
        _cache_acoes[cnpj] = registro
    return registro


def historico_por_cnpj(cnpj, banco=None):
    """Todos os exercícios da companhia, do mais antigo ao mais recente.

    `balanco_por_cnpj` devolve só o exercício mais recente, que responde
    "quanto vale hoje". Constância de lucro é outra pergunta — precisa da
    série — e alcançar a conexão privada a partir de outro módulo para
    respondê-la deixaria o esquema do banco espalhado pelo projeto.
    """
    if not cnpj:
        return []
    conexao = _conectar(banco)
    if conexao is None:
        return []
    try:
        linhas = conexao.execute(
            "SELECT * FROM fundamentos WHERE cnpj = ? ORDER BY ano", (cnpj,)
        ).fetchall()
        if not linhas and len(cnpj) >= 8:
            linhas = conexao.execute(
                "SELECT * FROM fundamentos WHERE cnpj LIKE ? ORDER BY ano",
                (cnpj[:8] + "%",)
            ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conexao.close()
    return [dict(linha) for linha in linhas]


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


def _patrimonio_para_pvp(balanco, itr):
    """Escolhe o patrimônio do P/VP e diz de onde ele veio.

    O P/VP divide preço de HOJE por patrimônio por ação. Deixar o numerador
    andar todo dia e o denominador parado no último 31/12 é o que separa este
    múltiplo do que qualquer fonte que acompanhe o trimestre publica — a
    diferença chega a quinze meses logo antes de a DFP seguinte sair.

    ROE e margem não entram nessa troca: os dois casam lucro com patrimônio, e
    lucro aqui é anual. Cruzar lucro de doze meses com patrimônio de meio de
    ano produz um ROE que nem a DFP nem o ITR sustentam.

    Devolve (patrimonio, origem, data). Fora da faixa de plausibilidade, ou sem
    trimestre mais novo, volta o da DFP.
    """
    anual = _positivo(balanco.get("patrimonio_liquido"))
    ano = balanco.get("ano")
    data_anual = f"{ano}-12-31" if ano else None

    if not itr:
        return anual, "dfp", data_anual

    data_itr = str(itr.get("data_ref") or "")
    if not data_itr or (data_anual and data_itr <= data_anual):
        return anual, "dfp", data_anual

    trimestral = _positivo(itr.get("patrimonio_liquido"))
    if trimestral is None:
        return anual, "dfp", data_anual

    if anual and (trimestral > VARIACAO_MAXIMA_PATRIMONIO * anual
                  or anual > VARIACAO_MAXIMA_PATRIMONIO * trimestral):
        return anual, "dfp", data_anual

    return trimestral, "itr", data_itr


def _acoes_em_circulacao(balanco, registro_acoes):
    """Quantas ações dividem o patrimônio. Devolve (quantidade, origem).

    Duas fontes, nesta ordem:

    * `fre` — a quantidade que a companhia declarou no Formulário de Referência (item 17.1).
      É o que cabe no VPA, que é conceito de data, e existe mesmo para quem
      não publica LPA.
    * `lpa` — lucro / LPA, a dedução que era a única fonte até aqui. Continua
      como segunda opção, porque nem toda companhia aparece no FRE.

    Quando as duas existem e discordam por ordem de grandeza, a declarada é
    recusada e vale a deduzida: lucro e LPA saem do mesmo demonstrativo
    auditado, então elas erram juntas ou não erram.
    """
    lucro = _numero(balanco.get("lucro_liquido"))
    lpa = _numero(balanco.get("lpa_on"))
    deduzido = None
    if lucro is not None and lpa:
        candidato = lucro / lpa
        if candidato > 0:
            deduzido = candidato

    declarado = None
    if registro_acoes:
        declarado = _positivo(registro_acoes.get("total"))

    if declarado is None:
        return deduzido, ("lpa" if deduzido else None)
    if deduzido is None:
        return declarado, "fre"

    if (declarado > DIVERGENCIA_MAXIMA_ACOES * deduzido
            or deduzido > DIVERGENCIA_MAXIMA_ACOES * declarado):
        return deduzido, "lpa"
    return declarado, "fre"


def multiplos_do_ticker(ticker, preco=None, banco=None, caminho_cadastro=None):
    """Múltiplos calculados a partir do balanço. Sempre devolve um dicionário.

    Campo que não fecha volta None — nunca zero. `preco` é necessário para P/L
    e P/VP; sem ele, ROE e margem ainda saem.

    `patrimonio_origem` e `patrimonio_data` dizem de que documento saiu o
    denominador do P/VP: "dfp" para exercício fechado, "itr" para trimestre.
    `acoes_origem` diz de onde veio a quantidade de ações: "fre" quando a
    companhia declarou, "lpa" quando foi deduzida de lucro/LPA. Os três viajam
    junto para a tela poder declarar a procedência, como já faz com o
    exercício — um múltiplo sem data é um múltiplo que não dá para conferir.
    """
    resultado = {"pl": None, "pvp": None, "roe": None, "margem_liq": None,
                 "origem": "cvm", "exercicio": None, "cnpj": None,
                 "denominacao": None, "disponivel": False,
                 "patrimonio_origem": None, "patrimonio_data": None,
                 "acoes": None, "acoes_origem": None, "vpa": None}

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

    itr = balanco_itr_por_cnpj(cnpj, banco)

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

    # P/VP = preço / VPA. O patrimônio vem do documento mais recente que
    # resista à faixa de plausibilidade; a quantidade de ações, da declaração
    # da companhia, caindo para lucro/LPA quando ela não existe.
    patrimonio_pvp, origem_pat, data_pat = _patrimonio_para_pvp(balanco, itr)
    resultado["patrimonio_origem"] = origem_pat
    resultado["patrimonio_data"] = data_pat

    acoes, origem_acoes = _acoes_em_circulacao(balanco, acoes_por_cnpj(cnpj, banco))
    resultado["acoes"] = acoes
    resultado["acoes_origem"] = origem_acoes

    if preco and patrimonio_pvp and acoes:
        vpa = patrimonio_pvp / acoes
        if vpa > 0:
            resultado["vpa"] = vpa
            pvp = preco / vpa
            if 0 < pvp <= PVP_MAXIMO_PLAUSIVEL:
                resultado["pvp"] = pvp

    return resultado


def limpar_cache():
    with _lock:
        _cache.clear()
        _cache_itr.clear()
        _cache_acoes.clear()
