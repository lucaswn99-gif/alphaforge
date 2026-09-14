"""Carteira do assinante: posições, aportes e validação de ticker.

Leitura e escrita da tabela `carteiras` (esquema em `modules/contas.py`). Sem
FastAPI aqui de propósito — regra de negócio que só roda dentro de uma rota
não tem como ser testada sem subir HTTP.

Três decisões que valem explicação:

  - **Postar um ticker que já existe faz MERGE, não duplica.** Uma carteira
    tem UMA posição por papel; comprar de novo muda quantidade e preço médio.
    O merge é média ponderada pela quantidade, que é a definição contábil de
    preço médio. Guardar duas linhas do mesmo papel seria erro de contabilidade
    disfarçado de histórico — e o histórico de aportes, quando existir, é outra
    tabela, não esta.

  - **A validação tem duas camadas, e elas respondem perguntas diferentes.**
    A regex diz se o código TEM FORMA de ticker da B3; o registro diz se ele
    EXISTE. `ZZZZ3` passa na primeira e falha na segunda. Uma camada só deixa
    entrar lixo ou barra papel legítimo.

  - **Papel não encontrado no registro entra marcado, não é recusado.** As
    nossas bases envelhecem (o cadastro da B3 é um JSON versionado, o IFIX tem
    cache de 12 h). Recusar o cadastro de um papel real por falha da NOSSA base
    é o pior desfecho possível para quem paga. Ele entra com
    `verificado = False` e a tela diz isso. Mesmo princípio do "não apurado
    nunca vira zero": não conhecemos não é o mesmo que não existe.
"""

import re
import sqlite3
from datetime import datetime, timezone

from modules import contas

# Classes de ação negociadas na B3: 3 (ON), 4 (PN), 5 (PNA), 6 (PNB), 7 (PNC),
# 8 (PND) e 11 (unit/FII/ETF).
#
# As classes 5 a 8 estão aqui porque o próprio AlphaForge as varre: BRSR6 e
# CPLE6 estão no UNIVERSO_BESST, USIM5 e CPLE6 estão na carteira do IBOV. Uma
# regex que aceitasse só 3, 4 e 11 recusaria o cadastro de papéis que o nosso
# scanner aprova — contradição dentro do produto.
FORMA_TICKER = re.compile(r"^[A-Z]{4}(3|4|5|6|7|8|11)$")

# Guarda-corpo, não regra de negócio: carteira com mais posições que isso é
# quase certamente script em laço, não investidor.
MAXIMO_POSICOES = 300

# Acima disso o número é dedo errado no teclado, não posição.
QUANTIDADE_MAXIMA = 1_000_000_000.0
PRECO_MAXIMO = 1_000_000.0


class ErroCarteira(ValueError):
    """Entrada recusada, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def normalizar_ticker(bruto):
    return str(bruto or "").strip().upper()


def classificar(ticker):
    """(classe, verificado). Nunca levanta: registro fora do ar não pode
    impedir alguém de cadastrar a própria carteira.

    A ordem importa. Ação primeiro (é o caso comum), depois os registros de
    fundo. Terminando em 11 sem estar em registro nenhum, fica "desconhecida"
    — pode ser unit nova, FII recém-listado ou erro de digitação, e nós não
    temos como saber qual.
    """
    ticker = normalizar_ticker(ticker)

    try:
        from modules import cadastro_b3
        if cadastro_b3.cnpj_do_ticker(ticker):
            return "acao", True
    except Exception:  # noqa: BLE001
        pass

    if ticker.endswith("11"):
        try:
            from modules import etfs_b3
            if etfs_b3.categoria_do_ticker(ticker):
                return "etf", True
        except Exception:  # noqa: BLE001
            pass

        try:
            from modules import segmentos_fii
            if segmentos_fii.segmento_do_ticker(ticker) != segmentos_fii.NAO_CLASSIFICADO:
                return "fii", True
        except Exception:  # noqa: BLE001
            pass

        try:
            from modules import fundamentos_fii
            if fundamentos_fii.cnpj_pelo_informe(ticker):
                return "fii", True
        except Exception:  # noqa: BLE001
            pass

    return "desconhecida", False


def validar(ticker, quantidade, preco_medio):
    """(ticker, quantidade, preco_medio, classe, verificado) ou levanta.

    Levanta `ErroCarteira` com a mensagem que a tela mostra — mensagem de erro
    genérica em formulário de dinheiro faz o usuário desistir sem saber o que
    corrigir.
    """
    ticker = normalizar_ticker(ticker)
    if not ticker:
        raise ErroCarteira("Informe o código do papel.")
    if not FORMA_TICKER.match(ticker):
        raise ErroCarteira(
            f"'{ticker}' não tem forma de ticker da B3: são quatro letras "
            "seguidas de 3, 4, 5, 6, 7, 8 ou 11 (ex.: PETR4, CPLE6, TAEE11).")

    try:
        quantidade = float(quantidade)
        preco_medio = float(preco_medio)
    except (TypeError, ValueError):
        raise ErroCarteira("Quantidade e preço médio precisam ser números.")

    if quantidade != quantidade or preco_medio != preco_medio:   # NaN
        raise ErroCarteira("Quantidade e preço médio precisam ser números.")
    if quantidade <= 0:
        raise ErroCarteira("A quantidade precisa ser maior que zero.")
    if preco_medio <= 0:
        raise ErroCarteira("O preço médio precisa ser maior que zero.")
    if quantidade > QUANTIDADE_MAXIMA:
        raise ErroCarteira("Quantidade acima do limite aceito.")
    if preco_medio > PRECO_MAXIMO:
        raise ErroCarteira("Preço médio acima do limite aceito.")

    classe, verificado = classificar(ticker)
    return ticker, quantidade, preco_medio, classe, verificado


def media_ponderada(qtd_atual, pm_atual, qtd_nova, pm_novo):
    """Preço médio depois do aporte. É a definição contábil: custo total
    dividido por quantidade total."""
    total = qtd_atual + qtd_nova
    if total <= 0:
        raise ErroCarteira("Quantidade resultante precisa ser maior que zero.")
    return ((qtd_atual * pm_atual) + (qtd_nova * pm_novo)) / total


def _linha(registro):
    bruto = dict(registro)
    quantidade = float(bruto["quantidade"])
    preco_medio = float(bruto["preco_medio"])
    return {
        "ticker": bruto["ticker"],
        "quantidade": quantidade,
        "preco_medio": round(preco_medio, 4),
        "custo_total": round(quantidade * preco_medio, 2),
        "classe": bruto.get("classe") or "desconhecida",
        "verificado": bool(bruto.get("verificado")),
        # None significa herdar a filosofia da carteira. A tela precisa saber
        # a diferença entre herdar e ter escolhido o mesmo valor por acaso.
        "filosofia": bruto.get("filosofia"),
        "atualizado_em": bruto.get("atualizado_em"),
    }


def listar(usuario_id):
    """Posições, custo total e peso de cada uma.

    O peso é sobre CUSTO, não sobre valor de mercado: o preço de mercado é
    trabalho do Pilar 2 em diante, e misturar as duas bases aqui produziria um
    percentual que não bate com nenhuma das duas.
    """
    contas.iniciar()
    with contas._conectar() as cx:
        linhas = cx.execute(
            "SELECT * FROM carteiras WHERE usuario_id = ? ORDER BY ticker",
            (usuario_id,)).fetchall()

    posicoes = [_linha(l) for l in linhas]
    custo = sum(p["custo_total"] for p in posicoes)
    for posicao in posicoes:
        posicao["peso_pct"] = round(posicao["custo_total"] / custo * 100.0, 2) if custo else None

    return {
        "posicoes": posicoes,
        "custo_total": round(custo, 2),
        "posicoes_total": len(posicoes),
        "nao_verificados": sum(1 for p in posicoes if not p["verificado"]),
    }


def adicionar(usuario_id, ticker, quantidade, preco_medio):
    """Cria a posição, ou incorpora o aporte à que já existe.

    Devolve (linha, foi_merge) — a tela diz "posição criada" ou "aporte
    incorporado", que são eventos diferentes para quem está olhando.
    """
    ticker, quantidade, preco_medio, classe, verificado = validar(
        ticker, quantidade, preco_medio)

    contas.iniciar()
    agora = _agora()
    with contas._conectar() as cx:
        atual = cx.execute(
            "SELECT * FROM carteiras WHERE usuario_id = ? AND ticker = ?",
            (usuario_id, ticker)).fetchone()

        if atual:
            nova_qtd = float(atual["quantidade"]) + quantidade
            novo_pm = media_ponderada(float(atual["quantidade"]),
                                      float(atual["preco_medio"]),
                                      quantidade, preco_medio)
            cx.execute(
                "UPDATE carteiras SET quantidade = ?, preco_medio = ?, "
                "classe = ?, verificado = ?, atualizado_em = ? "
                "WHERE usuario_id = ? AND ticker = ?",
                (nova_qtd, novo_pm, classe, int(verificado), agora,
                 usuario_id, ticker))
            foi_merge = True
        else:
            quantas = cx.execute(
                "SELECT COUNT(*) FROM carteiras WHERE usuario_id = ?",
                (usuario_id,)).fetchone()[0]
            if quantas >= MAXIMO_POSICOES:
                raise ErroCarteira(
                    f"Limite de {MAXIMO_POSICOES} posições por carteira atingido.")
            try:
                cx.execute(
                    "INSERT INTO carteiras (usuario_id, ticker, quantidade, "
                    "preco_medio, classe, verificado, criado_em, atualizado_em) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (usuario_id, ticker, quantidade, preco_medio, classe,
                     int(verificado), agora, agora))
            except sqlite3.IntegrityError:
                # Corrida entre duas abas do mesmo usuário: a linha nasceu
                # entre o SELECT e o INSERT. Recomeça como merge.
                cx.rollback()
                return adicionar(usuario_id, ticker, quantidade, preco_medio)
            foi_merge = False

        linha = cx.execute(
            "SELECT * FROM carteiras WHERE usuario_id = ? AND ticker = ?",
            (usuario_id, ticker)).fetchone()

    return _linha(linha), foi_merge


def atualizar(usuario_id, ticker, quantidade, preco_medio):
    """Corrige a posição (substitui, não soma). Devolve a linha ou None.

    Separado de `adicionar` porque são intenções opostas: aporte SOMA, correção
    SUBSTITUI. Um endpoint só para as duas coisas seria um campo booleano
    decidindo o que acontece com o dinheiro de alguém.
    """
    ticker, quantidade, preco_medio, classe, verificado = validar(
        ticker, quantidade, preco_medio)

    contas.iniciar()
    with contas._conectar() as cx:
        alterou = cx.execute(
            "UPDATE carteiras SET quantidade = ?, preco_medio = ?, classe = ?, "
            "verificado = ?, atualizado_em = ? WHERE usuario_id = ? AND ticker = ?",
            (quantidade, preco_medio, classe, int(verificado), _agora(),
             usuario_id, ticker)).rowcount
        if not alterou:
            return None
        linha = cx.execute(
            "SELECT * FROM carteiras WHERE usuario_id = ? AND ticker = ?",
            (usuario_id, ticker)).fetchone()
    return _linha(linha)


def remover(usuario_id, ticker):
    """True se removeu. O `usuario_id` está no WHERE de propósito: sem ele,
    saber o ticker de outra pessoa bastaria para apagar a posição dela."""
    ticker = normalizar_ticker(ticker)
    contas.iniciar()
    with contas._conectar() as cx:
        return cx.execute(
            "DELETE FROM carteiras WHERE usuario_id = ? AND ticker = ?",
            (usuario_id, ticker)).rowcount > 0
