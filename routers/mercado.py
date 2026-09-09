"""Painéis de contexto: commodities, notícias e o placar do pregão.

Nada aqui é fonte nova de risco para o resto do terminal — cada endpoint
degrada sozinho. Se as notícias caírem, o scanner continua; se as commodities
caírem, o placar continua. Foi o que faltou quando o `.info` do Yahoo derrubou
a tabela inteira.
"""

from fastapi import APIRouter, Query

from modules import identidade, mercado, noticias
from routers.equity import carimbo_de_coleta, executar_scanner, flag

router = APIRouter(prefix="/api/mercado", tags=["mercado"])

# Quantos papéis entram em cada lado do placar do pregão.
TAMANHO_PLACAR = 10
# Abaixo disso a "maior alta do dia" é ruído de fechamento, não movimento.
VARIACAO_MINIMA = 0.01


@router.get("/commodities")
def get_commodities(forcar: bool = Query(False)):
    """Petróleo, minério, metais, grãos, índices, câmbio e juro americano."""
    itens = mercado.coletar(forcar=flag(forcar))
    return {
        **carimbo_de_coleta(),
        "itens": itens,
        "grupos": mercado.por_grupo(itens),
        "esperados": len(mercado.INSTRUMENTOS),
        "obtidos": len(itens),
        # Futuro tem atraso da bolsa de origem; a tela precisa poder dizer isso.
        "aviso": ("Futuros e índices com atraso da bolsa de origem. "
                  "Referência de contexto, não preço de execução."),
    }


@router.get("/noticias")
def get_noticias(forcar: bool = Query(False)):
    """Manchetes de macro, mercado e commodities.

    Só manchete, veículo, horário e link — o texto da matéria fica no site do
    veículo, que é de quem ele é.
    """
    payload = noticias.coletar(forcar=flag(forcar))
    return {**payload, "categorias": list(noticias.CATEGORIAS)}


@router.get("/placar")
def get_placar(forcar: bool = Query(False)):
    """Maiores altas e baixas do dia entre os papéis que o scanner acompanha.

    Sai da mesma carga de preços do scanner — nenhuma requisição adicional. O
    universo é o do radar (IBOV + acompanhados), não a B3 inteira, e a tela diz
    isso: chamar de "maiores altas da B3" o recorte de 100 papéis seria mentira
    pequena e desnecessária.
    """
    scanner = executar_scanner(forcar=flag(forcar))
    linhas = [linha for linha in scanner.get("oportunidades", [])
              if linha.get("variacao_dia") is not None]

    def _resumir(linha):
        return {
            "ticker": linha["ticker"],
            "nome": linha.get("nome"),
            "preco": linha.get("preco"),
            "variacao_dia": linha["variacao_dia"],
            "identidade": linha.get("identidade") or identidade.identidade(linha["ticker"]),
        }

    ordenadas = sorted(linhas, key=lambda l: l["variacao_dia"], reverse=True)
    altas = [_resumir(l) for l in ordenadas if l["variacao_dia"] > VARIACAO_MINIMA]
    baixas = [_resumir(l) for l in reversed(ordenadas) if l["variacao_dia"] < -VARIACAO_MINIMA]

    return {
        **carimbo_de_coleta(),
        "altas": altas[:TAMANHO_PLACAR],
        "baixas": baixas[:TAMANHO_PLACAR],
        "universo": len(linhas),
        "origem_composicao": scanner.get("origem_composicao"),
        "aviso": ("Universo do radar (IBOV + acompanhados), não a B3 inteira. "
                  "Fechamentos do Yahoo, atrasados 15 minutos."),
    }


@router.get("/identidade/{ticker}")
def get_identidade(ticker: str):
    """Logo, setor e cor de um papel. A tela usa para o tile ao lado do código."""
    return identidade.identidade(ticker)
