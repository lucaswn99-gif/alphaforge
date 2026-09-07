import yfinance as yf
import sqlite3
def calcular_altman_z_score_emergente(ativo_circulante: float, passivo_circulante: float,
                                      ativo_total: float, lucros_retidos: float,
                                      ebitda: float, patrimonio_liquido: float,
                                      passivo_total: float) -> dict:
    capital_giro = ativo_circulante - passivo_circulante
    
    x1 = capital_giro / ativo_total if ativo_total > 0 else 0
    x2 = lucros_retidos / ativo_total if ativo_total > 0 else 0
    x3 = ebitda / ativo_total if ativo_total > 0 else 0
    x4 = patrimonio_liquido / passivo_total if passivo_total > 0 else 0
    
    z_score = round(6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4, 2)
    
    if z_score < 1.10:
        classificacao = "Zona de Estresse / Alto Risco de Insolvência"
    elif 1.10 <= z_score <= 2.60:
        classificacao = "Zona Cinzenta / Alerta de Alavancagem"
    else:
        classificacao = "Zona Segura / Baixo Risco de Falência"
        
    return {"z_score": z_score, "classificacao": classificacao}

def auditar_credito_corporativo(nome_emissor: str, divida_liquida: float, 
                                ebitda: float, despesa_financeira_anual: float, 
                                z_metrics: dict) -> dict:
    alavancagem = round(divida_liquida / ebitda, 2) if ebitda > 0 else 999.0
    icj = round(ebitda / despesa_financeira_anual, 2) if despesa_financeira_anual > 0 else 0.0
    
    z_info = calcular_altman_z_score_emergente(**z_metrics)
    
    vetos = []
    if alavancagem > 3.5:
        vetos.append(f"Dívida Líquida/EBITDA ({alavancagem}x) acima do teto de 3.5x.")
    if icj < 1.5:
        vetos.append(f"Cobertura de Juros insuficiente ({icj}x): geração operacional não suporta juros da dívida.")
    if z_info["z_score"] < 1.10:
        vetos.append(f"Altman Z-Score crítico ({z_info['z_score']}): risco de insolvência elevado.")
        
    status = "REPROVADO / VETO" if len(vetos) > 0 else "APROVADO / ALTA CONFIANÇA"
    
    return {
        "emissor": nome_emissor,
        "tipo_ativo": "Credito Corporativo",
        "status": status,
        "alavancagem_dl_ebitda": alavancagem,
        "cobertura_juros_icj": icj,
        "altman_z_score": z_info["z_score"],
        "classificacao_z": z_info["classificacao"],
        "motivos_veto": vetos
    }

def auditar_ativo_bancario(nome_banco: str, indice_basileia: float, 
                           indice_imobilizacao: float, lucros_ultimos_3_anos: bool) -> dict:
    vetos = []
    if indice_basileia < 11.0:
        vetos.append(f"Índice de Basileia ({indice_basileia}%) abaixo do patamar prudencial de 11%.")
    if indice_imobilizacao > 50.0:
        vetos.append(f"Índice de Imobilização elevado ({indice_imobilizacao}%): risco de liquidez patrimonial.")
    if not lucros_ultimos_3_anos:
        vetos.append("Instituição financeira com prejuízos recorrentes nos últimos balanços.")
        
    status = "REPROVADO / VETO" if len(vetos) > 0 else "APROVADO / SEGURO"
    
    return {
        "instituicao": nome_banco,
        "tipo_ativo": "Ativo Bancario (CDB/LCI/LCA)",
        "status": status,
        "indice_basileia": f"{indice_basileia}%",
        "indice_imobilizacao": f"{indice_imobilizacao}%",
        "historico_lucratividade": "Consistente" if lucros_ultimos_3_anos else "Prejuízos Recorrentes",
        "motivos_veto": vetos
    }

def auditar_ticker_b3(ticker_input: str) -> dict:
    """
    Busca automaticamente os dados de balanço na B3/Yahoo e gera o laudo.
    """
    try:
        ticker_sa = f"{ticker_input}.SA" if not ticker_input.endswith(".SA") else ticker_input
        t = yf.Ticker(ticker_sa)
        info = t.info
        
        if not info or ('regularMarketPrice' not in info and 'previousClose' not in info):
            return {"erro": f"Dados de balanço indisponíveis para {ticker_input}."}

        # Extração Rápida (Info)
        ebitda = info.get('ebitda', 1) 
        divida_total = info.get('totalDebt', 0)
        caixa = info.get('totalCash', 0)
        divida_liquida = divida_total - caixa
        
        # Extração Profunda (Balanço Patrimonial DFP/ITR)
        bs = t.balance_sheet
        fin = t.financials
        
        def safe_get(df, index_name, default):
            try: return float(df.loc[index_name].dropna().iloc[0])
            except: return float(default)

        ativo_total = safe_get(bs, 'Total Assets', info.get('totalAssets', 1))
        passivo_total = safe_get(bs, 'Total Liabilities Net Minority Interest', divida_total)
        ativo_circulante = safe_get(bs, 'Current Assets', ativo_total * 0.4)
        passivo_circulante = safe_get(bs, 'Current Liabilities', passivo_total * 0.4)
        patrimonio_liquido = safe_get(bs, 'Stockholders Equity', 1)
        lucros_retidos = safe_get(bs, 'Retained Earnings', 0)
        
        despesa_financeira = safe_get(fin, 'Interest Expense', ebitda * 0.15)
        despesa_financeira = abs(despesa_financeira) if despesa_financeira else 1.0

        z_metrics = {
            "ativo_circulante": ativo_circulante,
            "passivo_circulante": passivo_circulante,
            "ativo_total": ativo_total,
            "lucros_retidos": lucros_retidos,
            "ebitda": ebitda,
            "patrimonio_liquido": patrimonio_liquido,
            "passivo_total": passivo_total
        }

        nome_emissor = info.get('shortName', ticker_input)

        resultado = auditar_credito_corporativo(
            nome_emissor=nome_emissor,
            divida_liquida=divida_liquida,
            ebitda=ebitda,
            despesa_financeira_anual=despesa_financeira,
            z_metrics=z_metrics
        )
        
        resultado["origem_dados"] = "Balanço DFP/ITR Automático (B3/Yahoo)"
        resultado["ticker_analisado"] = ticker_sa
        return resultado

    except Exception as e:
        return {"erro": f"Falha ao raspar balanço automático: {str(e)}"}
def auditar_empresa_cvm(busca: str) -> dict:
    """Busca a empresa pelo CNPJ ou Nome no banco CVM local e faz a auditoria com ajustes de Proxy."""
    try:
        conn = sqlite3.connect('dados_mercado.db')
        cursor = conn.cursor()
        
        query = f"%{busca.upper()}%"
        cursor.execute("""
            SELECT CNPJ_CIA, DENOM_CIA, ativo_total, ativo_circulante, 
                   passivo_total, passivo_circulante, patrimonio_liquido, 
                   ebitda, despesa_financeira_anual, divida_liquida 
            FROM cvm_balancos 
            WHERE CNPJ_CIA LIKE ? OR DENOM_CIA LIKE ? LIMIT 1
        """, (query, query))
        
        resultado = cursor.fetchone()
        conn.close()
        
        if not resultado:
            return {"erro": f"Empresa '{busca}' não encontrada no banco CVM local. Verifique o CNPJ ou Nome."}
            
        cnpj, nome, ativo_total, ativo_circ, passivo_total, passivo_circ, pat_liq, ebitda, desp_fin, div_liq = resultado
        
        # ==========================================
        # AJUSTES DE PROXY CVM (TRATAMENTO DE DADOS)
        # ==========================================
        # 1. A CVM traz o EBIT. Adicionamos ~40% como proxy conservadora de Depreciação/Amortização.
        ebitda_corrigido = float(ebitda) * 1.4 if float(ebitda) > 0 else float(ebitda)
        
        # 2. O Resultado Financeiro na CVM vem negativo. Usamos abs() para torná-lo positivo para a fórmula.
        desp_fin_corrigida = abs(float(desp_fin)) if float(desp_fin) != 0 else 1.0
        
        z_metrics = {
            "ativo_circulante": float(ativo_circ),
            "passivo_circulante": float(passivo_circ),
            "ativo_total": float(ativo_total) if float(ativo_total) > 0 else 1,
            "lucros_retidos": 0.0, # Aproximação para DFP
            "ebitda": ebitda_corrigido,
            "patrimonio_liquido": float(pat_liq),
            "passivo_total": float(passivo_total) if float(passivo_total) > 0 else 1
        }
        
        auditoria = auditar_credito_corporativo(
            nome_emissor=f"{nome} (CNPJ: {cnpj})",
            divida_liquida=float(div_liq),
            ebitda=ebitda_corrigido,
            despesa_financeira_anual=desp_fin_corrigida,
            z_metrics=z_metrics
        )
        
        auditoria["origem_dados"] = "Banco de Dados CVM Local (Proxy Ajustada p/ D&A)"
        return auditoria
        
    except Exception as e:
        return {"erro": f"Falha ao consultar banco CVM local: {str(e)}"}

    except Exception as e:
        return {"erro": f"Falha ao consultar banco CVM local: {str(e)}"}