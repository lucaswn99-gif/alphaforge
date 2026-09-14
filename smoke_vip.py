"""Smoke do console VIP num Chromium de verdade.

O que só o browser prova: que o login redireciona para o console, que o
console redireciona para o login, que a tabela renderiza, que o aporte
aparece com a média ponderada certa NA TELA (e não só na API), e que o fundo
gerado do login existe sem quebrar layout.
"""
import os
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AF_WEBHOOK_SEGREDO", "segredo-de-teste")

from modules import contas
contas.CAMINHO_BANCO = os.path.join(tempfile.mkdtemp(), "contas_smoke.db")

import api
import uvicorn


def rodar():
    uvicorn.run(api.app, host="127.0.0.1", port=8996, log_level="warning")


if __name__ == "__main__":
    threading.Thread(target=rodar, daemon=True).start()
    time.sleep(2.0)

    from playwright.sync_api import sync_playwright

    erros, falhas = [], []

    def checar(rotulo, condicao, extra=""):
        if condicao:
            print(f"  ok   {rotulo}")
        else:
            print(f"  FALHA {rotulo} {extra}")
            falhas.append(rotulo)

    BASE = "http://127.0.0.1:8996"
    with sync_playwright() as p:
        navegador = p.chromium.launch(
            executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
        pagina = navegador.new_page()
        pagina.on("console", lambda m: erros.append(m.text) if m.type == "error" else None)
        pagina.on("pageerror", lambda e: erros.append(str(e)))

        # -------- login --------
        pagina.goto(f"{BASE}/vip/login", wait_until="networkidle")
        checar("login carregou", "Console VIP" in pagina.title() or "AlphaForge" in pagina.title())
        checar("tela de login visível",
               pagina.eval_on_selector("#telaLogin", "el => !el.classList.contains('hidden')"))
        checar("fundo gerado está no DOM (sem imagem de rede)",
               pagina.eval_on_selector_all(".palco svg path.curva", "els => els.length") == 1)
        checar("nenhuma tag <img> na página (fundo é CSS/SVG)",
               pagina.eval_on_selector_all("img", "els => els.length") == 0)

        # -------- console sem sessão redireciona --------
        pagina.goto(f"{BASE}/vip", wait_until="networkidle")
        checar("console sem sessão cai no login", pagina.url.endswith("/vip/login"), pagina.url)

        # -------- entrar (conta premium) --------
        pagina.request.post(f"{BASE}/conta/registrar",
                            data={"email": "vip@smoke.com", "senha": "senha-boa-123"})
        contas.definir_plano(contas.autenticar("vip@smoke.com", "senha-boa-123")["id"], "premium")

        # Registrar já abre sessão. Quem tem sessão e abre /vip/login vai
        # direto ao console — a URL não pode discordar do estado real.
        pagina.goto(f"{BASE}/vip/login", wait_until="networkidle")
        pagina.wait_for_url("**/vip", timeout=8000)
        checar("sessão aberta em /vip/login cai no console", pagina.url.endswith("/vip"))

        # Agora sem cookie, para exercitar o formulário de verdade.
        pagina.context.clear_cookies()
        pagina.goto(f"{BASE}/vip/login", wait_until="networkidle")
        pagina.fill("#email", "vip@smoke.com")
        pagina.fill("#senha", "senha-boa-123")
        pagina.click("#btnEntrar")
        pagina.wait_for_url("**/vip", timeout=8000)
        pagina.wait_for_selector("#painelCarteira:not(.hidden)", timeout=8000)
        checar("login leva ao console", pagina.url.endswith("/vip"))
        checar("estado vazio aparece na carteira nova",
               pagina.eval_on_selector("#estadoVazio", "el => !el.classList.contains('hidden')"))

        # -------- primeira posição --------
        pagina.fill("#ticker", "petr4")
        pagina.fill("#quantidade", "100")
        pagina.fill("#precoMedio", "30")
        pagina.click("#btnAdicionar")
        pagina.wait_for_selector("#tabelaPosicoes tr", timeout=8000)
        texto = pagina.inner_text("#tabelaPosicoes")
        checar("posição renderizou em caixa alta", "PETR4" in texto, texto[:150])
        checar("custo total na faixa de topo", "3.000,00" in pagina.inner_text("#custoTotal"),
               pagina.inner_text("#custoTotal"))

        # -------- aporte: média ponderada na tela --------
        pagina.fill("#ticker", "PETR4")
        pagina.fill("#quantidade", "100")
        pagina.fill("#precoMedio", "40")
        pagina.click("#btnAdicionar")
        pagina.wait_for_function(
            "() => document.getElementById('custoTotal').innerText.includes('7.000')",
            timeout=8000)
        linhas = pagina.eval_on_selector_all("#tabelaPosicoes tr", "els => els.length")
        checar("aporte não criou segunda linha", linhas == 1, linhas)
        checar("preço médio virou 35,00 na tela", "35,00" in pagina.inner_text("#tabelaPosicoes"),
               pagina.inner_text("#tabelaPosicoes")[:200])
        checar("aviso diz que o aporte foi incorporado",
               "incorporado" in pagina.inner_text("#avisoItem"), pagina.inner_text("#avisoItem"))

        # -------- papel fora dos registros entra marcado --------
        pagina.fill("#ticker", "ZZZZ3")
        pagina.fill("#quantidade", "10")
        pagina.fill("#precoMedio", "5")
        pagina.click("#btnAdicionar")
        pagina.wait_for_function(
            "() => document.getElementById('tabelaPosicoes').innerText.includes('ZZZZ3')",
            timeout=8000)
        checar("papel desconhecido entra marcado, não é recusado",
               "Não verificado" in pagina.inner_text("#tabelaPosicoes"),
               pagina.inner_text("#tabelaPosicoes")[:300])

        # -------- ticker inválido: erro na tela, nada gravado --------
        pagina.fill("#ticker", "PETR9")
        pagina.fill("#quantidade", "10")
        pagina.fill("#precoMedio", "5")
        pagina.click("#btnAdicionar")
        pagina.wait_for_function(
            "() => document.getElementById('avisoItem').innerText.includes('forma de ticker')",
            timeout=8000)
        checar("ticker inválido explica o formato aceito",
               "forma de ticker" in pagina.inner_text("#avisoItem"))
        checar("nada foi gravado do ticker inválido",
               "PETR9" not in pagina.inner_text("#tabelaPosicoes"))

        # -------- edição na própria linha (substituiu o prompt) --------
        pagina.click("tr[data-ticker='PETR4'] button:has-text('Corrigir')")
        pagina.wait_for_selector("tr[data-ticker='PETR4'] .campo-linha", timeout=5000)
        checar("edição abre dois campos na linha",
               pagina.eval_on_selector_all("tr[data-ticker='PETR4'] .campo-linha",
                                           "els => els.length") == 2)
        pagina.fill("tr[data-ticker='PETR4'] [data-campo='quantidade']", "50")
        pagina.fill("tr[data-ticker='PETR4'] [data-campo='preco']", "20")
        pagina.click("tr[data-ticker='PETR4'] button:has-text('Salvar')")
        pagina.wait_for_function(
            "() => document.getElementById('custoTotal').innerText.includes('1.050')",
            timeout=8000)
        checar("edição inline gravou (50 x 20 = 1.000 + ZZZZ3 50)",
               "1.050" in pagina.inner_text("#custoTotal"), pagina.inner_text("#custoTotal"))
        checar("campos somem depois de salvar",
               pagina.eval_on_selector_all(".campo-linha", "els => els.length") == 0)

        pagina.click("tr[data-ticker='PETR4'] button:has-text('Corrigir')")
        pagina.wait_for_selector(".campo-linha", timeout=5000)
        pagina.click("tr[data-ticker='PETR4'] button:has-text('Cancelar')")
        checar("cancelar fecha a edição sem gravar",
               pagina.eval_on_selector_all(".campo-linha", "els => els.length") == 0)

        # -------- importação de planilha --------
        import io
        from openpyxl import Workbook
        livro = Workbook()
        livro.active.append(["Posicao consolidada"])          # lixo antes do cabecalho
        livro.active.append([])
        livro.active.append(["Papel", "Qtde", "Preco medio"])
        livro.active.append(["PETR4 - PETROBRAS PN", 300, "32,50"])   # ja existe
        livro.active.append(["VALE3", 40, 60.0])                      # novo
        livro.active.append(["XXXX9", 10, 5.0])                       # invalido
        livro.active.append(["TOTAL", "", 1234.0])                    # rodape
        buffer = io.BytesIO(); livro.save(buffer)

        pagina.set_input_files("#arquivo", {
            "name": "extrato.xlsx",
            "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "buffer": buffer.getvalue()})
        pagina.wait_for_selector("#painelPrevia:not(.hidden)", timeout=8000)

        previa = pagina.inner_text("#tabelaPrevia")
        checar("prévia mostra o papel novo", "VALE3" in previa, previa[:250])
        checar("prévia extrai o ticker da descrição", "PETR4" in previa)
        checar("prévia marca a linha inválida", "XXXX9" in previa and "forma de ticker" in previa)
        checar("rodapé TOTAL não vira linha", "TOTAL" not in previa, previa[:250])
        checar("prévia diz que PETR4 será substituído",
               "Substitui" in previa, previa[:250])
        checar("resumo conta novos e existentes",
               "1 papel(is) novo(s)" in pagina.inner_text("#resumoPrevia"),
               pagina.inner_text("#resumoPrevia"))

        # A prévia não pode ter gravado nada ainda.
        checar("prévia não gravou VALE3",
               "VALE3" not in pagina.inner_text("#tabelaPosicoes"))

        # Trocar para "somar" muda o texto da ação sem novo upload.
        pagina.check("input[name='modoImportacao'][value='somar']")
        checar("modo somar muda a leitura da linha existente",
               "Aporte sobre" in pagina.inner_text("#tabelaPrevia"),
               pagina.inner_text("#tabelaPrevia")[:250])
        pagina.check("input[name='modoImportacao'][value='substituir']")

        pagina.click("#btnConfirmarImportacao")
        pagina.wait_for_function(
            "() => document.getElementById('tabelaPosicoes').innerText.includes('VALE3')",
            timeout=8000)
        checar("importação gravou o papel novo",
               "VALE3" in pagina.inner_text("#tabelaPosicoes"))
        checar("substituir trocou PETR4 por 300 @ 32,50",
               "32,50" in pagina.inner_text("#tabelaPosicoes"),
               pagina.inner_text("#tabelaPosicoes")[:300])
        checar("aviso diz quantas entraram",
               "importada" in pagina.inner_text("#avisoImportacao"),
               pagina.inner_text("#avisoImportacao"))

        # -------- planilha em formato não aceito --------
        pagina.set_input_files("#arquivo", {
            "name": "extrato.pdf", "mimeType": "application/pdf", "buffer": b"nao e planilha"})
        pagina.wait_for_function(
            "() => document.getElementById('avisoImportacao').innerText.includes('Formato')",
            timeout=8000)
        checar("formato não aceito explica o que enviar",
               "Formato" in pagina.inner_text("#avisoImportacao"))

        # -------- filtro --------
        pagina.fill("#buscaPosicao", "ZZZ")
        pagina.wait_for_timeout(200)
        checar("filtro esconde o que não casa",
               "PETR4" not in pagina.inner_text("#tabelaPosicoes"),
               pagina.inner_text("#tabelaPosicoes")[:150])
        pagina.fill("#buscaPosicao", "")

        # -------- sair --------
        pagina.click("#btnSair")
        pagina.wait_for_url("**/vip/login", timeout=8000)
        checar("sair devolve ao login", pagina.url.endswith("/vip/login"))

        # -------- conta gratuita vê o convite, não a carteira --------
        pagina.request.post(f"{BASE}/conta/registrar",
                            data={"email": "free@smoke.com", "senha": "senha-boa-123"})
        pagina.goto(f"{BASE}/vip", wait_until="networkidle")
        pagina.wait_for_selector("#painelAssinatura:not(.hidden)", timeout=8000)
        checar("conta gratuita vê o convite de assinatura",
               pagina.eval_on_selector("#painelAssinatura", "el => !el.classList.contains('hidden')"))
        checar("conta gratuita NÃO vê a carteira",
               pagina.eval_on_selector("#painelCarteira", "el => el.classList.contains('hidden')"))

        # -------- responsivo --------
        pagina.set_viewport_size({"width": 390, "height": 780})
        pagina.goto(f"{BASE}/vip/login", wait_until="networkidle")
        largura = pagina.evaluate("document.documentElement.scrollWidth")
        checar("login não estoura a largura no celular", largura <= 400, largura)

        # Ruído esperado deste harness: o sandbox não alcança CDN nem fontes,
        # e o 422 é provocado por este próprio teste, com o ticker inválido —
        # o navegador registra toda resposta 4xx como erro de console. O que
        # importa é não haver erro de EXECUÇÃO (ReferenceError, TypeError).
        ruido = ("ERR_TUNNEL_CONNECTION_FAILED", "status of 404",
                 "status of 422", "bad HTTP response code (404)",
                 "ERR_NAME_NOT_RESOLVED")
        reais = [e for e in erros if not any(r in e for r in ruido)]
        checar("nenhum erro de execução JS", len(reais) == 0, reais)

        navegador.close()

    print()
    if falhas:
        print(f"{len(falhas)} FALHA(S): " + ", ".join(falhas))
        sys.exit(1)
    print("Tudo passou (smoke do console VIP).")
