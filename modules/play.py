"""Google Play Developer API — verificação de assinatura.

Regra que organiza este arquivo inteiro: **o cliente nunca decide o que
comprou**. O navegador manda um `purchaseToken` e mais nada; quem diz o que
aquele token vale é o Google, consultado daqui, com credencial de serviço. Um
token é uma pergunta, não uma afirmação.

Três armadilhas do Play Billing que este módulo existe para evitar:

  - **Reconhecer em três dias.** Compra não reconhecida é estornada
    automaticamente pelo Google e o acesso revogado. Renovação não precisa;
    a compra inicial precisa, e o prazo corre em dias corridos.

  - **A notificação não traz o estado.** A RTDN avisa que algo mudou e entrega
    só o token. Quem tratar o `notificationType` como verdade vai liberar
    Premium para assinatura cancelada mais cedo ou mais tarde — o certo é
    sempre reconsultar.

  - **Cancelar não é perder acesso.** `SUBSCRIPTION_CANCELED` significa que não
    vai renovar, não que acabou: o usuário pagou até o fim do ciclo. Cortar na
    hora do cancelamento é tomar o que já foi pago.
"""

import json
import os
import threading
from datetime import datetime, timezone

PACOTE = os.environ.get("AF_PLAY_PACOTE", "")
CREDENCIAL = os.environ.get("AF_PLAY_CREDENCIAL", "")

BASE_API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"
ESCOPO = "https://www.googleapis.com/auth/androidpublisher"

# Produtos criados no Play Console. Dois produtos separados, cada um com um
# plano base — em vez de um produto com dois planos base. A Digital Goods API
# devolve item por produto; com dois planos no mesmo produto seria preciso
# escolher a oferta no cliente, e escolha de oferta no cliente é mais uma
# coisa que pode ser adulterada antes de chegar aqui.
PRODUTOS = {
    "alphaforge_premium_mensal": {"rotulo": "Premium mensal", "meses": 1},
    "alphaforge_premium_anual": {"rotulo": "Premium anual", "meses": 12},
}

# Estados em que a assinatura dá acesso. Cancelada continua na lista de
# propósito: quem cancelou pagou até o fim do período.
ESTADOS_ATIVOS = {
    "SUBSCRIPTION_STATE_ACTIVE",
    "SUBSCRIPTION_STATE_CANCELED",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD",
}

_lock = threading.Lock()
_sessao = None


class PlayIndisponivel(RuntimeError):
    """Configuração ausente ou credencial inválida — não é erro do usuário."""


def configurado():
    return bool(PACOTE and CREDENCIAL and os.path.exists(CREDENCIAL))


def _sessao_autorizada():
    """Sessão HTTP que renova o token de acesso sozinha.

    `google-auth` entra aqui porque assinar JWT RS256 à mão, num caminho que
    decide quem pagou, é o lugar errado para economizar dependência.
    """
    global _sessao
    if _sessao is not None:
        return _sessao
    with _lock:
        if _sessao is not None:
            return _sessao
        if not configurado():
            raise PlayIndisponivel(
                "Defina AF_PLAY_PACOTE e AF_PLAY_CREDENCIAL (caminho do JSON da "
                "conta de serviço) para falar com a API do Google Play.")
        try:
            from google.auth.transport.requests import AuthorizedSession
            from google.oauth2 import service_account
        except ImportError as erro:
            raise PlayIndisponivel(f"Falta a biblioteca google-auth: {erro}")

        credencial = service_account.Credentials.from_service_account_file(
            CREDENCIAL, scopes=[ESCOPO])
        _sessao = AuthorizedSession(credencial)
        return _sessao


def consultar(token):
    """Estado real da assinatura, direto do Google.

    Devolve o corpo de `purchases.subscriptionsv2.get`. 404 vira None: token
    que o Google não conhece é token que não compra nada.
    """
    sessao = _sessao_autorizada()
    url = f"{BASE_API}/{PACOTE}/purchases/subscriptionsv2/tokens/{token}"
    resposta = sessao.get(url, timeout=20)
    if resposta.status_code == 404:
        return None
    if resposta.status_code >= 400:
        raise PlayIndisponivel(
            f"Play respondeu {resposta.status_code}: {resposta.text[:300]}")
    return resposta.json()


def reconhecer(token, produto):
    """Confirma a compra ao Google. Sem isto, estorno automático em 3 dias.

    Idempotente do lado deles: reconhecer duas vezes não quebra nada, então
    esta função não tenta ser esperta sobre já ter reconhecido antes.
    """
    sessao = _sessao_autorizada()
    url = (f"{BASE_API}/{PACOTE}/purchases/subscriptions/"
           f"{produto}/tokens/{token}:acknowledge")
    resposta = sessao.post(url, json={}, timeout=20)
    if resposta.status_code >= 400:
        raise PlayIndisponivel(
            f"Falha ao reconhecer ({resposta.status_code}): {resposta.text[:300]}")
    return True


def interpretar(dados):
    """Traduz a resposta do Play para o que o AlphaForge precisa saber.

    Devolve dict com: ativo, estado, produto, expira_em, reconhecida, order_id,
    token_anterior. `expira_em` sai em ISO com fuso, pronto para gravar.
    """
    if not dados:
        return {"ativo": False, "estado": "DESCONHECIDO", "produto": None,
                "expira_em": None, "reconhecida": True, "order_id": None,
                "token_anterior": None}

    estado = dados.get("subscriptionState", "DESCONHECIDO")
    itens = dados.get("lineItems") or []

    # A assinatura pode ter mais de um item; o que vale para o acesso é o que
    # expira por último.
    expira_em, produto = None, None
    for item in itens:
        fim = item.get("expiryTime")
        if fim and (expira_em is None or fim > expira_em):
            expira_em = fim
            produto = item.get("productId")
    if produto is None and itens:
        produto = itens[0].get("productId")

    ativo = estado in ESTADOS_ATIVOS
    if ativo and expira_em:
        try:
            limite = datetime.fromisoformat(expira_em.replace("Z", "+00:00"))
            if limite < datetime.now(timezone.utc):
                ativo = False
        except (TypeError, ValueError):
            pass

    return {
        "ativo": ativo,
        "estado": estado,
        "produto": produto,
        "expira_em": expira_em,
        "reconhecida": dados.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        "order_id": dados.get("latestOrderId"),
        # Presente quando o usuário trocou de plano: o token novo aponta para o
        # antigo, que deve parar de valer.
        "token_anterior": dados.get("linkedPurchaseToken"),
    }


def decodificar_notificacao(corpo):
    """Extrai o aviso do envelope do Pub/Sub.

    O envelope traz `message.data` em base64 com o JSON de verdade. Devolve
    (tipo, token, id_da_mensagem) — ou (None, None, id) para avisos de teste,
    que o Play manda ao configurar o tópico e não carregam compra nenhuma.
    """
    import base64

    mensagem = (corpo or {}).get("message") or {}
    id_mensagem = mensagem.get("messageId")
    bruto = mensagem.get("data")
    if not bruto:
        return None, None, id_mensagem

    try:
        conteudo = json.loads(base64.b64decode(bruto).decode("utf-8"))
    except Exception as erro:  # noqa: BLE001
        raise ValueError(f"Corpo da notificação ilegível: {erro}")

    if "testNotification" in conteudo:
        return "TESTE", None, id_mensagem

    aviso = conteudo.get("subscriptionNotification")
    if not aviso:
        # Compra avulsa ou aviso de voided purchase: não usamos nenhum dos dois.
        return None, None, id_mensagem
    return aviso.get("notificationType"), aviso.get("purchaseToken"), id_mensagem
