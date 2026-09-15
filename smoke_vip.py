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
import numpy as np
import pandas as pd

# Motor falso: o diagnostico real consulta perfil no Yahoo, que o sandbox nao
# alcanca. O que este smoke prova e a TELA — selo por linha, cartoes do
# retrato e ordenacao —, nao a aritmetica de Graham, que tem cobertura
# propria em test_filosofias.py.
from routers import filosofias as rota_filosofias


class _FonteDeTeste:
    """Série de preço sintética — cobre as janelas de TODOS os eventos de
    cauda (2008 a 2022) mais folga, para o Pilar 4 ter o que recortar."""

    def precos(self, ticker_sa, periodo="max"):
        semente = abs(hash(ticker_sa)) % 1000
        rng = np.random.default_rng(semente)
        datas = pd.bdate_range("2007-01-01", "2026-08-01")
        retornos = rng.normal(0.0002, 0.018, size=len(datas))
        precos = 10.0 * np.exp(np.cumsum(retornos))
        return pd.Series(precos, index=datas)


class _MomentumDeTeste:
    def avaliar(self, ticker_sa):
        # Fração (nao porcentagem) — mesmo formato do MotorMomentum real.
        # PETR4 vem ruim de proposito, para o radar mostrar um eixo fraco.
        ruim = "PETR4" in ticker_sa
        return {"momentum_12m_1m": -0.08 if ruim else 0.24,
                "volatilidade_anual": 0.44 if ruim else 0.22,
                "sharpe": 0.05 if ruim else 1.4}


class MotorDeTeste:
    def __init__(self):
        self.fonte = _FonteDeTeste()
        self.momentum = _MomentumDeTeste()

    def _balanco_cvm(self, ticker):
        # Balanco sintetico plausivel. PETR4 fica ALAVANCADO de proposito —
        # e o caso que prova que o simulador de Selic reage (cobertura cai
        # com o controle) e que o eixo de Seguranca do radar penaliza.
        if ticker == "PETR4":
            return {"ano": 2024, "divida_curto_prazo": 60_000, "divida_longo_prazo": 320_000,
                     "caixa": 30_000, "ebit": 55_000, "ativo_total": 900_000,
                     "ativo_circulante": 150_000, "passivo_circulante": 160_000,
                     "passivo_nao_circulante": 420_000, "patrimonio_liquido": 320_000,
                     "lucro_liquido": 90_000, "receita_liquida": 500_000,
                     "lucros_acumulados": 120_000}
        if ticker == "VALE3":
            # Caixa liquido: prova o ramo "beneficiado" (juro alto vira ganho).
            return {"ano": 2024, "divida_curto_prazo": 5_000, "divida_longo_prazo": 15_000,
                     "caixa": 60_000, "ebit": 60_000, "ativo_total": 400_000,
                     "ativo_circulante": 120_000, "passivo_circulante": 60_000,
                     "passivo_nao_circulante": 80_000, "patrimonio_liquido": 260_000,
                     "lucro_liquido": 70_000, "receita_liquida": 250_000,
                     "lucros_acumulados": 90_000}
        return None  # papel fora dos nossos registros: some do balanco, nao vira zero

    def _historico_de_lucro(self, ticker):
        if ticker == "PETR4":
            return [90_000, -5_000, 80_000], [2022, 2023, 2024]
        if ticker == "VALE3":
            return [70_000, 65_000, 72_000], [2022, 2023, 2024]
        return [], []

    def setor_besst(self, ticker):
        return {"TAEE11": "energia"}.get(ticker)

    def _avaliar_barsi(self, ticker, setor, aplicar_momentum=True):
        return {"ticker": ticker, "aprovado": True, "motivos": [],
                "preco_teto": 45.0, "margem_seguranca": 0.25,
                "yield_sobre_preco": 7.2, "payout": 62.0,
                "tendencia_dpa": {"classificacao": "crescente"}}

    def _avaliar_bazin(self, ticker):
        # PETR4 com payout estourado: deteriora em Bazin tambem, para o smoke
        # provar que a troca de regua muda o veredito e nao so o rotulo.
        ruim = ticker == "PETR4"
        criterios = {"dy_suficiente": True,
                     "payout_saudavel": not ruim,
                     "alavancagem_ok": True, "abaixo_do_teto": True}
        return {"ticker": ticker, "preco_teto": 40.0, "margem_seguranca": 0.15,
                "dy_12m": 7.0, "payout": 95.0 if ruim else 55.0, "dl_ebit": 1.1,
                "criterios": criterios, "criterios_nao_apurados": [],
                "aprovado": all(criterios.values())}

    def _avaliar_graham(self, ticker, aplicar_momentum=True):
        alerta = "Prejuizo em pelo menos um dos 3 exercicios apurados."
        base = {"ticker": ticker, "aprovado": False, "motivos": [],
                "alertas_qualidade": [], "fora_do_escopo": False,
                "motivo_escopo": None, "numero_graham": 20.0,
                "margem_seguranca": 0.1, "criterios_medidos": 6}
        if ticker == "PETR4":                       # deteriorou: desconformidade
            base.update(motivos=[alerta], alertas_qualidade=[alerta])
        elif ticker == "VALE3":                     # so caro: atencao
            base.update(motivos=["P/L x P/VP = 44.6 - teto 22.5."])
        else:
            base.update(aprovado=True)
        return base


rota_filosofias.motor = lambda: MotorDeTeste()


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
        pagina.wait_for_selector("tr[data-ticker='PETR4'] [data-campo]", timeout=5000)
        checar("edição abre dois campos na linha",
               pagina.eval_on_selector_all("tr[data-ticker='PETR4'] [data-campo]",
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
               pagina.eval_on_selector_all("[data-campo]", "els => els.length") == 0)

        pagina.click("tr[data-ticker='PETR4'] button:has-text('Corrigir')")
        pagina.wait_for_selector("[data-campo]", timeout=5000)
        pagina.click("tr[data-ticker='PETR4'] button:has-text('Cancelar')")
        checar("cancelar fecha a edição sem gravar",
               pagina.eval_on_selector_all("[data-campo]", "els => els.length") == 0)

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

        # A regua precisa estar declarada antes de qualquer veredito de acao:
        # sem filosofia escolhida, acao sai como "nao apurado" DE PROPOSITO.
        pagina.wait_for_selector("#opcoesFilosofia button", timeout=8000)
        checar("sem filosofia escolhida a tela avisa em vez de medir por Graham",
               "apurado" in pagina.inner_text("#avisoFilosofia").lower(),
               pagina.inner_text("#avisoFilosofia"))
        checar("e nenhuma acao foi medida antes da escolha",
               "Escolha a filosofia" in pagina.eval_on_selector(
                   "tr[data-ticker='PETR4'] td[data-celula='diagnostico'] span",
                   "el => el.title"),
               pagina.eval_on_selector(
                   "tr[data-ticker='PETR4'] td[data-celula='diagnostico'] span",
                   "el => el.title"))
        pagina.click("#opcoesFilosofia button >> nth=2")     # Graham
        pagina.wait_for_timeout(1200)

        # -------- diagnóstico --------
        pagina.wait_for_selector("#painelDiagnostico:not(.hidden)", timeout=10000)
        cartoes = pagina.inner_text("#cartoesDiagnostico")
        checar("painel de diagnóstico aparece", "Desconformidade" in cartoes, cartoes[:200])
        checar("os cinco estados aparecem, incluindo Sem filosofia",
               all(r in cartoes for r in ("Desconformidade", "Atenção", "Conforme",
                                          "Não apurado", "Sem filosofia")),
               cartoes[:250])

        tabela = pagina.inner_text("#tabelaPosicoes")
        checar("selo de diagnóstico entra na linha",
               "Desconformidade" in tabela or "Atenção" in tabela, tabela[:250])
        # PETR4 deteriorou (prejuízo) -> desconformidade; VALE3 só está caro
        # -> atenção. Confundir os dois mandaria vender no topo do que deu certo.
        estado_petr = pagina.eval_on_selector(
            "tr[data-ticker='PETR4'] td[data-celula='diagnostico'] span", "el => el.innerText")
        estado_vale = pagina.eval_on_selector(
            "tr[data-ticker='VALE3'] td[data-celula='diagnostico'] span", "el => el.innerText")
        checar("papel que deteriorou vira Desconformidade",
               estado_petr.strip() == "Desconformidade", estado_petr)
        checar("papel só caro vira Atenção, não Desconformidade",
               estado_vale.strip() == "Atenção", estado_vale)
        estado_zzz = pagina.eval_on_selector(
            "tr[data-ticker='ZZZZ3'] td[data-celula='diagnostico'] span", "el => el.innerText")
        checar("papel fora dos registros vira Não apurado",
               estado_zzz.strip() == "Não apurado", estado_zzz)

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

        # -------- filosofia declarada --------
        # O motor tem 40 testes proprios; aqui o que se prova e que a escolha
        # existe na tela, faz a volta pelo servidor e some do caminho das
        # linhas que nao sao acao.
        checar("o seletor de filosofia aparece",
               pagina.is_visible("#opcoesFilosofia"))
        checar("as quatro opcoes de filosofia aparecem com resumo (3 teses + nenhuma)",
               pagina.eval_on_selector_all("#opcoesFilosofia button", "els => els.length") == 4)

        pagina.click("#opcoesFilosofia button >> nth=1")     # Bazin
        pagina.wait_for_timeout(900)
        checar("a filosofia escolhida fica marcada",
               "Bazin" in pagina.inner_text("#opcoesFilosofia"))

        pagina.reload(wait_until="networkidle")
        pagina.wait_for_timeout(1500)
        marcada = pagina.eval_on_selector_all(
            "#opcoesFilosofia button",
            "els => els.filter(e => e.style.background && e.style.background !== 'transparent')"
            ".map(e => e.innerText)")
        checar("a filosofia persiste entre recargas",
               any("Bazin" in t for t in marcada), marcada)

        # -------- "nenhuma": escolha deliberada, nao pendencia --------
        pagina.click("#opcoesFilosofia button >> nth=3")     # Nenhuma
        pagina.wait_for_timeout(900)
        checar("nenhuma fica marcada e o aviso de pendencia some",
               "Nenhuma" in pagina.inner_text("#opcoesFilosofia")
               and pagina.inner_text("#avisoFilosofia").strip() == "",
               (pagina.inner_text("#opcoesFilosofia"), pagina.inner_text("#avisoFilosofia")))
        pagina.wait_for_function(
            "() => { const el = document.querySelector(\"tr[data-ticker='PETR4'] "
            "td[data-celula='diagnostico'] span\"); return el && el.innerText.trim() === 'Sem filosofia'; }",
            timeout=10000)
        titulo_sem_filosofia = pagina.eval_on_selector(
            "tr[data-ticker='PETR4'] td[data-celula='diagnostico'] span", "el => el.title")
        checar("sem filosofia mostra dado bruto (ROE ou P/VP), nao veredito",
               "ROE" in titulo_sem_filosofia or "P/VP" in titulo_sem_filosofia,
               titulo_sem_filosofia)
        checar("sem filosofia nao usa linguagem de veredito",
               "aprovado" not in titulo_sem_filosofia.lower()
               and "reprovado" not in titulo_sem_filosofia.lower(),
               titulo_sem_filosofia)

        # Volta para Bazin: o resto do smoke (Pilar 3, bloqueio de aporte por
        # desconformidade) depende dela.
        pagina.click("#opcoesFilosofia button >> nth=1")     # Bazin
        pagina.wait_for_timeout(900)

        # A regua por papel so existe para acao.
        colunas = pagina.eval_on_selector_all(
            "#tabelaPosicoes tr", "els => els.map(e => e.children.length)")
        checar("toda linha tem o mesmo numero de colunas do cabecalho",
               len(set(colunas)) <= 1, colunas)
        reguas = pagina.eval_on_selector_all(
            "#tabelaPosicoes select", "els => els.length")
        checar("linha de acao tem seletor de regua", reguas >= 1, reguas)

        # -------- Pilar 3: rebalanceamento por aporte --------
        # O motor tem 42 testes proprios; o que so o browser prova e que o
        # painel existe, que o alvo faz a volta completa pelo servidor e que
        # a recusa por alvo ausente aparece NA TELA em vez de sumir.
        checar("painel de rebalanceamento existe",
               pagina.is_visible("#btnCalcularAporte"))

        pagina.fill("#alvoAcao", "60")
        pagina.fill("#alvoFii", "30")
        pagina.fill("#alvoEtf", "5")
        soma = pagina.inner_text("#somaAlvos")
        checar("a soma dos alvos aparece enquanto se digita", "95" in soma, soma)

        # Alvo que nao fecha 100 e recusado pelo servidor, com motivo na tela.
        pagina.click("#btnSalvarAlvos")
        pagina.wait_for_selector("#avisoAlvos:not(.hidden)", timeout=5000)
        texto = pagina.inner_text("#avisoAlvos")
        checar("alvo que nao soma 100 e recusado com motivo", "100" in texto, texto)

        # Sem alvo valido gravado, rebalancear tem que explicar em vez de
        # escolher uma alocacao por conta propria.
        pagina.fill("#valorAporte", "1000")
        pagina.click("#btnCalcularAporte")
        pagina.wait_for_selector("#avisoAporte:not(.hidden)", timeout=8000)
        texto = pagina.inner_text("#avisoAporte")
        checar("sem alvo definido a tela explica em vez de inventar alocacao",
               "alvo" in texto.lower(), texto)

        pagina.fill("#alvoEtf", "10")
        soma = pagina.inner_text("#somaAlvos")
        checar("soma fecha 100 depois da correcao", "100" in soma, soma)
        pagina.click("#btnSalvarAlvos")
        # Espera o TEXTO mudar, nao a visibilidade: o aviso ja estava na tela
        # desde a recusa anterior, entao esperar por :not(.hidden) volta na
        # hora e le a mensagem velha.
        pagina.wait_for_function(
            "() => document.getElementById('avisoAlvos').innerText.toLowerCase()"
            ".includes('salvo')", timeout=8000)
        checar("alvo valido e aceito", "salvo" in pagina.inner_text("#avisoAlvos").lower(),
               pagina.inner_text("#avisoAlvos"))

        # Recarrega: o alvo tem que voltar do servidor, nao do formulario.
        pagina.reload(wait_until="networkidle")
        pagina.wait_for_timeout(1200)
        checar("alvo persiste entre recargas",
               pagina.input_value("#alvoAcao") in ("60", "60.0"),
               pagina.input_value("#alvoAcao"))

        # -------- Pilar 4: estresse macro --------
        # Selic e barata (so SQLite) e carrega sozinha; eventos de cauda sao
        # caros (historico inteiro) e ficam atras de um botao.
        pagina.wait_for_selector("#painelEstresse:not(.hidden)", timeout=10000)
        pagina.wait_for_selector("tr[data-selic-ticker='PETR4']", timeout=8000)
        tabela_selic = pagina.inner_text("#tabelaSelic")
        checar("selic: PETR4 e VALE3 aparecem, ZZZZ3 (nao-acao) nao aparece",
               "PETR4" in tabela_selic and "VALE3" in tabela_selic
               and "ZZZZ3" not in tabela_selic, tabela_selic[:250])
        checar("selic meta aparece no rotulo",
               "%" in pagina.inner_text("#selicMetaLabel"),
               pagina.inner_text("#selicMetaLabel"))

        # VALE3 tem caixa liquido no balanco de teste: juro alto vira ganho,
        # independente da taxa simulada.
        estado_vale_selic = pagina.eval_on_selector(
            "tr[data-selic-ticker='VALE3'] td[data-celula='estado'] span", "el => el.innerText")
        checar("papel com caixa liquido fica Beneficiado por juro alto",
               estado_vale_selic.strip() == "Beneficiado", estado_vale_selic)

        # Arrasta o controle para uma Selic bem acima do ponto onde a divida
        # alavancada do PETR4 de teste deixa de fechar a conta (>~15,7%).
        pagina.eval_on_selector(
            "#controleSelic",
            "(el, v) => { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }",
            20)
        pagina.wait_for_function(
            "() => { const el = document.querySelector(\"tr[data-selic-ticker='PETR4'] "
            "td[data-celula='estado'] span\"); return el && el.innerText.trim() === 'Crítico'; }",
            timeout=10000)
        checar("subir a Selic simulada empurra papel alavancado para Critico", True)
        checar("o numero da Selic simulada acompanha o controle",
               "20" in pagina.inner_text("#selicSimulada"), pagina.inner_text("#selicSimulada"))

        # Eventos de cauda: sob pedido, por ser caro.
        pagina.click("#btnEstresseHistorico")
        pagina.wait_for_selector("[data-evento]", timeout=15000)
        eventos_texto = pagina.inner_text("#listaEventos")
        checar("os cinco eventos de cauda aparecem",
               all(nome in eventos_texto for nome in (
                   "Crise financeira de 2008", "Recessão brasileira de 2015-16",
                   "Joesley Day", "Pandemia de 2020", "Aperto monetário de 2021-22")),
               eventos_texto[:400])
        checar("evento mostra queda em porcentagem, nao em branco",
               "%" in eventos_texto, eventos_texto[:200])

        # -------- Pilar 5: radar de cinco eixos --------
        pagina.click("#btnRadar")
        pagina.wait_for_selector("#corpoRadar:not(.hidden)", timeout=15000)
        checar("media geral do radar e um numero, nao travessao",
               pagina.inner_text("#radarMedia").strip() not in ("", "—"),
               pagina.inner_text("#radarMedia"))
        resumo_radar = pagina.inner_text("#radarResumo")
        checar("resumo do radar cita quantas acoes e o eixo mais forte/fraco",
               "avaliada" in resumo_radar and "mais forte" in resumo_radar
               and "mais fraco" in resumo_radar, resumo_radar)

        svg_textos = pagina.eval_on_selector_all(
            "#svgRadar text", "els => els.map(e => e.textContent)")
        checar("os cinco eixos aparecem no SVG do radar",
               all(any(rotulo in txto for txto in svg_textos) for rotulo in
                   ("Valor", "Qualidade", "Proventos", "Momentum", "Segurança")),
               svg_textos)
        checar("radar desenhou o poligono de dados, nao so a grade",
               pagina.eval_on_selector_all("#svgRadar polygon", "els => els.length") >= 6,
               pagina.eval_on_selector_all("#svgRadar polygon", "els => els.length"))

        tabela_radar = pagina.inner_text("#tabelaRadarPapeis")
        checar("tabela do radar traz PETR4 e VALE3",
               "PETR4" in tabela_radar and "VALE3" in tabela_radar, tabela_radar[:250])

        # -------- Renda fixa: cadastro e marcação na curva --------
        # Painel independente da carteira de ação: existe mesmo antes de
        # cadastrar nada, e o pré-fixado não depende de rede nenhuma.
        checar("carteira vazia comeca com o estado vazio de renda fixa",
               pagina.is_visible("#estadoVazioRendaFixa"))

        pagina.fill("#rfEmissor", "Banco Exemplo")
        pagina.select_option("#rfTipo", "cdb")
        pagina.select_option("#rfIndexador", "pre")
        pagina.fill("#rfTaxa", "10")
        pagina.fill("#rfDataAplicacao", "2024-01-02")
        pagina.fill("#rfValorAplicado", "10000")
        pagina.click("#btnRendaFixaAdicionar")
        pagina.wait_for_timeout(700)

        tabela_rf = pagina.inner_text("#tabelaRendaFixa")
        checar("posicao pre-fixada aparece na tabela com tipo e indexador",
               "Banco Exemplo" in tabela_rf and "CDB" in tabela_rf and "Pré" in tabela_rf,
               tabela_rf)
        rentab_pre = pagina.eval_on_selector(
            "#tabelaRendaFixa tr td:nth-child(6)", "el => el.innerText")
        checar("pre-fixado composto desde 2024 mostra rentabilidade positiva",
               rentab_pre.startswith("+"), rentab_pre)
        resumo_rf = pagina.inner_text("#resumoRendaFixa")
        checar("resumo de renda fixa mostra aplicado e atual (curva)",
               "aplicado" in resumo_rf.lower() and "curva" in resumo_rf.lower(), resumo_rf)

        # Indexador ao CDI sem rede — o sandbox não alcança o BCB, a mesma
        # premissa já usada acima para o Yahoo. Prova que a marcação volta
        # "não apurado" em vez de inventar uma correção.
        pagina.fill("#rfEmissor", "Fundo CDI Exemplo")
        pagina.select_option("#rfTipo", "lci")
        pagina.select_option("#rfIndexador", "pct_cdi")
        pagina.fill("#rfTaxa", "100")
        pagina.fill("#rfDataAplicacao", "2024-01-02")
        pagina.fill("#rfValorAplicado", "5000")
        pagina.click("#btnRendaFixaAdicionar")
        pagina.wait_for_function(
            "() => (document.getElementById('tabelaRendaFixa').innerText || '')"
            ".toLowerCase().includes('não apurado')",
            timeout=15000)
        checar("indexador sem rede mostra nao apurado, nunca correcao inventada",
               "não apurado" in pagina.inner_text("#tabelaRendaFixa").lower(),
               pagina.inner_text("#tabelaRendaFixa"))

        # Limpa as duas posições de teste.
        for _ in range(2):
            pagina.click("#tabelaRendaFixa button:has-text('Remover')")
            pagina.wait_for_timeout(500)
        checar("remover as duas posicoes volta ao estado vazio",
               pagina.is_visible("#estadoVazioRendaFixa"))

        # -------- Perfil do investidor e objetivo --------
        # Campo simples, sem questionário: o que só o browser prova é que os
        # campos condicionais aparecem/somem com o objetivo e que o valor
        # volta preenchido depois de recarregar.
        checar("campos de renda passiva comecam escondidos",
               not pagina.is_visible("#camposRendaPassiva"))
        checar("campos de aposentadoria comecam escondidos",
               not pagina.is_visible("#camposAposentadoria"))

        pagina.select_option("#perfilInvestidor", "moderado")
        pagina.select_option("#objetivoCarteira", "aposentadoria")
        checar("selecionar aposentadoria mostra os campos de aposentadoria",
               pagina.is_visible("#camposAposentadoria"))
        checar("aposentadoria nao mostra os campos de renda passiva",
               not pagina.is_visible("#camposRendaPassiva"))

        pagina.fill("#horizonteAnos", "20")
        pagina.fill("#metaPatrimonio", "1500000")
        pagina.click("#btnSalvarPerfil")
        pagina.wait_for_timeout(600)
        checar("aviso confirma que o perfil foi salvo",
               "salvo" in pagina.inner_text("#avisoPerfil").lower(),
               pagina.inner_text("#avisoPerfil"))

        pagina.reload(wait_until="networkidle")
        pagina.wait_for_selector("#painelCarteira:not(.hidden)", timeout=8000)
        pagina.wait_for_function(
            "() => document.getElementById('perfilInvestidor').value === 'moderado'",
            timeout=8000)
        checar("perfil e objetivo persistem entre recargas",
               pagina.eval_on_selector("#objetivoCarteira", "el => el.value") == "aposentadoria"
               and pagina.eval_on_selector("#horizonteAnos", "el => el.value") == "20")
        checar("campo de aposentadoria reaparece ja marcado apos recarregar",
               pagina.is_visible("#camposAposentadoria"))

        # Trocar para renda passiva não pode deixar resto do horizonte antigo.
        pagina.select_option("#objetivoCarteira", "renda_passiva")
        pagina.fill("#metaRetiradaMensal", "6000")
        pagina.click("#btnSalvarPerfil")
        pagina.wait_for_timeout(600)
        pagina.reload(wait_until="networkidle")
        pagina.wait_for_selector("#painelCarteira:not(.hidden)", timeout=8000)
        pagina.wait_for_function(
            "() => document.getElementById('objetivoCarteira').value === 'renda_passiva'",
            timeout=8000)
        horizonte_depois = pagina.eval_on_selector("#horizonteAnos", "el => el.value")
        checar("trocar de objetivo nao deixa resto do horizonte anterior",
               horizonte_depois == "", horizonte_depois)

        # -------- Backtest de 12 meses --------
        # PETR4 e VALE3 têm histórico sintético desde 2007 (ver _FonteDeTeste
        # acima) — cobrem a janela inteira, então a carteira toda entra na
        # simulação (cobertura de 100%, sem sem_historico).
        pagina.click("#btnBacktest")
        pagina.wait_for_selector("#corpoBacktest:not(.hidden)", timeout=15000)
        retorno_texto = pagina.inner_text("#backtestRetorno")
        checar("retorno do backtest e um numero, nao travessao",
               retorno_texto.strip() not in ("", "—") and "%" in retorno_texto,
               retorno_texto)
        resumo_backtest = pagina.inner_text("#backtestResumo")
        checar("resumo do backtest cita posicoes e cobertura",
               "posição" in resumo_backtest and "cobertura" in resumo_backtest,
               resumo_backtest)
        checar("cobertura de 100% quando as duas acoes tem historico completo",
               "100%" in resumo_backtest, resumo_backtest)
        checar("backtest nao desenhou nenhuma posicao em sem_historico",
               pagina.inner_text("#listaSemHistoricoBacktest").strip() == "")
        pontos_svg = pagina.eval_on_selector_all(
            "#svgBacktest polyline", "els => els.length")
        checar("backtest desenhou a linha da serie, nao so a grade",
               pontos_svg >= 1, pontos_svg)
        origem_backtest = pagina.inner_text("#backtestOrigem")
        checar("rotulo do backtest deixa claro que e simulacao, nao cotacao",
               "Simulação" in origem_backtest and "Calculado" in origem_backtest,
               origem_backtest)

        # -------- Projeção de capital --------
        # O backtest acima ja preencheu a taxa sugerida; o perfil ficou com
        # objetivo "renda passiva" e meta de R$ 6.000/mes do bloco anterior —
        # prova que o gap usa o que ja esta cadastrado, sem repetir consulta.
        taxa_sugerida = pagina.eval_on_selector("#projTaxaAnual", "el => el.value")
        checar("taxa sugerida vem pre-preenchida com o retorno do backtest",
               taxa_sugerida != "", taxa_sugerida)

        pagina.fill("#projAporteMensal", "500")
        pagina.fill("#projHorizonteAnos", "10")
        pagina.click("#btnProjetar")
        pagina.wait_for_selector("#corpoProjecao:not(.hidden)", timeout=15000)

        aviso_hipotetico = pagina.inner_text("#projAvisoHipotetico")
        checar("aviso hipotetico aparece em destaque, nao em rodape",
               "hipotética" in aviso_hipotetico.lower(), aviso_hipotetico)
        checar("valor final da projecao nao fica em branco",
               pagina.inner_text("#projValorFinal").strip() not in ("", "—", "R$ —"),
               pagina.inner_text("#projValorFinal"))
        pontos_svg_proj = pagina.eval_on_selector_all(
            "#svgProjecao polyline", "els => els.length")
        checar("projecao desenhou a linha da serie",
               pontos_svg_proj >= 1, pontos_svg_proj)

        gaps_texto = pagina.inner_text("#listaGapsProjecao")
        checar("gap da meta de renda passiva aparece como falta/sobra, nunca binario",
               ("Faltam" in gaps_texto or "Sobram" in gaps_texto) and "R$" in gaps_texto,
               gaps_texto)
        checar("gap cita a meta cadastrada de renda passiva",
               "Renda passiva" in gaps_texto, gaps_texto)

        # Taxa/horizonte vazios: a tela recusa sem chamar o servidor.
        pagina.fill("#projTaxaAnual", "")
        pagina.click("#btnProjetar")
        pagina.wait_for_timeout(300)
        checar("taxa vazia e recusada na tela, com aviso",
               "informe" in pagina.inner_text("#avisoProjecao").lower(),
               pagina.inner_text("#avisoProjecao"))

        # -------- Fundos de investimento (Etapa A) --------
        # Sem cota diária ainda: prova que a tela nunca finge uma
        # rentabilidade, e que cotas x valor da cota vira o aplicado certo.
        checar("carteira nova comeca com o estado vazio de fundos",
               pagina.is_visible("#estadoVazioFundos"))

        pagina.fill("#fundoNome", "XP Multimercado FIC FIM")
        pagina.fill("#fundoCnpj", "12.345.678/0001-99")
        pagina.select_option("#fundoClasse", "multimercado")
        pagina.fill("#fundoCotas", "100")
        pagina.fill("#fundoValorCota", "150")
        pagina.fill("#fundoDataAplicacao", "2024-01-02")
        pagina.click("#btnFundoAdicionar")
        pagina.wait_for_timeout(700)

        tabela_fundos = pagina.inner_text("#tabelaFundos")
        checar("fundo cadastrado aparece na tabela com classe e CNPJ",
               "XP Multimercado" in tabela_fundos and "Multimercado" in tabela_fundos
               and "12.345.678/0001-99" in tabela_fundos, tabela_fundos)
        checar("aplicado bate com cotas vezes valor da cota (100 x 150 = 15.000)",
               "15.000,00" in tabela_fundos, tabela_fundos)
        checar("fundo aparece marcado como nao apurado (sem cota diaria ainda)",
               "não apurado" in tabela_fundos.lower(), tabela_fundos)
        resumo_fundos = pagina.inner_text("#resumoFundos")
        checar("resumo de fundos avisa que a cota diaria ainda nao foi coletada (sem CVM no sandbox)",
               "sem cota diária coletada" in resumo_fundos, resumo_fundos)

        pagina.click("#tabelaFundos button:has-text('Remover')")
        pagina.wait_for_timeout(500)
        checar("remover o fundo volta ao estado vazio",
               pagina.is_visible("#estadoVazioFundos"))

        # -------- Relatório em PDF --------
        # Tela prévia com checkbox por cenário (a escolha do usuário nas
        # perguntas de design do Task #72) — nunca "gerar com tudo" direto.
        # O bloco anterior esvaziou a taxa de propósito para testar a recusa
        # client-side; aqui a repõe, simulando o assessor que já projetou e
        # agora só quer o relatório com a mesma premissa ainda na tela.
        pagina.fill("#projTaxaAnual", "10")
        pagina.click("#btnAbrirRelatorio")
        pagina.wait_for_selector("#blocoRelatorioPrevia:not(.hidden)", timeout=8000)
        checkboxes_evento = pagina.eval_on_selector_all(
            ".relatorio-evento", "els => els.length")
        checar("tela previa lista os cinco cenarios de estresse",
               checkboxes_evento == 5, checkboxes_evento)
        checar("cenarios vem marcados por padrao (usuario desmarca, nao marca)",
               pagina.eval_on_selector_all(
                   ".relatorio-evento:checked", "els => els.length") == 5)
        # Taxa/aporte/horizonte já ficaram preenchidos pelo bloco de projeção
        # acima — a projeção deve vir habilitada e marcada, sem redigitar.
        checar("checkbox de projecao vem habilitado quando ja ha premissa preenchida",
               pagina.eval_on_selector("#relatorioIncluirProjecao", "el => !el.disabled"))
        checar("checkbox de projecao vem marcado por padrao nesse caso",
               pagina.eval_on_selector("#relatorioIncluirProjecao", "el => el.checked"))

        with pagina.expect_download(timeout=15000) as info_download:
            pagina.click("#btnGerarRelatorio")
        download = info_download.value
        caminho_pdf = os.path.join(tempfile.mkdtemp(), "relatorio-smoke.pdf")
        download.save_as(caminho_pdf)
        with open(caminho_pdf, "rb") as arq:
            conteudo_pdf = arq.read()
        checar("baixar relatorio devolve um PDF de verdade, nao um erro disfarcado",
               conteudo_pdf.startswith(b"%PDF"), conteudo_pdf[:20])
        checar("PDF gerado tem tamanho plausivel (varias secoes preenchidas)",
               len(conteudo_pdf) > 1000, len(conteudo_pdf))
        checar("tela previa fecha sozinha depois do download",
               pagina.is_hidden("#blocoRelatorioPrevia"))

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
