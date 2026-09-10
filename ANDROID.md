# AlphaForge no Android

O AlphaForge já é um PWA instalável. Este documento cobre o passo seguinte:
empacotar como **TWA (Trusted Web Activity)** e publicar na Play Store.

TWA é o formato sancionado pelo Google para app baseado em web. Ele roda o
Chrome por baixo, sem barra de navegador, com o domínio verificado. **Não use
WebView cru** — a política de funcionalidade mínima do Google rejeita como
"webview spam".

A cobrança da assinatura tem documento próprio: veja `PLAY_BILLING.md`.

---

## 0. O que já existe

| Peça | Onde | Estado |
|---|---|---|
| `manifest.webmanifest` | `static/` | pronto, com ícones 96/192/512 + maskable |
| Service worker | `static/sw.js`, servido em `/sw.js` | pronto |
| `assetlinks.json` | rota `/.well-known/assetlinks.json` | **falta a impressão digital** |
| `twa-manifest.json` | raiz do projeto | pronto, com Play Billing ligado |
| Política de privacidade | `/privacidade` | pronto |
| Termos de uso | `/termos` | pronto |
| Aviso de não-recomendação | rodapé + faixa + `/api/legal` | pronto |
| Contas e planos | `modules/contas.py`, `modules/planos.py` | pronto |
| Assinatura pelo Play | `modules/play.py`, `routers/conta.py` | pronto, falta configurar |

O service worker é publicado na **raiz** de propósito. Um SW só controla o
escopo a partir da própria pasta: servido de `/static/sw.js` ele controlaria
apenas `/static`, e o app instalado não abriria. É o erro que quebra a maior
parte das tentativas.

---

## 1. A conta no Play Console

Você abriu como **pessoa física** — a de organização exige verificação D-U-N-S,
que MEI não atende. Isso tem uma consequência que define o cronograma:

> Conta pessoal criada depois de 13/11/2023 precisa de **teste fechado com 12
> testadores por 14 dias consecutivos** antes de liberar a produção.

E a exigência é **por app**, não por conta. O Bolhas cumprindo o prazo não
dispensa o AlphaForge de cumprir o dele. A boa notícia: os mesmos 12
testadores servem para os dois — eles só precisam aceitar o convite da faixa
de teste de cada app, e o relógio de 14 dias reinicia para cada um.

**Aproveite o paralelo.** Suba o teste fechado do AlphaForge assim que ele
existir, mesmo incompleto: os 14 dias correm ao mesmo tempo que os do Bolhas
em vez de depois.

---

## 2. Gerar a chave de assinatura

```bash
keytool -genkeypair -v \
  -keystore alphaforge.keystore \
  -alias alphaforge \
  -keyalg RSA -keysize 2048 -validity 10000
```

> **Guarde o `.keystore` e a senha fora do repositório.** Perder essa chave
> significa não conseguir mais atualizar o app publicado. Ela está no
> `.gitignore` — confira antes do primeiro commit.

Pegue a impressão digital:

```bash
keytool -list -v -keystore alphaforge.keystore -alias alphaforge
```

Copie a linha `SHA256:` — 65 caracteres, no formato `AB:CD:EF:...`.

---

## 3. Publicar o Digital Asset Links

No `.env` do droplet:

```
ANDROID_PACOTE=br.api.alphaforge.twa
ANDROID_SHA256=AB:CD:EF:...
```

Reinicie o serviço e confira:

```bash
curl -s https://alphaforge.api.br/.well-known/assetlinks.json | jq
```

Sem a variável, a rota devolve **503 com instrução** em vez de lista vazia —
lista vazia é JSON válido e faz o TWA falhar em silêncio, que é o pior modo
de falhar.

Servir isso pela API, e não como arquivo estático, evita de saída dois
problemas clássicos: o `Content-Type` sai certo sempre, e não há como um
editor de texto gravar um BOM no começo do arquivo.

### Duas impressões, não uma

Com o **Play App Signing** (o padrão, e recomendado), o Google re-assina o app
com uma chave própria. Existem então duas impressões:

1. a **sua chave de upload** (a do passo 2)
2. a **chave de assinatura do Google**, em *Play Console → Proteção → Gerencie
   a Assinatura de Apps do Google Play*

Cuidado com a tela: ela mostra **duas** seções. A de cima é a "Chave de
assinatura do app" — é essa. A de baixo é o "Certificado da chave de upload",
que é a sua própria, a mesma do passo 2.

**As duas precisam estar no `assetlinks.json`**, separadas por vírgula:

```
ANDROID_SHA256=AB:CD:...,12:34:...
```

Esquecer a segunda é o motivo nº 1 de o app abrir com barra de navegador
depois de publicado — funciona no seu celular de teste e quebra na loja.

---

## 4. Gerar o `.aab`

O `twa-manifest.json` já está pronto na raiz do projeto, com o Play Billing
ligado. Copie-o para a pasta onde vai construir e rode:

```bash
npm i -g @bubblewrap/cli

bubblewrap init --manifest https://alphaforge.api.br/static/manifest.webmanifest
```

**Quando o assistente perguntar pela chave, aponte para o
`alphaforge.keystore` que você criou** — não aceite o padrão. No Bolhas, o
assistente criou uma chave própria em silêncio e a impressão publicada deixou
de bater com a do APK.

Depois do `init`, sobrescreva o `twa-manifest.json` gerado pelo que está no
repositório (ele tem o Play Billing e os atalhos), e então:

```bash
bubblewrap update
bubblewrap build
```

Sai `app-release-bundle.aab` para a loja e `app-release-signed.apk` para teste.

### Confira antes de instalar

```bash
python verificar_twa.py app-release-signed.apk
```

Isso compara três coisas que moram em lugares diferentes e precisam concordar:
o nome do pacote dentro do APK, a assinatura dentro do APK, e o que o
`assetlinks.json` publicado declara. Também detecta BOM e `Content-Type`
errado.

Este script existe por causa de um dia perdido no Bolhas: o app abria com
barra de navegador e a suspeita passou por cache, BOM, chave e estado do
aparelho — quando a causa era um nome de pacote com um segmento a mais no
`assetlinks.json` do que o compilado no APK. Nada avisa; o Chrome só deixa de
verificar, em silêncio.

Depois:

```bash
bubblewrap install
```

**O que checar no aparelho:** o app abre **sem barra de endereço**. Se
aparecer barra, rode o verificador — ele diz qual dos três não bate.

---

## 5. Ligar a assinatura

O `twa-manifest.json` já traz:

```json
"playBilling": { "enabled": true },
"alphaDependencies": { "enabled": true }
```

Isso acrescenta a permissão `com.android.vending.BILLING` ao APK e habilita a
Digital Goods API dentro do app. Sem isso, `getDigitalGoodsService` não existe
e a tela mostra "a assinatura abre pelo aplicativo Android" — que seria a
verdade, mas dentro do próprio app fica esquisito.

O resto da configuração (produtos, conta de serviço, Pub/Sub) está em
`PLAY_BILLING.md`.

---

## 6. Formulários obrigatórios no Play Console

| Formulário | O que informar |
|---|---|
| **Política de privacidade** | `https://alphaforge.api.br/privacidade` |
| **Data safety** | coleta e-mail (conta) e registros técnicos (IP, user-agent); não compartilha para publicidade |
| **Financial features declaration** | app de informação sobre investimentos; **não** é corretora, **não** intermedeia ordem, **não** custodia recurso |
| **Detalhes do login** | há conta, mas **nenhuma parte é restrita** — o gratuito funciona sem login. Marque "Não". |
| **Classificação de conteúdo** | questionário padrão |
| **Público-alvo** | marque só 18+, para não cair na Play Families Policy |

Antes de enviar, defina o contato legal no `.env` do droplet:

```
CONTATO_LEGAL=seu-email-de-contato@dominio
```

Sem isso a página de privacidade diz que o canal não foi configurado — o que
é honesto, mas o Google exige um contato real.

> A resposta de "Detalhes do login" precisa continuar verdadeira. O dia em que
> o AlphaForge exigir conta para abrir, essa declaração vira mentira e a
> próxima atualização pode ser recusada. É por isso que o plano gratuito
> funciona sem cadastro.

---

## 7. O que ainda falta para o app valer a pena

Não jogue as sete abas no celular. Otimizador, precificador de opções e
calculadora de crédito são ferramentas de mesa e ficam ruins em tela pequena.

O que justifica um app em vez de um site é **notificação**:

- "PETR4 rompeu a EMA21 com volume 1,8x"
- "Seu FII divulgou rendimento: R$ 0,92"
- "Sua trava de alta vence em 3 dias úteis"

O motor de cálculo para isso já existe (`modules/quant.py`). Falta o agendador
e o FCM. Sem push, o app é um marcador com ícone — e o `enableNotifications`
está `false` no manifesto justamente por isso: pedir permissão de notificação
para nunca mandar nenhuma queima a permissão.

---

## Ordem sugerida

1. Gerar a chave e publicar o `assetlinks.json` com a primeira impressão
2. `bubblewrap init` → substituir o `twa-manifest.json` → `update` → `build`
3. `python verificar_twa.py` e testar no aparelho
4. Criar o app no Console, subir o `.aab` em teste interno
5. Pegar a segunda impressão (Play App Signing) e acrescentar ao `ANDROID_SHA256`
6. Rodar o verificador de novo — agora com duas impressões
7. Configurar a assinatura (`PLAY_BILLING.md`)
8. Abrir o teste fechado e recrutar os 12 testadores
9. Preencher os formulários enquanto os 14 dias correm
10. Solicitar acesso de produção
