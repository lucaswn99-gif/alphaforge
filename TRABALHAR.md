# Trabalhar de outra máquina

O projeto inteiro vive no GitHub, inclusive as bases da CVM. Qualquer máquina
com Git e Python trabalha nele.

## Primeira vez no notebook

    git clone https://github.com/lucaswn99-gif/alphaforge.git
    cd alphaforge
    python -m venv venv
    venv\Scripts\activate            # Windows
    pip install -r requirements.txt pytest

Rodar local para conferir antes de subir:

    uvicorn api:app --reload --port 8000
    # abre http://127.0.0.1:8000

## Ciclo de trabalho

    python verificar_bases.py        # trava de sanidade das bases
    python -m pytest -q              # a suíte inteira
    git add -A && git commit -m "..." && git push origin main

O `subir.bat` faz esses três passos de uma vez, e **aborta o push** se a trava
ou os testes falharem. Ele é Windows; em Linux ou Mac use os comandos acima.

## Deploy

**Com o GitHub Actions configurado (recomendado):** o `git push` publica
sozinho. Nada mais a fazer, de qualquer máquina — inclusive editando pelo site
do GitHub, do celular. Ver `DEPLOY.md` para os cinco segredos a cadastrar.

**Sem o Actions:** você precisa da chave SSH do droplet naquela máquina.

    ssh root@alphaforge.api.br "cd /root/alphaforge && git pull origin main && systemctl restart alphaforge"

Isso significa copiar a chave privada para o notebook — o que é justamente o
motivo de o Actions ser melhor: a chave fica num cofre só, e não espalhada por
cada máquina de onde você trabalha.

## O que NÃO está no repositório

- `.env` (chaves de API). Hoje nenhuma é necessária: o crédito virou
  calculadora e não usa LLM.
- `venv/` — cada máquina cria a sua.
- `modules/cache_ibov.json` — regenerado sozinho a cada 12h.

## Atualizar as bases da CVM

Roda em qualquer máquina; o resultado vai para o repositório e chega ao
servidor no deploy seguinte.

    python atualizar_fundamentos_cvm.py     # balanços (DFP) — alguns minutos
    python atualizar_fundos_cvm.py          # valor patrimonial de FII
    python verificar_bases.py               # confere antes de subir

O servidor nunca baixa nem processa a CVM em tempo de requisição — ele só lê
os arquivos que vieram no `git pull`.
