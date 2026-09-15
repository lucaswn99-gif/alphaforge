"""Backtest de 12 meses: reconstrução da série de valor da CARTEIRA INTEIRA,
não a média das partes — mesmo princípio do Pilar 4 (`modules/estresse.py`).

Ação, FII e ETF entram pelo preço histórico, a mesma fonte do Pilar 4. Renda
fixa não tem preço para retroceder: entra pelo REPLAY DA FÓRMULA do indexador
contratado (a mesma conta de `modules/renda_fixa.marcar_na_curva`), nunca por
leitura de mercado — e por isso o resultado é rotulado como simulação, não
cotação.

**Só entra quem tem histórico para a janela inteira.** Um papel que listou há
6 meses, ou uma renda fixa aplicada há 3, distorceria o índice combinado no
meio do caminho se entrasse com peso constante desde o início — mesmo
critério que o Pilar 4 já usa para preço de ação (`estresse_historico`),
estendido aqui a renda fixa. Quem fica de fora aparece em `sem_historico`,
com o peso que não entrou na conta — nunca some calado.

**Granularidade mensal, não diária.** Alinhar preço de ação (diário) com a
fórmula de indexador (mensal para IPCA, diário só nos dias úteis que o BCB
publica para CDI) dia a dia exigiria um calendário de pregão comum que o
projeto não tem. Treze pontos mensais (hoje e os doze meses anteriores) dão a
leitura de 12 meses pedida sem inventar uma precisão diária que a fonte mais
grossa (IPCA) não sustenta.

**O truque da posição-espelho.** Para saber quanto uma renda fixa rendeu
DESDE O INÍCIO DA JANELA (não desde a aplicação real, que pode ser mais
antiga), este módulo chama `marcar_na_curva` com uma posição hipotética cujo
"valor aplicado" é 1,0 e cuja "data de aplicação" é o início da janela. A
matemática do indexador é multiplicativamente decomponível no tempo (o fator
acumulado de um período maior é o produto dos fatores dos sub-períodos), o
que faz esse atalho devolver exatamente a mesma razão que dividir o valor
real em cada data pelo valor real no início da janela — sem duplicar a conta
de `renda_fixa.py` nem reimplementar a fórmula de cada indexador aqui.

Um detalhe de precisão: `marcar_na_curva` arredonda o VALOR que devolve a 2
casas, correto para dinheiro de verdade mas grosseiro demais para uma
posição-espelho de R$ 1,00 (arredondaria o próprio rendimento do período).
Por isso a razão usada aqui vem de `detalhes["fator"]` (6 casas), não do
valor arredondado.

**Fundo de investimento entra pela cota diária da CVM (Etapa B de
`modules/fundos.py`), quando o CNPJ tem coleta cobrindo o início da janela.**
Não precisa de posição-espelho: a cota já é uma série de verdade, então o
relativo é simplesmente cota(alvo) / cota(início) — o mesmo raciocínio de
`_relativo_acao`, só que a fonte é `modules/cota_cvm.py` em vez de preço de
pregão. Sem cota cobrindo o início da janela (CNPJ não coletado, ou coletado
só a partir de uma data mais recente), o fundo cai em `sem_historico` — nunca
entra com "sem variação" fingindo neutralidade."""

import calendar
from datetime import date


def _subtrair_meses(referencia, n):
    """`referencia` menos `n` meses, com o dia grudado no fim do mês quando o
    mês de destino é mais curto (31/01 menos 1 mês = 28 ou 29/02)."""
    mes_total = referencia.month - 1 - n
    ano = referencia.year + mes_total // 12
    mes = mes_total % 12 + 1
    ultimo_dia = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(referencia.day, ultimo_dia))


def pontos_mensais(hoje, meses=12):
    """[hoje - meses, ..., hoje - 1 mês, hoje] — `meses` + 1 pontos, em
    ordem crescente. O primeiro é o início da janela do backtest."""
    return [_subtrair_meses(hoje, n) for n in range(meses, -1, -1)]


def _preco_na_data(precos, alvo):
    """Último fechamento com data <= alvo. `precos`: [(data, valor), ...]
    já ordenado por data crescente. `None` quando não há fechamento antes de
    `alvo` (ainda não tinha listado, ou o histórico não cobre a data)."""
    valor = None
    for data_ponto, preco in precos:
        if data_ponto > alvo:
            break
        valor = preco
    return valor


def _relativo_acao(precos, inicio, alvo):
    base = _preco_na_data(precos, inicio)
    atual = _preco_na_data(precos, alvo)
    if not base or atual is None:
        return None
    return atual / base


def _relativo_renda_fixa(posicao, inicio, alvo, buscar_cdi, buscar_ipca):
    """Igual ao truque descrito na docstring do módulo, com um cuidado extra:
    `marcar_na_curva` arredonda o VALOR para 2 casas (moeda de verdade), o que
    é preciso demais para machucar um real e grosseiro demais para uma
    posição-espelho de R$ 1,00 — 1,0421 viraria 1,04, um erro de dezenas de
    vezes o próprio rendimento do período. Por isso a razão vem do `fator`
    em `detalhes` (6 casas), não do valor arredondado a 2."""
    if posicao["data_aplicacao"] > inicio:
        return None
    from modules import renda_fixa
    pseudo = {"data_aplicacao": inicio, "valor_aplicado": 1.0,
             "indexador": posicao["indexador"], "taxa": posicao["taxa"]}
    valor, _resumo, apurado, detalhes = renda_fixa.marcar_na_curva(
        pseudo, hoje=alvo, buscar_cdi=buscar_cdi, buscar_ipca=buscar_ipca)
    if not apurado:
        return None
    return detalhes.get("fator", valor)


def _relativo_fundo(posicao, inicio, alvo, buscar_cota):
    """Cota(alvo) / cota(início) — sem posição-espelho, a cota já é a série
    de verdade. `None` quando falta cota em qualquer uma das duas pontas
    (CNPJ sem coleta, ou coleta começando depois de `inicio`)."""
    if buscar_cota is None:
        from modules import cota_cvm
        buscar_cota = cota_cvm.cota_na_data
    base = buscar_cota(posicao["cnpj"], inicio)
    atual = buscar_cota(posicao["cnpj"], alvo)
    if not base or atual is None:
        return None
    return atual / base


def carteira_12_meses(datas, acoes, precos_por_ticker, posicoes_renda_fixa,
                      buscar_cdi=None, buscar_ipca=None,
                      posicoes_fundos=None, buscar_cota=None):
    """(dict) — ver docstring do módulo para os critérios de inclusão.

    `datas`: pontos mensais crescentes (ver `pontos_mensais`).
    `acoes`: [{"ticker", "peso"}] — peso é o de HOJE (custo ou valor atual;
      quem chama decide — ver Pilar 4 sobre por que custo é aceitável aqui).
    `precos_por_ticker`: {ticker: [(data, fechamento), ...]} por data crescente.
    `posicoes_renda_fixa`: [{"identificador", "peso", "data_aplicacao",
      "indexador", "taxa"}].
    `posicoes_fundos`: [{"identificador", "peso", "cnpj"}] — Etapa B da cota
      diária (ver docstring do módulo); `None`/vazio é aceito (Etapa A ainda
      não alimentava o backtest, e chamadas antigas continuam funcionando).

    Devolve `serie` (índice base 100 em cada ponto mensal),
    `retorno_periodo_pct`, `cobertura_pct` (peso incluído / peso total) e
    `sem_historico` (o que ficou de fora e por quê).
    """
    posicoes_fundos = posicoes_fundos or []
    inicio = datas[0]
    peso_total = (sum(p["peso"] for p in acoes)
                 + sum(p["peso"] for p in posicoes_renda_fixa)
                 + sum(p["peso"] for p in posicoes_fundos))

    incluidas = []
    sem_historico = []

    for posicao in acoes:
        precos = precos_por_ticker.get(posicao["ticker"]) or []
        if _preco_na_data(precos, inicio) is None:
            sem_historico.append({"tipo": "acao", "identificador": posicao["ticker"],
                                  "peso": posicao["peso"]})
            continue
        incluidas.append(("acao", posicao, precos))

    for posicao in posicoes_renda_fixa:
        if posicao["data_aplicacao"] > inicio:
            sem_historico.append({"tipo": "renda_fixa",
                                  "identificador": posicao["identificador"],
                                  "peso": posicao["peso"]})
            continue
        incluidas.append(("renda_fixa", posicao, None))

    from modules import cota_cvm
    for posicao in posicoes_fundos:
        if (buscar_cota or cota_cvm.cota_na_data)(posicao["cnpj"], inicio) is None:
            sem_historico.append({"tipo": "fundo", "identificador": posicao["identificador"],
                                  "peso": posicao["peso"]})
            continue
        incluidas.append(("fundo", posicao, None))

    peso_coberto = sum(p["peso"] for _tipo, p, _extra in incluidas)

    if not incluidas or peso_coberto <= 0:
        return {"serie": [], "retorno_periodo_pct": None, "cobertura_pct": 0.0,
                "meses": len(datas) - 1, "incluidas": 0,
                "sem_historico": sem_historico,
                "motivo": ("Nenhuma posição tem histórico para os últimos "
                          f"{len(datas) - 1} meses inteiros.")}

    serie = []
    for alvo in datas:
        indice = 0.0
        for tipo, posicao, precos in incluidas:
            if tipo == "acao":
                relativo = _relativo_acao(precos, inicio, alvo)
            elif tipo == "fundo":
                relativo = _relativo_fundo(posicao, inicio, alvo, buscar_cota)
            else:
                relativo = _relativo_renda_fixa(posicao, inicio, alvo,
                                                buscar_cdi, buscar_ipca)
            # Não deveria faltar aqui — o critério de inclusão já garantiu
            # cobertura desde o início da janela — mas 1,0 (sem variação) é
            # o piso seguro se algo escapar, nunca um índice quebrado.
            if relativo is None:
                relativo = 1.0
            indice += (posicao["peso"] / peso_coberto) * relativo
        serie.append({"data": alvo.isoformat(), "indice": round(indice * 100.0, 4)})

    primeiro = serie[0]["indice"]
    retorno_periodo_pct = (round((serie[-1]["indice"] / primeiro - 1) * 100.0, 2)
                          if primeiro else None)

    return {
        "serie": serie,
        "retorno_periodo_pct": retorno_periodo_pct,
        "cobertura_pct": round(peso_coberto / peso_total * 100.0, 2) if peso_total else 0.0,
        "meses": len(datas) - 1,
        "incluidas": len(incluidas),
        "sem_historico": sem_historico,
        "motivo": None,
    }
