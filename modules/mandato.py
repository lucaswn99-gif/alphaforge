"""Filosofia declarada pelo investidor: da carteira, e a exceção por papel.

O diagnóstico media TODA ação pelo investidor defensivo de Graham. Isso produz
ruído em vez de sinal para quem não tem mandato de valor: uma carteira de renda
com transmissora e seguradora era julgada por liquidez corrente ≥ 2 e dívida de
longo prazo abaixo do capital de giro — critérios de indústria americana de
1949, aplicados a concessão brasileira, cuja alavancagem contra receita
contratada é o modelo de negócio e não deterioração.

A evidência disso saiu do próprio sistema: rodado sobre o IBOV inteiro, o
filtro de Graham aprovou três bancos, e os três passaram porque liquidez e
capital de giro NÃO PUDERAM ser medidos no balanço deles. Corrigido o piso de
critérios e tirada a instituição financeira do escopo, a régua esvaziou.

Então a filosofia passa a ser escolha de quem investe:

    barsi    renda por setor perene: preço-teto por DPA projetado, payout na
             faixa, alavancagem sob teto e constância de lucro
    bazin    renda por preço: teto que entrega 6% de yield, com payout e
             dívida/EBIT como trava contra o yield que vai cair
    graham   valor: os critérios do investidor defensivo, para quem quer isso
    nenhuma  sem régua: mostra o dado bruto do papel (múltiplo, ROE, dívida)
             sem veredito de aprovado/reprovado — para quem não quer que
             NENHUMA das três teses julgue a carteira

**"nenhuma" não é ausência de escolha, é uma escolha.** Sem filosofia
declarada, o investidor não decidiu nada ainda e a tela cobra a decisão —
mostrar "não apurado" com um aviso é correto. Escolher "nenhuma" é o oposto:
é uma decisão explícita de não ser julgado por Barsi, Bazin ou Graham, e a
tela não pode continuar cobrando uma escolha que já foi feita. É também o que
torna uma carteira multiativo possível: renda fixa e fundo de investimento
não têm filosofia de ação nenhuma que caiba neles.

**Duas camadas, e a de baixo vence.** A carteira tem uma filosofia; uma posição
pode declarar outra. Cobre o caso real de um mandato de renda com duas ou três
posições que não deveriam ser julgadas por DY.

**Fora do escopo não é reprovação.** Barsi sobre uma varejista, ou Graham sobre
um banco, não devolve "desconforme" — devolve "não apurado", com o motivo. É o
mesmo princípio que o resto do projeto segue: ausência é um estado próprio.
"""

from datetime import datetime, timezone

from modules import contas

BARSI = "barsi"
BAZIN = "bazin"
GRAHAM = "graham"
NENHUMA = "nenhuma"

FILOSOFIAS = (BARSI, BAZIN, GRAHAM, NENHUMA)

ROTULOS = {
    BARSI: "Barsi — renda por setor perene",
    BAZIN: "Bazin — renda por preço-teto",
    GRAHAM: "Graham — investidor defensivo",
    NENHUMA: "Nenhuma — só o dado bruto",
}

RESUMOS = {
    BARSI: ("Preço-teto pelo DPA projetado, payout entre 30% e 80%, dívida "
            "líquida/EBIT sob teto e lucro em todos os exercícios apurados. "
            "Só se aplica a setor da tese BESST."),
    BAZIN: ("Preço-teto que entrega 6% de yield, com payout na faixa e dívida "
            "líquida/EBIT abaixo de 2,5x — as duas travas existem porque yield "
            "alto sozinho costuma ser provento prestes a cair."),
    GRAHAM: ("Os critérios do investidor defensivo: porte, liquidez corrente, "
             "dívida sob o capital de giro, constância e crescimento de lucro, "
             "e o teto combinado P/L × P/VP ≤ 22,5. Exigente por desenho — e "
             "não se aplica a instituição financeira."),
    NENHUMA: ("Nenhuma das três teses julga esta carteira. As ações mostram "
              "múltiplo, ROE e dívida líquida/EBIT sem veredito de aprovado "
              "ou reprovado — a leitura é toda sua."),
}

# Sem padrão, pelo mesmo motivo do alvo por classe: escolher a lente pela qual
# a carteira de alguém é julgada é decisão de quem investe. Sem filosofia
# declarada, a ação sai como não apurada e a tela pede a escolha.
PADRAO = None


class ErroMandato(ValueError):
    """Escolha recusada, com motivo em português para a tela repetir."""


def _agora():
    return datetime.now(timezone.utc).isoformat()


def validar(bruto):
    """Nome de filosofia normalizado, ou levanta."""
    escolha = str(bruto or "").strip().lower()
    if escolha not in FILOSOFIAS:
        raise ErroMandato(
            f"Filosofia desconhecida: '{bruto}'. Use {', '.join(FILOSOFIAS)}.")
    return escolha


def definir(usuario_id, bruto):
    """Filosofia da carteira inteira."""
    escolha = validar(bruto)
    contas.iniciar()
    with contas._conectar() as cx:
        cx.execute(
            "INSERT INTO mandato_carteira (usuario_id, filosofia, atualizado_em) "
            "VALUES (?, ?, ?) ON CONFLICT(usuario_id) DO UPDATE SET "
            "filosofia = excluded.filosofia, atualizado_em = excluded.atualizado_em",
            (usuario_id, escolha, _agora()))
    return escolha


def obter(usuario_id):
    """Filosofia da carteira, ou None quando nunca foi escolhida."""
    contas.iniciar()
    with contas._conectar() as cx:
        linha = cx.execute(
            "SELECT filosofia FROM mandato_carteira WHERE usuario_id = ?",
            (usuario_id,)).fetchone()
    return linha["filosofia"] if linha else None


def definir_do_papel(usuario_id, ticker, bruto):
    """Exceção de uma posição. `bruto` vazio ou None volta a herdar da carteira.

    Devolve True se a posição existe. Herdar e escolher são estados diferentes,
    e por isso a limpeza grava NULL em vez de gravar a filosofia da carteira:
    trocar a filosofia da carteira depois precisa arrastar junto quem herda.
    """
    escolha = None if bruto in (None, "", "herdar") else validar(bruto)
    ticker = str(ticker or "").strip().upper()
    contas.iniciar()
    with contas._conectar() as cx:
        return cx.execute(
            "UPDATE carteiras SET filosofia = ?, atualizado_em = ? "
            "WHERE usuario_id = ? AND ticker = ?",
            (escolha, _agora(), usuario_id, ticker)).rowcount > 0


def resolver(filosofia_da_carteira, filosofia_do_papel):
    """Qual filosofia vale para esta posição. A do papel vence."""
    return filosofia_do_papel or filosofia_da_carteira or PADRAO
