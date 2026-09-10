# Google Play Billing — o que o código faz e o que falta configurar

O código está pronto e testado. O que resta é configuração em três lugares
(Play Console, Google Cloud, Bubblewrap) e duas variáveis no servidor.

## Como funciona

Um TWA não fala com o Play Billing pelo Java — fala pela **Digital Goods API**
do Chrome, combinada com a **Payment Request API**. O navegador abre a folha de
pagamento do Google e devolve um `purchaseToken`.

```
tela ──► Digital Goods API ──► folha do Google ──► purchaseToken
  │
  └──► POST /conta/assinatura/play/confirmar { token }
                      │
                      ├──► Google Play Developer API: o que é este token?
                      ├──► reconhece a compra (prazo de 3 dias)
                      ├──► grava em `assinaturas`
                      └──► recalcula o plano da conta

Play ──► Pub/Sub ──► POST /conta/assinatura/play/notificar?segredo=…
                      │
                      └──► reconsulta o token e recalcula (nunca confia no evento)
```

**O cliente nunca decide o que comprou.** O navegador manda um token e nada
mais. Qual produto, se está pago e até quando vale são perguntas respondidas
pelo Google, consultado do servidor com credencial de serviço.

## Três armadilhas que o código já trata

**Reconhecer em três dias.** Compra não reconhecida é estornada
automaticamente pelo Google e o acesso revogado. `_aplicar_compra` reconhece na
hora; se falhar, a notificação seguinte tenta de novo. Renovação não precisa —
só a compra inicial.

**A notificação não traz o estado.** A RTDN avisa que algo mudou e entrega só o
token. O `notificationType` é registrado mas **nunca usado como verdade**: o
código sempre reconsulta. Quem confia no tipo do evento libera Premium para
assinatura cancelada mais cedo ou mais tarde.

**Cancelar não é perder acesso.** `SUBSCRIPTION_STATE_CANCELED` significa que
não vai renovar, não que acabou — o usuário pagou até o fim do ciclo. Por isso
esse estado está na lista de ativos, junto com `IN_GRACE_PERIOD`. Cortar na
hora do cancelamento é tomar o que já foi pago.

E uma quarta, do lado da rede: se cair entre pagar e confirmar, o usuário
ficaria pagando sem acesso. Toda abertura do app relista as compras do Play e
reenvia o que o servidor ainda não conhece. O conserto é automático e
silencioso.

## Passo 1 — Criar os produtos no Play Console

**Monetizar → Produtos → Assinaturas → Criar assinatura.** Dois produtos, cada
um com **um** plano base:

| ID do produto | Plano base | Preço |
|---|---|---|
| `alphaforge_premium_mensal` | mensal, renovação automática | R$ 39,90 |
| `alphaforge_premium_anual` | anual, renovação automática | R$ 399,00 |

Dois produtos separados em vez de um produto com dois planos base é
deliberado: a Digital Goods API devolve item por produto, e com dois planos no
mesmo produto seria preciso escolher a oferta no cliente — mais uma coisa que
pode ser adulterada antes de chegar ao servidor.

Os IDs precisam bater exatamente com `PRODUTOS`, em `modules/play.py`.

## Passo 2 — Conta de serviço para a API

1. **Google Cloud Console** → crie (ou escolha) um projeto → **APIs e serviços**
   → ative a **Google Play Android Developer API**.
2. **IAM → Contas de serviço** → criar → baixe a chave em **JSON**.
3. **Play Console → Usuários e permissões** → convide o e-mail da conta de
   serviço → permissão de **ver dados financeiros** e **gerenciar pedidos e
   assinaturas**, no app AlphaForge.

O JSON vai para o droplet, fora da pasta do projeto e fora do git:

```bash
sudo mkdir -p /etc/alphaforge
sudo mv chave-play.json /etc/alphaforge/play.json
sudo chmod 600 /etc/alphaforge/play.json
```

## Passo 3 — Notificações em tempo real

1. **Google Cloud → Pub/Sub** → criar tópico, ex. `alphaforge-play`.
2. Dê ao publicador do Google permissão de publicar nele:
   `google-play-developer-notifications@system.gserviceaccount.com`, papel
   **Pub/Sub Publisher**.
3. Crie uma **subscrição do tipo push** apontando para:
   `https://alphaforge.api.br/conta/assinatura/play/notificar?segredo=SEU_SEGREDO`
4. **Play Console → Monetizar → Configuração de monetização** → cole o nome
   completo do tópico e salve. Use o botão de **enviar notificação de teste** —
   o endpoint responde `{"ok": true, "teste": true}`.

## Passo 4 — Variáveis no servidor

No `.env` do droplet:

```bash
AF_PLAY_PACOTE=br.api.alphaforge          # o applicationId do TWA
AF_PLAY_CREDENCIAL=/etc/alphaforge/play.json
AF_PLAY_SEGREDO=algo-longo-e-aleatorio     # o mesmo da URL do Pub/Sub
```

Sem `AF_PLAY_PACOTE` e `AF_PLAY_CREDENCIAL`, o botão de assinar diz que a
assinatura não está configurada em vez de quebrar. Sem `AF_PLAY_SEGREDO`, o
endpoint de notificação responde 503 — um webhook aberto é um botão de "me dê
Premium" exposto na internet.

E uma dependência nova, a única do projeto inteiro:

```bash
pip install google-auth==2.43.0
```

Ela entra porque assinar JWT RS256 à mão, num caminho que decide quem pagou, é
o lugar errado para economizar dependência.

## Passo 5 — Ligar o Play Billing no TWA

No `twa-manifest.json` do AlphaForge (quando você gerar o TWA dele):

```json
"playBilling": { "enabled": true },
"alphaDependencies": { "enabled": true }
```

Depois `bubblewrap update` e `bubblewrap build`. Isso acrescenta a permissão
`com.android.vending.BILLING` ao APK e habilita a Digital Goods API dentro do
app. **Sem essa configuração o `getDigitalGoodsService` não existe** e a tela
mostra "a assinatura abre pelo aplicativo Android" — que é a verdade.

## O que acontece fora do app Android

No navegador comum, a Digital Goods API não existe. A tela detecta isso e diz
que a assinatura abre pelo aplicativo. Quando você montar a venda pelo site
(que não paga os 15% do Google), o webhook genérico
`/conta/assinatura/webhook` já está pronto para a adquirente chamar — é o
mesmo caminho, com outro porteiro.

## Testar antes de cobrar de verdade

**Play Console → Configuração → Teste de licença**: adicione seu e-mail como
testador de licença. Compras feitas por essas contas nas faixas de teste são
processadas de ponta a ponta, com notificação e tudo, **sem cobrança real**. As
renovações também são aceleradas — uma assinatura mensal renova em minutos, o
que permite ver a RTDN de renovação funcionando sem esperar um mês.

## Testes automatizados

```
python test_billing.py
```

Substitui `modules.play` por um dublê e injeta a Digital Goods API no
navegador. Prova as 31 regras: token desconhecido recusado, token válido
concedendo acesso, compra pendente sendo reconhecida, anônimo barrado,
notificação com segredo errado barrada, notificação repetida ignorada,
cancelada mantendo acesso até vencer, expirada revogando, usuário desistindo
sem virar erro, e a compra órfã sendo recuperada sozinha na abertura seguinte.

## Referências

- [Receive Payments via Google Play Billing with the Digital Goods API](https://developer.chrome.com/docs/android/trusted-web-activity/receive-payments-play-billing/)
- [Real-time Developer Notifications reference](https://developer.android.com/google/play/billing/rtdn-reference)
- [Purchases.subscriptionsv2.get](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2/get)
