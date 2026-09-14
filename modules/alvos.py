"""Percentual-alvo por classe de ativo.

Guarda o que o assinante DECIDIU sobre a própria alocação — ação, FII, ETF —
para o rebalanceamento saber o que é déficit. Leitura e escrita da tabela
`alvos_carteira` (esquema em `modules/contas.py`).

Duas decisões que valem explicação:

  - **Não existe alvo padrão.** Um alvo sugerido por nós é recomendação de
    alocação, não configuração de software, e sai do que este produto se propõe
    a fazer. Sem alvo definido, o rebalanceamento não roda e a tela pede a
    definição. É menos cômodo e é o único desenho honesto.

  - **A soma tem que fechar 100.** Alvo que soma 90 deixaria 10% do aporte sem
    destino declarado, e o motor teria que inventar para onde mandar. Recusar
    na entrada é mais barato que explicar depois.
"""

from datetime import datetime, timezone

from modules import contas

# As classes que `carteira.classificar` sabe reconhecer e para as quais faz
# sentido ter alvo. "desconhecida" fica de fora de propósito: não dá para
# definir alvo para uma categoria que só existe porque a nossa base falhou.
CLASSES_ALVO = ("acao", "fii", "etf")

ROTULOS = {
    "acao": "Ações",
    "fii": "Fundos imobiliários",
    "etf": "ETFs",
}

# A soma pode fechar 99,99 ou 100,01 por arredondamento de tela. Acima disso é
# alvo mal preenchido, não ruído de ponto flutuante.
TOLERANCIA_SOMA = 0.01


class ErroAlvo(ValueError):
    """Alvo recusado, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def validar(bruto):
    """{classe: percentual} normalizado, ou levanta `ErroAlvo`.

    Classe ausente vale zero — quem não quer ETF na carteira não deveria ser
    obrigado a digitar "etf: 0" para o formulário aceitar.
    """
    if not isinstance(bruto, dict):
        raise ErroAlvo("Envie um objeto com o percentual de cada classe.")

    desconhecidas = [c for c in bruto if c not in CLASSES_ALVO]
    if desconhecidas:
        raise ErroAlvo(
            f"Classe sem alvo possível: {', '.join(sorted(desconhecidas))}. "
            f"Use {', '.join(CLASSES_ALVO)}.")

    alvos = {}
    for classe in CLASSES_ALVO:
        valor = bruto.get(classe, 0)
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            raise ErroAlvo(f"O alvo de {ROTULOS[classe]} precisa ser um número.")
        if numero != numero:   # NaN
            raise ErroAlvo(f"O alvo de {ROTULOS[classe]} precisa ser um número.")
        if numero < 0:
            raise ErroAlvo(f"O alvo de {ROTULOS[classe]} não pode ser negativo.")
        if numero > 100:
            raise ErroAlvo(f"O alvo de {ROTULOS[classe]} não pode passar de 100%.")
        alvos[classe] = numero

    soma = sum(alvos.values())
    if abs(soma - 100.0) > TOLERANCIA_SOMA:
        raise ErroAlvo(
            f"Os alvos somam {soma:.2f}% — precisam somar 100%.")
    return alvos


def definir(usuario_id, bruto):
    """Grava o alvo do usuário. Devolve o mapa normalizado."""
    alvos = validar(bruto)
    contas.iniciar()
    agora = _agora()
    with contas._conectar() as cx:
        # Substitui o conjunto inteiro: alvo é um retrato que soma 100, não
        # campos independentes. Gravar classe a classe deixaria o banco passar
        # por estados que somam 140.
        cx.execute("DELETE FROM alvos_carteira WHERE usuario_id = ?", (usuario_id,))
        cx.executemany(
            "INSERT INTO alvos_carteira (usuario_id, classe, percentual, "
            "atualizado_em) VALUES (?, ?, ?, ?)",
            [(usuario_id, classe, pct, agora) for classe, pct in alvos.items()])
    return alvos


def obter(usuario_id):
    """{classe: percentual} ou None quando o usuário nunca definiu.

    None e "tudo zero" são coisas diferentes: a primeira significa não
    configurado, a segunda seria um alvo inválido que `validar` nem aceita.
    """
    contas.iniciar()
    with contas._conectar() as cx:
        linhas = cx.execute(
            "SELECT classe, percentual FROM alvos_carteira WHERE usuario_id = ?",
            (usuario_id,)).fetchall()
    if not linhas:
        return None
    alvos = {classe: 0.0 for classe in CLASSES_ALVO}
    for linha in linhas:
        if linha["classe"] in alvos:
            alvos[linha["classe"]] = float(linha["percentual"])
    return alvos


def limpar(usuario_id):
    """True se havia alvo definido."""
    contas.iniciar()
    with contas._conectar() as cx:
        return cx.execute(
            "DELETE FROM alvos_carteira WHERE usuario_id = ?",
            (usuario_id,)).rowcount > 0
