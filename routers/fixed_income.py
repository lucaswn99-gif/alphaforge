"""Renda Fixa & Crédito: auditoria de emissor a partir de PDF ou de ticker.

Divisão de trabalho, e é o ponto do módulo:

    Gemini -> EXTRAI campos brutos do balanço que estão no PDF
    motor  -> CALCULA alavancagem, cobertura de juros e Altman Z

A versão anterior pedia os próprios índices ao modelo e usava a resposta para
carimbar APROVADO/REPROVADO. Índice de crédito produzido por LLM não é
reproduzível, não é auditável, e "não invente" no prompt não muda isso. Aqui o
modelo só faz o que modelo faz bem — ler documento e localizar número — e todo
cálculo passa por `modules/credit_engine.py`.

Campo que o PDF não traz volta como null e o laudo sai INCONCLUSIVO, em vez de
virar 0.0 e ganhar uma classificação.
"""

import io
import json
import os
import time

import requests
from fastapi import APIRouter, File, Query, UploadFile
from pypdf import PdfReader

from modules import credit_engine, credito_cvm

router = APIRouter(prefix="/renda-fixa", tags=["Renda Fixa & Crédito"])

# A chave nunca fica no código: defina GEMINI_API_KEY no ambiente (.env local,
# ou variável de ambiente do serviço no deploy).
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
GEMINI_TIMEOUT = 45
GEMINI_TENTATIVAS = 3

LIMITE_TEXTO = 30000  # caracteres enviados ao modelo
MINIMO_TEXTO_UTIL = 40

# Multiplicador por unidade declarada no balanço. Os índices são razões entre
# grandezas da mesma unidade, então isso não muda os índices — serve para os
# valores absolutos que aparecem no laudo não saírem mil vezes menores.
MULTIPLICADOR_UNIDADE = {
    "unidades": 1.0,
    "milhares": 1_000.0,
    "milhoes": 1_000_000.0,
    "milhões": 1_000_000.0,
    "bilhoes": 1_000_000_000.0,
    "bilhões": 1_000_000_000.0,
}

CAMPOS_NUMERICOS = (
    "ativo_total", "ativo_circulante", "passivo_total", "passivo_circulante",
    "patrimonio_liquido", "lucros_retidos", "ebitda", "ebit",
    "divida_bruta", "caixa_e_equivalentes", "despesa_financeira",
    "lucro_liquido", "receita_liquida",
)

PROMPT_EXTRACAO = """Você é um extrator de dados de demonstrações financeiras.

Sua ÚNICA tarefa é localizar valores no texto abaixo e transcrevê-los. Você não
calcula índices, não classifica risco e não emite opinião.

Regras:
- Transcreva o número como aparece no documento, sem converter unidade.
- Se um campo não estiver explicitamente no texto, retorne null. Nunca estime,
  nunca derive de outro campo, nunca use conhecimento externo sobre a empresa.
- "unidade" descreve a escala em que o balanço está publicado.
- Despesa financeira pode vir negativa; transcreva com o sinal do documento.

Responda SOMENTE com este JSON:
{
  "emissor": "razão social como aparece no documento, ou null",
  "periodo": "exercício/competência do balanço, ou null",
  "unidade": "unidades | milhares | milhoes | bilhoes",
  "ativo_total": null,
  "ativo_circulante": null,
  "passivo_total": null,
  "passivo_circulante": null,
  "patrimonio_liquido": null,
  "lucros_retidos": null,
  "ebitda": null,
  "ebit": null,
  "divida_bruta": null,
  "caixa_e_equivalentes": null,
  "despesa_financeira": null,
  "lucro_liquido": null,
  "receita_liquida": null,
  "observacao": "o que não foi encontrado, em uma frase, ou null"
}

Texto do documento:
"""


def extrair_texto_pdf(conteudo_bytes):
    """Texto selecionável do PDF. PDF digitalizado devolve string vazia."""
    try:
        leitor = PdfReader(io.BytesIO(conteudo_bytes))
        return "\n".join(pagina.extract_text() or "" for pagina in leitor.pages)
    except Exception:  # noqa: BLE001
        return ""


def laudo_indisponivel(emissor, motivo, detalhe=None):
    """Payload mantido compatível com o painel, mas sem número inventado:
    índice que não pôde ser calculado vai como null, não como 0.0."""
    return {
        "emissor": emissor,
        "status": "INCONCLUSIVO / DADO INSUFICIENTE",
        "alavancagem_dl_ebitda": None,
        "cobertura_juros_icj": None,
        "altman_z_score": None,
        "classificacao_z": motivo,
        "motivos_veto": [],
        "indices_indisponiveis": ["alavancagem", "cobertura de juros", "Altman Z-Score"],
        "parecer": detalhe or motivo,
        "origem_dados": "n/d",
    }


def _chamar_gemini(prompt):
    """Devolve (json_extraido, erro). Nunca levanta."""
    if not GEMINI_API_KEY:
        return None, "GEMINI_API_KEY não configurada no ambiente do servidor."

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    resposta = None
    for tentativa in range(GEMINI_TENTATIVAS):
        try:
            resposta = requests.post(url, headers={"Content-Type": "application/json"},
                                     json=payload, timeout=GEMINI_TIMEOUT)
        except requests.RequestException as exc:
            if tentativa == GEMINI_TENTATIVAS - 1:
                return None, f"Falha de rede ao chamar o Gemini: {type(exc).__name__}"
            time.sleep(2 ** tentativa)
            continue
        # 503 sobrecarregado, 429 limite de taxa: vale reesperar.
        if resposta.status_code not in (503, 429):
            break
        if tentativa < GEMINI_TENTATIVAS - 1:
            time.sleep(2 ** tentativa)

    if resposta is None:
        return None, "Sem resposta do Gemini."
    if resposta.status_code != 200:
        return None, f"Gemini respondeu {resposta.status_code}: {resposta.text[:300]}"

    try:
        corpo = resposta.json()
    except ValueError:
        return None, "Gemini devolveu corpo não-JSON."

    candidatos = corpo.get("candidates") or []
    if not candidatos:
        motivo = (corpo.get("promptFeedback") or {}).get("blockReason", "desconhecido")
        return None, f"Gemini não retornou candidatos (motivo: {motivo})."

    partes = (candidatos[0].get("content") or {}).get("parts") or []
    if not partes:
        return None, f"Gemini retornou vazio (finishReason: {candidatos[0].get('finishReason')})."

    bruto = (partes[0].get("text") or "").replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(bruto), None
    except json.JSONDecodeError:
        return None, f"Gemini não devolveu JSON válido. Trecho: {bruto[:300]}"


def normalizar_extracao(bruto):
    """Aplica a escala declarada e converte tudo para float ou None."""
    unidade = str(bruto.get("unidade") or "unidades").strip().lower()
    fator = MULTIPLICADOR_UNIDADE.get(unidade, 1.0)

    campos = {}
    for chave in CAMPOS_NUMERICOS:
        valor = credit_engine._num(bruto.get(chave))
        campos[chave] = valor * fator if valor is not None else None

    # EBITDA ausente e EBIT presente: usamos o EBIT e dizemos que foi isso.
    # A versão antiga do coletor da CVM inflava o EBIT em 40% como "proxy de
    # D&A" — um chute que entra no Z-Score como se fosse medido.
    proxy_ebit = False
    if campos["ebitda"] is None and campos["ebit"] is not None:
        campos["ebitda"] = campos["ebit"]
        proxy_ebit = True

    divida_liquida = None
    if campos["divida_bruta"] is not None:
        caixa = campos["caixa_e_equivalentes"] or 0.0
        divida_liquida = campos["divida_bruta"] - caixa

    return campos, {
        "unidade_declarada": unidade,
        "fator_aplicado": fator,
        "ebitda_e_na_verdade_ebit": proxy_ebit,
        "divida_liquida": divida_liquida,
    }


@router.post("/auditar-pdf")
async def auditar_pdf(file: UploadFile = File(...)):
    nome = (file.filename or "documento").replace(".pdf", "").upper()

    try:
        conteudo = await file.read()
    except Exception as exc:  # noqa: BLE001
        return laudo_indisponivel(nome, "Falha na leitura", f"Não foi possível ler o arquivo: {exc}")

    texto = extrair_texto_pdf(conteudo)
    if not texto or len(texto.strip()) < MINIMO_TEXTO_UTIL:
        return laudo_indisponivel(
            nome, "Documento ilegível",
            "Não há texto selecionável no PDF. O documento parece ser digitalização ou imagem — "
            "seria preciso OCR, que não está habilitado aqui.",
        )

    extraido, erro = _chamar_gemini(PROMPT_EXTRACAO + texto[:LIMITE_TEXTO])
    if erro:
        return laudo_indisponivel(nome, "Extração indisponível", erro)
    if not isinstance(extraido, dict):
        return laudo_indisponivel(nome, "Extração inválida", "O extrator não devolveu um objeto.")

    campos, meta = normalizar_extracao(extraido)

    laudo = credit_engine.auditar_credito_corporativo(
        nome_emissor=extraido.get("emissor") or nome,
        divida_liquida=meta["divida_liquida"],
        ebitda=campos["ebitda"],
        despesa_financeira_anual=campos["despesa_financeira"],
        z_metrics={
            "ativo_circulante": campos["ativo_circulante"],
            "passivo_circulante": campos["passivo_circulante"],
            "ativo_total": campos["ativo_total"],
            "lucros_retidos": campos["lucros_retidos"],
            "ebitda": campos["ebitda"],
            "patrimonio_liquido": campos["patrimonio_liquido"],
            "passivo_total": campos["passivo_total"],
        },
    )

    laudo["origem_dados"] = f"PDF enviado - campos extraídos por {GEMINI_MODEL}, índices calculados localmente"
    laudo["periodo"] = extraido.get("periodo")
    laudo["campos_extraidos"] = campos
    laudo["extracao"] = meta
    laudo["observacao_extrator"] = extraido.get("observacao")
    laudo["parecer"] = montar_parecer(laudo, meta)
    return laudo


def montar_parecer(laudo, meta):
    """Parecer descritivo montado a partir do que foi calculado — não é texto
    gerado por modelo, para o laudo não afirmar mais do que os números dizem."""
    partes = []
    if laudo["status"].startswith("REPROVADO"):
        partes.append("Emissor reprovado pelos critérios de crédito.")
    elif laudo["status"].startswith("APROVADO"):
        partes.append("Emissor dentro dos critérios de crédito.")
    else:
        partes.append("Laudo inconclusivo: faltam dados no documento para fechar os índices.")

    if laudo.get("alavancagem_dl_ebitda") is not None:
        partes.append(f"Dívida líquida/EBITDA em {laudo['alavancagem_dl_ebitda']}x.")
    if laudo.get("cobertura_juros_icj") is not None:
        partes.append(f"Cobertura de juros em {laudo['cobertura_juros_icj']}x.")
    if laudo.get("altman_z_score") is not None:
        partes.append(f"Altman Z de {laudo['altman_z_score']} ({laudo['classificacao_z']}).")

    if laudo.get("campos_faltantes"):
        partes.append("Não localizados no documento: " + ", ".join(laudo["campos_faltantes"]) + ".")
    if meta.get("ebitda_e_na_verdade_ebit"):
        partes.append("EBITDA não constava; foi usado o EBIT, sem ajuste de depreciação.")
    if laudo.get("lucros_retidos_assumidos_zero"):
        partes.append("Lucros retidos não constavam e entraram como zero no Z-Score.")

    return " ".join(partes)


@router.get("/auditar-ticker")
def auditar_ticker(ticker: str = Query(..., description="Código na B3, ex.: VALE3")):
    """Laudo de emissor listado, sem chave de API e sem PDF.

    Ordem das fontes, e por quê:

      1. Balanço entregue à CVM (DFP). É auditado, é o mesmo número dos dois
         lados da tela e responde de qualquer IP — no Render é o único que
         responde.
      2. Balanço do Yahoo, só se a base da CVM não cobrir a companhia. O
         `balance_sheet` sai por endpoint diferente do `quoteSummary` e às
         vezes sobrevive; quando não sobrevive, o laudo sai INCONCLUSIVO em
         vez de sair errado.

    O Gemini nunca calculou índice aqui — ele extraía campo de PDF. Com o campo
    já estruturado na DFP, não há o que extrair, e o laudo deixa de depender de
    chave nenhuma.
    """
    laudo = credito_cvm.laudo_por_ticker(ticker)
    if laudo.get("veredito") != "INCONCLUSIVO" or laudo.get("erro"):
        return laudo

    reserva = credit_engine.auditar_ticker_b3(ticker)
    reserva["veredito"] = credito_cvm.veredito_do_status(reserva.get("status"))
    if reserva.get("erro") or reserva["veredito"] == "INCONCLUSIVO":
        # A CVM é a fonte de referência: se as duas falharem, o motivo que
        # interessa reportar é o dela.
        return {**laudo, "tentativa_reserva": reserva.get("erro") or "Yahoo também inconclusivo"}
    reserva["origem_dados"] = "Balanço publicado (Yahoo) — companhia fora da base da CVM"
    reserva["identidade"] = laudo.get("identidade")
    return reserva
