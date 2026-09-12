# Motores de filosofia e painel de BDRs

Dois submódulos novos, desacoplados do motor de ordens: `PhilosophyEngine`
(sinais e rankings) e `GlobalEquitiesPanel` (ações EUA contra BDRs).

| Arquivo | Papel |
|---|---|
| `modules/rede.py` | GET com nova tentativa e recuo exponencial. |
| `modules/fontes.py` | Camada de dados injetável: Yahoo, SEC EDGAR, e o saneamento de todo número que entra. |
| `modules/filosofias.py` | `PhilosophyEngine` e `MotorMomentum`. |
| `modules/bdr.py` | `GlobalEquitiesPanel`. |
| `routers/filosofias.py` | As cinco rotas, com cache e corte por plano. |
| `test_filosofias.py` | 136 verificações, todas com fontes falsas. |
| `conferir_filosofias.py` | Roda os motores contra dado real e aponta contrato quebrado. |
| `conferir_bdr.py` | Afere os fatores de paridade contra o preço de tela. |
| `smoke_frontend.py` | Abre a aba Filosofias num Chromium headless e confere a renderização. |
| `templates/index.html` | Aba "Filosofias", com sub-abas para Bogle, Barsi, Greenblatt e BDR. |

Dependência nova: nenhuma. Tudo roda com o que o projeto já tem.

## Rotas

| Rota | O que faz |
|---|---|
| `GET /filosofias/bogle?posicoes=VOO:10,WRLD11.SA:300` | Distância até o alvo e o ajuste que a fecha. |
| `GET /filosofias/barsi` | Ranking BESST por preço teto e margem de segurança. |
| `GET /filosofias/greenblatt` | Magic Formula com trava de Shareholder Yield. |
| `GET /filosofias/bdr?ativos=AAPL,MSFT` | Paridade entre ação e BDR. |
| `GET /filosofias/universos` | O que cada motor varre e com que critérios. |

As varreduras ficam 15 minutos em cache; `forcar=true` fura.

## Como usar fora da API

```python
from modules.filosofias import PhilosophyEngine
from modules.bdr import GlobalEquitiesPanel

motor = PhilosophyEngine()
motor.satelite_barsi()                                  # BESST
motor.satelite_greenblatt()                             # EUA
motor.nucleo_bogle({"VOO": 12, "WRLD11.SA": 300})       # rebalanceamento

GlobalEquitiesPanel().painel(["AAPL", "MSFT"])          # DataFrame
```

As fontes são injetáveis — é o que permite testar sem rede:

```python
PhilosophyEngine(fonte=MinhaFonteFalsa())
```

## As três decisões que moldaram o código

**Momentum não é cruzamento de média.** O filtro global usa o 12M-1M
acadêmico: retorno de doze meses ignorando o mês mais recente. O mês pulado é
o ponto inteiro — o curtíssimo prazo reverte, e incluí-lo transforma um fator
de continuidade num fator de reversão.

**"Não apurado" nunca vira zero.** Ativo sem EBIT publicado não tem
dívida/EBITDA infinita nem nula: tem indicador ausente, que não pontua a favor
nem contra e aparece em `nao_apurados`. Zero e "não sei" produzem decisões
opostas num ranking.

**Nada aqui é recomendação.** São filtros quantitativos aplicados igualmente a
todo o universo, sem considerar perfil ou objetivo de ninguém. O alvo da
carteira Bogle é política de quem investe — o motor mede a distância até ele,
não escolhe o alvo.

## Limitações declaradas

Estão no código, e repetidas aqui porque mudam a leitura do resultado.

**Barsi — cobertura mínima de critérios.** Um papel só pode ser aprovado se
pelo menos 2 dos 3 critérios de qualidade (payout, alavancagem, constância de
lucro) tiverem sido de fato **medidos**. Sem essa trava, "não apurado não
pontua contra" vira aprovação por omissão — foi assim que o BBAS3 passou numa
verificação com payout e alavancagem ambos ausentes, sustentado só pelo preço.
Num filtro de renda, aprovar empresa cuja sustentabilidade do provento não se
conseguiu medir é o erro mais caro possível. O campo `criterios_medidos` diz
quantos saíram.

**Barsi — payout tem duas vias.** A principal é DPA sobre LPA, com ajuste para
units (o provento é por unit, o `lpa_on` da CVM é por ação ordinária — ignorar
isso dava payout de 211% na TAEE11, quando o real é 70%). Quando o LPA não vem
— o caso dos bancos na base da CVM, metade do universo BESST — cai para
proventos totais sobre lucro total, com o número de ações derivado de valor de
mercado sobre preço. Essa segunda via é **aproximada** para companhia com ON e
PN, e o campo `origem_payout` diz qual foi usada.

**Barsi — é EBIT, não EBITDA.** A DFP coletada não traz depreciação separada.
O denominador sai menor que o EBITDA real, então o múltiplo é **mais
conservador**: reprova antes, nunca depois.

**Barsi — três exercícios, não cinco.** A base cobre 2023–2025. O critério
clássico pede cinco anos de lucro; o campo `exercicios_com_lucro` diz quantos
foram de fato verificados, para quem lê saber o peso do "passou".

**Barsi — tendência de DPA é indicador, não filtro.** Cada papel aprovado ou
reprovado carrega `tendencia_dpa`: classifica a trajetória do provento por
ação nos últimos exercícios fechados como `crescente`, `estavel`,
`decrescente` ou `nao_apurado`. A janela (`ANOS_TENDENCIA_DPA = 5`) é
deliberadamente mais larga que a do preço teto (`ANOS_DPA = 3`) — três pontos
bastam para uma média, não para dizer se o provento está subindo. Abaixo de
`MINIMO_ANOS_TENDENCIA = 3` exercícios, ou com algum ano sem provento
positivo na janela, a classificação vem `nao_apurado` em vez de arriscar um
CAGR sobre base insuficiente. É puramente informativo: **não entra em
`motivos_reprova` nem em `criterios_medidos`** — preço barato com provento em
queda ainda pode ser barato, e quem decide isso é quem lê o ranking, não o
motor por trás de um "aprovado" silencioso. O campo `consistencia`
(`sempre_subiu` / `sempre_caiu` / `com_oscilacao`) mostra se o CAGR resume uma
trajetória limpa ou um zigue-zague que só parece limpo no agregado.

**Barsi — Basileia não é apurada.** Banco e seguradora não entram no teste de
dívida/EBIT (captar recurso é matéria-prima deles, não alavancagem). O
critério equivalente seria Basileia > 13%, que a base da CVM não publica. Eles
vêm marcados como não apurado em vez de aprovados por omissão.

**Barsi — o universo BESST é curado à mão.** O cadastro da B3 que o projeto
guarda traz CNPJ e razão social, não setor. E BESST não é classificação da
bolsa: é uma tese sobre setores perenes. Derivar de um campo que não existe
seria inventar precisão. A lista está em `UNIVERSO_BESST`.

**Greenblatt — SEC opcional.** Sem `SEC_USER_AGENT` no ambiente, o balanço vem
do Yahoo. O campo `origem_contabil` diz de onde veio cada linha.

```bash
# no .env — a SEC exige identificação, senão responde 403
SEC_USER_AGENT=Lucas Werner lucaswn99@gmail.com
```

**BDR — o fator de paridade é o risco do módulo, e não é hipótese.** Na
primeira aferição contra o mercado, **18 dos 20 fatores desta tabela estavam
errados**. Nenhum produziu sinal falso, porque o painel desconfia da própria
tabela — mas a lição é que fator de BDR não se escreve de memória, se mede.

A tabela atual foi aferida em 12/09/2026 por `conferir_bdr.py`. Rode de novo
depois de qualquer desdobramento.

Por isso o painel não confia na tabela. Ele calcula a razão **implícita** a
partir dos preços e compara. Divergência não vira sinal — vira aviso:

```
"A razão configurada (12) não bate com a implícita (10.02).
 O mercado sugere 10. Confira o fator na B3 — a tabela é de 2026-09."
```

A inferência é por **arredondamento ao inteiro**, com tolerância de 2%: fator
de BDR é número inteiro, e implícita a menos de 2% de um inteiro é esse
inteiro. A primeira versão escolhia de uma lista de razões "comuns" e errava
seis dos vinte por 4 a 7% — inclusive respondendo 25 para o MSFT34, cuja
implícita é 24,05. Lista curada de valores plausíveis tem o mesmo defeito da
tabela que deveria corrigir: envelhece sem avisar.

Sem razão configurada, a implícita arredondada assume, marcada como
`derivada`. E `confiavel` só é verdadeiro quando a razão veio da tabela **e** o
BDR tem liquidez: spread em papel parado é preço velho, não distorção.

Confira os fatores em b3.com.br → Produtos e Serviços → BDR e atualize
`BDRS_POR_ACAO` e `VERIFICADO_EM` em `modules/bdr.py`.

## Corte por plano

| Rota | Gratuito | Premium |
|---|---|---|
| `/filosofias/bogle` | 3 por dia | ilimitado |
| `/filosofias/barsi` | 3 aprovados, sem os reprovados | tudo, com o porquê de cada reprovação |
| `/filosofias/greenblatt` | 3 do ranking, universo de 40 ativos | top 20, universo completo (118) |
| `/filosofias/bdr` | 5 ativos | painel inteiro |

A lista de reprovados do Barsi é do Premium de propósito: saber **por que** um
papel não passou é metade do valor do módulo.

A cota do Bogle é debitada **depois** de validar a entrada. Cobrar antes seria
cobrar por mensagem de erro — quem digita o ticker errado perderia uma das
três consultas do dia sem nada ter sido calculado.

## Testes

```
python test_filosofias.py       # 136 verificações, sem rede
python conferir_filosofias.py   # os motores contra dado real
python conferir_bdr.py          # os fatores de paridade contra o mercado
```

136 verificações, sem rede. O que provam, além da aritmética: que o momentum
pula mesmo o mês recente (série que sobe 30% e depois desaba tem que marcar
+30%), que carteira em duas moedas é convertida antes de comparar, que banco
não é reprovado por uma dívida/EBIT que não se aplica a ele, que razão de BDR
errada vira aviso em vez de arbitragem, e que fonte que levanta exceção vira
ressalva em vez de derrubar a varredura.

O arquivo é pulado pelo `pytest` que guarda o deploy — ele sobe servidor no
corpo do módulo e quebraria a coleta.

`smoke_frontend.py` cobre a camada visual: sobe a API com motores falsos,
abre um Chromium headless de verdade (Playwright) e navega pela aba
Filosofias — Bogle, Barsi, Greenblatt e BDR — checando que cada sub-aba
renderiza os dados esperados sem erro de execução em JS. Roda fora do
`pytest` pelo mesmo motivo dos outros scripts de verificação: sobe servidor e
navegador no corpo do módulo.

```
python smoke_frontend.py        # a aba Filosofias, num navegador de verdade
```

## Frontend — aba Filosofias

`templates/index.html` ganhou uma aba nova, com quatro sub-abas — os quatro
motores moram juntos porque fazem a mesma pergunta ("o que este critério diz
sobre este ativo, com o quê medido e o quê não apurado"), e abas de primeiro
nível para cada um empurraria o menu para fora da tela no celular:

- **Bogle** — campos de posições e alvo (`TICKER:QUANTIDADE` /
  `TICKER:PERCENTUAL`), banda de rebalanceamento, e a tabela de peso
  atual × alvo × ação sugerida.
- **Barsi** — tabela BESST com preço teto, margem de segurança, payout,
  dívida/EBIT, o badge de **tendência de DPA** (verde para crescente, neutro
  para estável, vermelho para decrescente, cinza para não apurado — com o
  CAGR e o motivo no `title`) e o badge de momentum.
- **Greenblatt** — ranking com posto combinado, EV/EBIT, ROIC, Shareholder
  Yield e o mesmo badge de momentum (com aviso de "rebaixado" quando o
  momentum penalizou o posto sem excluir o ativo).
- **BDR × Ação** — o painel de paridade, com a data em que a tabela de
  razões foi conferida e o selo "CONFIÁVEL" só quando a razão veio da tabela
  e o BDR tem liquidez.

Barsi, Greenblatt e BDR carregam sob demanda — só buscam na primeira vez que
a sub-aba abre (`JA_CARREGADA`), porque são varreduras caras. Bogle nunca
carrega sozinho: precisa da carteira de quem está olhando.

As três rotas que o servidor pode cortar por plano (`/filosofias/barsi`,
`/filosofias/greenblatt`, `/filosofias/bdr`) entraram em `LISTAS_CORTAVEIS`:
a faixa "Gratuito … Ver tudo" que já existia para o scanner e o radar de
fundos aparece aqui do mesmo jeito, sem código novo por rota. O Bogle ganhou
entrada em `RECURSOS` (`bogle_rebalanceamento`) para o aviso de limite diário
mostrar o nome certo em vez da chave crua.
