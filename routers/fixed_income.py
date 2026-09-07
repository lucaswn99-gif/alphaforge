import json
import os
import time
import requests
from fastapi import APIRouter, UploadFile, File
from pypdf import PdfReader
import io

router = APIRouter(prefix="/renda-fixa", tags=["Renda Fixa & Crédito"])

# ==========================================
# A chave NUNCA fica no código. Defina a variável de ambiente
# GEMINI_API_KEY antes de rodar a aplicação (ex: num .env carregado
# com python-dotenv, ou nas envs do serviço de deploy).
# ==========================================
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Modelo estável (lançado em jul/2026). Nota: nesta geração, os
# parâmetros temperature/top_p/top_k foram descontinuados e são
# ignorados pela API (não geram erro, só não fazem mais efeito).
GEMINI_MODEL = "gemini-3.6-flash"


def extrair_texto_pdf_nativo(conteudo_bytes: bytes) -> str:
    """
    Extrai texto do PDF usando pypdf, que decodifica corretamente streams
    comprimidos (FlateDecode), fontes com subset customizado e a tabela
    ToUnicode — casos que um regex manual não cobre de forma confiável.
    """
    try:
        leitor = PdfReader(io.BytesIO(conteudo_bytes))
        partes = [pagina.extract_text() or "" for pagina in leitor.pages]
        return "\n".join(partes)
    except Exception:
        return ""


def resposta_erro(nome_arquivo: str, classificacao: str, parecer: str) -> dict:
    """Monta o payload de erro padrão, evitando repetir o dict em cada branch."""
    return {
        "emissor": nome_arquivo.replace(".pdf", "").upper(),
        "alavancagem_dl_ebitda": 0.0,
        "cobertura_juros_icj": 0.0,
        "altman_z_score": 0.0,
        "classificacao_z": classificacao,
        "status": "ERRO" if classificacao != "Documento Ilegível" else "REPROVADO",
        "parecer": parecer,
    }


@router.post("/auditar-pdf")
async def auditar_pdf(file: UploadFile = File(...)):
    if not GEMINI_API_KEY:
        return resposta_erro(
            file.filename,
            "Configuração ausente",
            "GEMINI_API_KEY não configurada no ambiente do servidor.",
        )

    try:
        conteudo = await file.read()
        texto_extraido = extrair_texto_pdf_nativo(conteudo)

        if not texto_extraido or len(texto_extraido.strip()) < 40:
            return resposta_erro(
                file.filename,
                "Documento Ilegível",
                "Não foi possível ler texto selecionável do PDF. O documento pode ser uma digitalização/imagem.",
            )

        prompt = f"""
        Você é um auditor sênior de crédito privado.
        REGRA DE OURO: NÃO INVENTE NENHUM DADO. Se a informação não estiver no texto, retorne 0.0.
        Analise o texto contábil/financeiro abaixo e retorne um JSON válido no seguinte formato:
        {{
            "emissor": "Nome da empresa emissora (ou 'Desconhecido')",
            "alavancagem_dl_ebitda": 2.5,
            "cobertura_juros_icj": 3.8,
            "altman_z_score": 2.9,
            "classificacao_z": "Zona Segura",
            "status": "APROVADO",
            "parecer": "Resumo do risco de crédito em 2 frases."
        }}

        Texto:
        {texto_extraido[:10000]}
        """

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

        headers = {"Content-Type": "application/json"}
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                # Força o modelo a devolver JSON puro, sem crases nem texto extra.
                "response_mime_type": "application/json",
            },
        }

        # Retry com backoff exponencial para erros temporários do Gemini
        # (503 = sobrecarregado, 429 = limite de taxa). Máximo 3 tentativas.
        resp = None
        max_tentativas = 3
        for tentativa in range(max_tentativas):
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.status_code not in (503, 429):
                break
            if tentativa < max_tentativas - 1:
                time.sleep(2 ** tentativa)  # espera 1s, depois 2s

        if resp.status_code != 200:
            return resposta_erro(
                file.filename,
                "Falha na API",
                f"Comunicação com Gemini falhou (Cód: {resp.status_code}). Detalhe: {resp.text[:300]}",
            )

        corpo = resp.json()
        candidatos = corpo.get("candidates") or []

        if not candidatos:
            # Resposta bloqueada por segurança, recitação, etc.
            motivo = corpo.get("promptFeedback", {}).get("blockReason", "desconhecido")
            return resposta_erro(
                file.filename,
                "Resposta bloqueada",
                f"O Gemini não retornou candidatos. Motivo reportado: {motivo}.",
            )

        finish_reason = candidatos[0].get("finishReason")
        partes = candidatos[0].get("content", {}).get("parts", [])

        if not partes:
            return resposta_erro(
                file.filename,
                "Resposta vazia",
                f"Gemini retornou sem conteúdo utilizável (finishReason: {finish_reason}).",
            )

        resultado_bruto = partes[0].get("text", "")
        limpo = resultado_bruto.replace("```json", "").replace("```", "").strip()

        try:
            return json.loads(limpo)
        except json.JSONDecodeError:
            return resposta_erro(
                file.filename,
                "JSON inválido",
                f"O Gemini não retornou um JSON válido. Resposta bruta (truncada): {limpo[:300]}",
            )

    except Exception as e:
        return resposta_erro(file.filename, "Erro Interno", f"Erro interno ao processar PDF: {str(e)}")