# Deploy do Alphaforge

`alphaforge.api.br` — droplet Ubuntu na DigitalOcean, Nginx com Let's Encrypt
na frente, Uvicorn servindo o FastAPI sob systemd.

## Como funciona hoje

    máquina local  --git push-->  GitHub  --Actions-->  droplet  --systemd-->  no ar

O `.github/workflows/deploy.yml` roda a suíte de testes primeiro. **Teste
vermelho não chega no servidor.** Passando, ele entra por SSH e executa o
`deploy.sh`, que:

1. compara o commit local com a `origin/main` e sai sem reiniciar se nada mudou;
2. reinstala dependências **só se o `requirements.txt` mudou** (senão o serviço
   ficaria fora do ar por um minuto a cada deploy, sem motivo);
3. reinicia o serviço;
4. espera o `/health` responder;
5. **se não responder, volta para o commit anterior e reinicia de novo.**

O passo 5 é o que importa: um deploy ruim derruba o site por segundos, não até
alguém perceber. O `/health` não consulta fonte externa de propósito — se ele
falhou, o problema é o serviço, não o Yahoo oscilando.

## Configuração, uma vez só

### 1. Chave de deploy

No **seu computador** (a chave privada nunca sai daqui para lugar nenhum além
do cofre do GitHub):

    ssh-keygen -t ed25519 -C "deploy-alphaforge" -f $HOME\.ssh\alphaforge_deploy

Copie a **pública** (`alphaforge_deploy.pub`) para o droplet:

    ssh <usuario>@alphaforge.api.br "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys" < $HOME\.ssh\alphaforge_deploy.pub

### 2. Segredos no GitHub

`github.com/lucaswn99-gif/alphaforge` → **Settings** → **Secrets and variables**
→ **Actions** → **New repository secret**:

| Nome | Valor |
|---|---|
| `DROPLET_HOST` | `alphaforge.api.br` |
| `DROPLET_USER` | o usuário que roda o serviço |
| `DROPLET_SSH_KEY` | conteúdo do arquivo **privado** `alphaforge_deploy` |
| `DROPLET_DIR` | caminho do projeto (ex.: `/opt/alphaforge`) |
| `DROPLET_SERVICE` | nome da unit (`systemctl list-units --type=service \| grep -i alpha`) |

Segredo do GitHub é escrita-apenas: depois de salvo, nem você nem eu
conseguimos lê-lo de volta. É por isso que este é o caminho certo — a
credencial não passa por conversa nenhuma.

### 3. Permitir o restart sem senha

Se o usuário do deploy não for root, autorize só o comando exato — no droplet,
`sudo visudo -f /etc/sudoers.d/alphaforge`:

    deploy ALL=(ALL) NOPASSWD: /bin/systemctl restart alphaforge

Uma linha, um comando. Não dê sudo geral para um usuário de deploy.

## As bases da CVM

`fundamentos_cvm.db` e `fundos_cvm.db` são versionados no repositório e sobem
junto no `git pull` — o servidor **não** baixa nem processa a DFP em tempo de
requisição. Para atualizá-los, rode na sua máquina e faça push:

    python atualizar_fundamentos_cvm.py     # balanços (DFP)
    python atualizar_fundos_cvm.py          # valor patrimonial de FII
    python verificar_bases.py               # trava de sanidade

O `verificar_bases.py` sai diferente de zero se algo não fechar, e o
`subir.bat` aborta o push nesse caso. Ele existe porque erro de dado passa nos
testes: a base de FII já foi ao ar com cota de R$ 0,02, e a de fundamentos já
subiu sem as colunas de crédito.

## Deploy manual, se precisar

    ssh <usuario>@alphaforge.api.br
    cd /opt/alphaforge && ./deploy.sh

## Verificação rápida

    curl https://alphaforge.api.br/health

Deve devolver `status: ok` e a versão. Se a versão não for a que você acabou de
subir, o pull não aconteceu.
