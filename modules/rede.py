"""Requisição HTTP com nova tentativa e recuo exponencial.

Existe porque as fontes deste projeto são todas gratuitas, e fonte gratuita
falha de um jeito específico: não com erro claro, mas com 429, com 503
intermitente, com resposta vazia e com timeout. Tentar uma vez e desistir
produz um painel que fica metade branco sem explicar por quê; tentar em laço
apertado faz o Yahoo devolver 429 para o resto do dia.

Duas decisões que valem o comentário:

  - **Recuo exponencial com tremor.** O intervalo dobra a cada tentativa e
    ganha um ruído aleatório. Sem o ruído, trinta ativos que falharam juntos
    voltam a bater no servidor exatamente no mesmo instante, e a segunda
    rodada falha pelo mesmo motivo que a primeira.

  - **Erro de cliente não é retentado.** 404 e 403 não melhoram com espera; só
    5xx, 429 e falha de conexão. Insistir em 404 é gastar seis segundos para
    chegar à mesma conclusão.

Nada aqui levanta exceção para o chamador: a resposta é `None` e o motivo vai
para o log. Um ativo que não respondeu não pode derrubar a varredura inteira.
"""

import logging
import random
import time

import requests

registro = logging.getLogger(__name__)

TENTATIVAS_PADRAO = 3
ESPERA_INICIAL = 1.0
ESPERA_MAXIMA = 20.0
TIMEOUT_PADRAO = 15

# Só estes voltam a ser tentados. O resto é problema que espera não resolve.
STATUS_RETENTAVEIS = {408, 425, 429, 500, 502, 503, 504}


def _esperar(tentativa, cabecalho_retry_after=None):
    """Quanto dormir antes da próxima tentativa.

    Se o servidor disse explicitamente quanto esperar (Retry-After), obedece:
    ele sabe do próprio limite mais do que qualquer fórmula nossa.
    """
    if cabecalho_retry_after:
        try:
            pedido = float(cabecalho_retry_after)
            if 0 < pedido <= ESPERA_MAXIMA:
                return pedido
        except (TypeError, ValueError):
            pass
    base = min(ESPERA_INICIAL * (2 ** tentativa), ESPERA_MAXIMA)
    return base * (0.5 + random.random() / 2.0)


def obter(url, cabecalhos=None, parametros=None, tentativas=TENTATIVAS_PADRAO,
          timeout=TIMEOUT_PADRAO, sessao=None):
    """GET com nova tentativa. Devolve o `Response` ou None.

    `sessao` aceita um `requests.Session` para reaproveitar conexão quando são
    muitas chamadas ao mesmo host — é o caso da SEC, que pede rajada baixa.
    """
    cliente = sessao or requests
    ultimo_motivo = "sem tentativa"

    for tentativa in range(tentativas):
        try:
            resposta = cliente.get(url, headers=cabecalhos, params=parametros,
                                   timeout=timeout)
        except requests.RequestException as falha:
            ultimo_motivo = f"{type(falha).__name__}: {falha}"
            if tentativa < tentativas - 1:
                time.sleep(_esperar(tentativa))
            continue

        if resposta.status_code == 200:
            return resposta

        ultimo_motivo = f"HTTP {resposta.status_code}"
        if resposta.status_code not in STATUS_RETENTAVEIS:
            registro.warning("%s — %s (sem nova tentativa)", url, ultimo_motivo)
            return None

        if tentativa < tentativas - 1:
            time.sleep(_esperar(tentativa, resposta.headers.get("Retry-After")))

    registro.warning("%s — desisti após %d tentativas: %s", url, tentativas,
                     ultimo_motivo)
    return None


def obter_json(url, cabecalhos=None, parametros=None, tentativas=TENTATIVAS_PADRAO,
               timeout=TIMEOUT_PADRAO, sessao=None):
    """Igual a `obter`, já decodificando o corpo. None se algo não fechar.

    JSON malformado é tratado como falha de rede porque, do ponto de vista de
    quem chamou, é a mesma coisa: não há dado utilizável.
    """
    resposta = obter(url, cabecalhos, parametros, tentativas, timeout, sessao)
    if resposta is None:
        return None
    try:
        return resposta.json()
    except ValueError as falha:
        registro.warning("%s — corpo não é JSON: %s", url, falha)
        return None
