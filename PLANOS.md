# Planos, contas e cotas

Como o AlphaForge separa gratuito de Premium, onde a regra mora e o que ainda
falta para cobrar de verdade.

## O que foi construído

Três blocos novos e um remendo em cada router que precisava de trava:

| Arquivo | Papel |
|---|---|
| `modules/contas.py` | Usuários, senhas, sessões e contagem de uso. SQLite em `contas.db`. |
| `modules/planos.py` | Definição dos planos, cotas, bloqueios e corte de resposta. |
| `routers/conta.py` | `/conta/registrar`, `/conta/entrar`, `/conta/sair`, `/conta/eu` e o webhook de assinatura. |
| `templates/index.html` | Botão de conta no cabeçalho, formulário de login, aviso de limite e faixa de lista cortada. |

Nenhuma dependência nova. O hash de senha usa `hashlib.scrypt`, da biblioteca
padrão — não há o que instalar no droplet.

## O corte

A régua do gratuito é **volume, não função**. Quem não assina vê os cinco
primeiros papéis do ranking com número real e completo; sente falta dos outros
setenta e cinco. Esconder o módulo inteiro faz desinstalar.

| Módulo | Gratuito | Premium |
|---|---|---|
| `/api/mercado` | completo | igual |
| `/renda-variavel/scanner-quantamental` | 5 papéis, sem os fatores do score | 80 papéis, fatores expostos |
| `/renda-variavel/acao/{ticker}` | 3 por dia | ilimitado |
| `/api/quant/scanner` | 3 papéis | completo |
| `/api/quant/papel/{ticker}` | 3 por dia | ilimitado |
| `/api/opcoes/precificar` e `/avaliar` | 5 por dia, somados | ilimitado |
| `/api/opcoes/recomendar` | **bloqueado** | completo |
| `/renda-fixa/calcular` | 2 por dia | ilimitado |
| `/renda-fixa/parametros` | completo | igual |
| `/wealth/fundos` | 4 por bloco, sem a recomendação de P/VP | completo |
| `/wealth/otimizar-portfolio` | **bloqueado** | completo |

Os números ficam em `modules/planos.py`, no topo: `LIMITES_FREE`,
`BLOQUEADOS_FREE`, `SCANNER_FREE_LINHAS`, `QUANT_FREE_LINHAS`,
`FUNDOS_FREE_LINHAS`. Mudar o corte é mudar um número, em um lugar.

## Três decisões que valem saber

**O corte é no servidor.** A API do gratuito devolve cinco linhas, não oitenta
com setenta e cinco borradas no CSS. Dado que chega ao navegador é dado
entregue — o painel de rede mostra tudo.

**Conta é opcional.** A ficha do app na Play Store declara que nenhuma parte
exige login, e a revisão testa isso. Quem não entra usa o gratuito com a cota
contada por IP. Um muro de login contradiria a declaração e reprovaria o app.
A conta serve para levar a cota entre aparelhos e para assinar.

**Falha de banco não vira paywall.** Se `contas.db` sumir ou travar, a
verificação de cota deixa passar em vez de bloquear. Um banco fora do ar não
pode transformar o terminal inteiro em tela de assinatura.

## Como rodar

Nada muda. O esquema é criado na primeira subida, dentro do evento de startup:

```
uvicorn api:app --host 0.0.0.0 --port $PORT
```

O arquivo `contas.db` nasce ao lado dos outros bancos, na raiz do projeto. Ele
está no `.gitignore` — guarda hash de senha de usuário real e nunca deve sair
do servidor.

## Dar Premium a alguém, na mão

Enquanto não há cobrança, é assim que se libera uma conta — para você, para um
testador, para quem comprou por fora:

```python
from modules import contas
u = contas.autenticar("pessoa@exemplo.com", "a-senha-dela")   # ou busque pelo id
contas.definir_plano(u["id"], "premium")                       # sem prazo
contas.definir_plano(u["id"], "premium", "2027-01-01T00:00:00+00:00")  # com prazo
```

Premium vencido volta a valer como gratuito sozinho, sem precisar de faxina.

## O webhook de assinatura

`POST /conta/assinatura/webhook` existe e está **inerte de propósito**. Sem a
variável `AF_WEBHOOK_SEGREDO` no ambiente, ele responde 503 — um webhook aberto
é um botão de "me dê Premium" exposto na internet.

Para ligar:

```bash
# no .env do servidor
AF_WEBHOOK_SEGREDO=algo-longo-e-aleatorio
```

E quem chamar manda o mesmo valor no cabeçalho `X-AF-Segredo`:

```json
POST /conta/assinatura/webhook
X-AF-Segredo: algo-longo-e-aleatorio

{"email": "pessoa@exemplo.com", "plano": "premium", "ate": "2027-01-01T00:00:00+00:00"}
```

O Google Play (via Pub/Sub, no Android) e a adquirente do site traduzem o
próprio formato para este corpo antes de chamar. Essa tradução ainda não
existe — é o próximo bloco.

## O que ainda falta

1. **Checkout.** Não há como pagar. O botão "Ver o Premium" hoje diz a verdade:
   a assinatura não abriu. Trocar por um checkout real é o próximo passo.
2. **Recuperação de senha.** Quem esquecer a senha hoje não tem caminho de
   volta. Precisa de envio de e-mail, que o projeto ainda não tem.
3. **Verificação de e-mail.** Não bloqueia o lançamento, mas antes de cobrar
   convém confirmar que o endereço existe.
4. **Declaração de recursos financeiros** na ficha do Play, junto da política
   de privacidade. Formulário, não código.

## Cobrança no Android — a data que importa

Assinatura vendida dentro do app Android passa obrigatoriamente pelo
faturamento do Google, que retém **15%** (10% de serviço + 5% de faturamento)
na faixa de até US$ 1 milhão por ano. A abertura para link de pagamento externo
já vale em outros mercados; o Brasil só entra em **setembro de 2027**, quando a
comissão cai para 10%.

Na prática: venda a assinatura no site, deixe o app Android apenas reconhecer o
login. Quem assinar pelo celular paga os 15% — e ainda vale a pena, porque é o
canal de menor atrito.

## Testes

```
python test_planos.py
```

Sobe uma app mínima com as mesmas dependências das rotas reais e prova as
quarenta regras: corte de lista, bloqueio, cota estourando, cota seguindo a
conta em vez do IP, login, webhook e Premium vencido voltando a gratuito.
