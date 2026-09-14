"""Rebalanceamento por aporte: para onde mandar o próximo dinheiro.

Função pura — recebe posições, preços, alvos e o valor do aporte, devolve as
ordens de compra. Sem rede e sem banco aqui de propósito: a aritmética que
decide onde alguém põe dinheiro precisa ser testável sem subir nada.

**Só compra. Nunca vende.** É a diferença entre este motor e um rebalanceador
clássico, e ela é deliberada:

  - venda realiza imposto e custo de corretagem para corrigir um desvio que o
    próximo aporte corrige de graça;
  - classe acima do alvo simplesmente não recebe nada, e volta ao alvo sozinha
    conforme o resto cresce.

Por isso o motor nunca devolve quantidade negativa, e "sobrealocado" não é erro
— é a instrução de não aportar ali agora.

**O peso é sobre VALOR DE MERCADO, não sobre custo.** O diagnóstico do Pilar 2
usa custo de propósito, para o veredito não oscilar com a cotação. Aqui é o
contrário: a pergunta é "qual é a minha exposição HOJE", e exposição é preço de
mercado. Peso sobre custo diria onde o dinheiro foi posto no passado, que é
outra pergunta.

**Posição sem preço entra pelo custo, marcada.** Tirar do denominador
distorceria o peso de todas as outras classes; entrar calada pelo custo
misturaria duas bases sem avisar. Ela entra, é contada, aparece em
`posicoes_sem_preco` e não recebe ordem — não dá para calcular quantas ações
comprar sem saber o preço.
"""

import math

# Teto de iterações do passo de sobra. Cada volta compra UMA ação, então o
# limite só é atingido por carteira grande com aporte grande — e ainda assim
# para, em vez de girar.
MAXIMO_SOBRA = 500

# Abaixo disso o dinheiro que sobrou não compra nada em lugar nenhum e não vale
# continuar procurando.
CENTAVOS = 0.01


def _valor_da_posicao(posicao, precos):
    """(valor, preco, base). `base` diz de onde o valor saiu."""
    ticker = posicao.get("ticker")
    quantidade = float(posicao.get("quantidade") or 0.0)
    preco = precos.get(ticker) if precos else None
    try:
        preco = float(preco) if preco is not None else None
    except (TypeError, ValueError):
        preco = None
    if preco is not None and preco > 0:
        return quantidade * preco, preco, "mercado"
    return float(posicao.get("custo_total") or 0.0), None, "custo"


def planejar(posicoes, precos, alvos, aporte, bloqueados=None):
    """Ordens de compra que aproximam a carteira do alvo, usando só o aporte.

    `bloqueados` é o conjunto de tickers que não devem receber dinheiro novo —
    no produto, as posições em desconformidade do Pilar 2. Elas continuam
    contando no peso atual (o dinheiro está lá), mas não recebem ordem.
    """
    posicoes = list(posicoes or [])
    precos = precos or {}
    bloqueados = set(bloqueados or ())
    alvos = {classe: float(pct) for classe, pct in (alvos or {}).items()}

    try:
        aporte = float(aporte)
    except (TypeError, ValueError):
        aporte = 0.0
    if aporte != aporte or aporte < 0:
        aporte = 0.0

    detalhadas = []
    sem_preco = []
    fora_do_alvo = []
    valor_fora = 0.0

    for posicao in posicoes:
        valor, preco, base = _valor_da_posicao(posicao, precos)
        linha = {
            "ticker": posicao.get("ticker"),
            "classe": posicao.get("classe") or "desconhecida",
            "quantidade": float(posicao.get("quantidade") or 0.0),
            "preco": preco,
            "valor_atual": round(valor, 2),
            "base_valor": base,
            "bloqueado": posicao.get("ticker") in bloqueados,
        }
        if base == "custo":
            sem_preco.append(linha["ticker"])
        if linha["classe"] not in alvos:
            # Classe sem alvo (tipicamente "desconhecida"): fica fora do
            # denominador. Mantê-la dentro criaria um excesso permanente que
            # nenhum aporte consegue corrigir, e o alvo nunca fecharia.
            fora_do_alvo.append(linha)
            valor_fora += valor
            continue
        detalhadas.append(linha)

    valor_atual = sum(l["valor_atual"] for l in detalhadas)
    total_depois = valor_atual + aporte

    # ---------------------------------------------------------- por classe
    classes = {}
    for classe, pct in alvos.items():
        atual = sum(l["valor_atual"] for l in detalhadas if l["classe"] == classe)
        alvo_valor = total_depois * pct / 100.0
        classes[classe] = {
            "classe": classe,
            "alvo_pct": round(pct, 2),
            "valor_atual": round(atual, 2),
            "peso_atual_pct": round(atual / valor_atual * 100.0, 2) if valor_atual else 0.0,
            "alvo_valor": round(alvo_valor, 2),
            "deficit": round(max(0.0, alvo_valor - atual), 2),
            "excesso": round(max(0.0, atual - alvo_valor), 2),
        }

    deficits = {c: max(0.0, total_depois * alvos[c] / 100.0
                       - sum(l["valor_atual"] for l in detalhadas if l["classe"] == c))
                for c in alvos}
    soma_deficit = sum(deficits.values())

    if aporte <= 0:
        destino = {c: 0.0 for c in alvos}
    elif soma_deficit <= 0:
        # Nenhuma classe abaixo do alvo: o aporte vai na proporção do próprio
        # alvo, que mantém a carteira onde está em vez de escolher um favorito.
        soma_pct = sum(alvos.values()) or 1.0
        destino = {c: aporte * alvos[c] / soma_pct for c in alvos}
    elif soma_deficit >= aporte:
        destino = {c: aporte * deficits[c] / soma_deficit for c in alvos}
    else:
        # O aporte cobre todos os déficits e ainda sobra: cobre primeiro, e o
        # excedente segue a proporção do alvo.
        resto = aporte - soma_deficit
        soma_pct = sum(alvos.values()) or 1.0
        destino = {c: deficits[c] + resto * alvos[c] / soma_pct for c in alvos}

    # ------------------------------------------------------- dentro da classe
    ordens = {}
    nao_alocado = []
    alvo_em_reais = {}

    for classe, montante in destino.items():
        if montante <= CENTAVOS:
            continue
        elegiveis = [l for l in detalhadas
                     if l["classe"] == classe and not l["bloqueado"] and l["preco"]]
        if not elegiveis:
            nao_alocado.append({
                "classe": classe,
                "valor": round(montante, 2),
                "motivo": _motivo_sem_destino(detalhadas, classe, bloqueados),
            })
            continue

        # Sem alvo por papel, a divisão dentro da classe mantém as proporções
        # internas: o usuário pediu para mexer no peso DA CLASSE, não na
        # composição dela. Posição bloqueada tem a fatia redistribuída entre as
        # demais, na mesma proporção.
        base = sum(l["valor_atual"] for l in elegiveis)
        for linha in elegiveis:
            fatia = (linha["valor_atual"] / base) if base else (1.0 / len(elegiveis))
            alvo_em_reais[linha["ticker"]] = alvo_em_reais.get(linha["ticker"], 0.0) \
                + montante * fatia

    for ticker, reais in alvo_em_reais.items():
        linha = next(l for l in detalhadas if l["ticker"] == ticker)
        quantidade = math.floor(reais / linha["preco"])
        ordens[ticker] = {
            "ticker": ticker,
            "classe": linha["classe"],
            "preco": round(linha["preco"], 2),
            "quantidade": int(quantidade),
            "valor": round(quantidade * linha["preco"], 2),
            "alvo_reais": round(reais, 2),
        }

    sobra = aporte - sum(o["valor"] for o in ordens.values()) \
        - sum(n["valor"] for n in nao_alocado)
    sobra = _aplicar_sobra(ordens, alvo_em_reais, sobra)

    # ------------------------------------------------------------- retrato
    for classe, dados in classes.items():
        comprado = sum(o["valor"] for o in ordens.values() if o["classe"] == classe)
        depois = dados["valor_atual"] + comprado
        dados["aporte"] = round(comprado, 2)
        dados["valor_depois"] = round(depois, 2)
        dados["peso_depois_pct"] = (
            round(depois / (valor_atual + aporte - sobra) * 100.0, 2)
            if (valor_atual + aporte - sobra) else 0.0)

    lista_ordens = sorted((o for o in ordens.values() if o["quantidade"] > 0),
                          key=lambda o: -o["valor"])

    return {
        "aporte": round(aporte, 2),
        "valor_atual": round(valor_atual, 2),
        "valor_depois": round(valor_atual + aporte - sobra, 2),
        "classes": [classes[c] for c in sorted(classes)],
        "ordens": lista_ordens,
        "sobra": round(max(0.0, sobra), 2),
        "nao_alocado": nao_alocado,
        "bloqueados": sorted(l["ticker"] for l in detalhadas if l["bloqueado"]),
        "posicoes_sem_preco": sorted(sem_preco),
        "fora_do_alvo": fora_do_alvo,
        "valor_fora_do_alvo": round(valor_fora, 2),
    }


def _motivo_sem_destino(detalhadas, classe, bloqueados):
    """Por que o dinheiro desta classe não virou ordem. O motivo muda o que o
    usuário tem que fazer, então ele é dito, não engolido."""
    da_classe = [l for l in detalhadas if l["classe"] == classe]
    if not da_classe:
        return ("Não há posição dessa classe na carteira para receber o aporte. "
                "Cadastre ao menos um papel dela.")
    if all(l["bloqueado"] for l in da_classe):
        return ("Todas as posições dessa classe estão em desconformidade e não "
                "recebem aporte novo.")
    return ("Sem cotação para as posições dessa classe — não dá para calcular "
            "quantas ações comprar.")


def _aplicar_sobra(ordens, alvo_em_reais, sobra):
    """Gasta o troco comprando uma ação de cada vez.

    O arredondamento para baixo de cada ordem deixa um resto por papel, e a
    soma desses restos pode passar do preço de vários deles. Devolver isso como
    "sobrou" seria dinheiro parado por artefato de arredondamento, não por
    decisão de ninguém.

    A cada volta compra o papel com a MAIOR diferença entre o que ele deveria
    receber e o que já recebeu — o que empurra a carteira para o alvo em vez de
    para o papel mais barato.
    """
    if sobra <= CENTAVOS or not ordens:
        return sobra

    for _ in range(MAXIMO_SOBRA):
        candidatos = [o for o in ordens.values()
                      if o["preco"] and o["preco"] <= sobra + 1e-9]
        if not candidatos:
            break
        escolhido = max(candidatos,
                        key=lambda o: alvo_em_reais.get(o["ticker"], 0.0) - o["valor"])
        if alvo_em_reais.get(escolhido["ticker"], 0.0) - escolhido["valor"] <= 0:
            # Todo mundo já recebeu o que devia: o que sobrou é troco de
            # verdade, e comprar mais afastaria do alvo em vez de aproximar.
            break
        escolhido["quantidade"] += 1
        escolhido["valor"] = round(escolhido["valor"] + escolhido["preco"], 2)
        sobra = round(sobra - escolhido["preco"], 2)

    return sobra
