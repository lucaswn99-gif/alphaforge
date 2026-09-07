import pandas as pd
import sqlite3
import requests
import zipfile
import io
import os

def formatar_valor(val):
    """Transforma o valor da CVM (em milhares) em formato Legível (Milhões ou Bilhões)"""
    if pd.isna(val): return "-"
    val = val * 1000  # Os dados da CVM vêm cortados em milhares
    if abs(val) >= 1_000_000_000:
        return f"R$ {val/1_000_000_000:.2f} Bi"
    else:
        return f"R$ {val/1_000_000:.2f} Mi"

def atualizar_base_cvm():
    print("Iniciando download da base oficial da CVM (Dados Abertos)...")
    anos = [2021, 2022, 2023, 2024]
    df_completo = pd.DataFrame()

    for ano in anos:
        print(f"[{ano}] Baixando e processando DRE Consolidada...")
        url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
        
        try:
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content))
                nome_arquivo = f"dfp_cia_aberta_DRE_con_{ano}.csv"
                
                if nome_arquivo in z.namelist():
                    # Lê o CSV direto da memória (sem salvar o arquivo gigante no disco)
                    df_dre = pd.read_csv(z.open(nome_arquivo), sep=';', encoding='iso-8859-1')
                    
                    # Filtra apenas o fechamento do ano atual e as contas principais
                    df_dre = df_dre[df_dre['ORDEM_EXERC'] == 'ÚLTIMO']
                    
                    # Códigos CVM: 3.01 (Receita), 3.05 (EBIT/Resultado antes trib), 3.11 (Lucro Líquido)
                    df_filtrado = df_dre[df_dre['CD_CONTA'].isin(['3.01', '3.05', '3.11'])].copy()
                    
                    # Pivota para ter 1 linha por empresa
                    df_pivot = df_filtrado.pivot_table(
                        index=['DENOM_CIA'], columns='CD_CONTA', values='VL_CONTA', aggfunc='sum'
                    ).reset_index()
                    
                    df_pivot['ano'] = ano
                    df_completo = pd.concat([df_completo, df_pivot])
        except Exception as e:
            print(f"Erro ao baixar ano {ano}: {e}")

    if not df_completo.empty:
        # Renomeia as colunas oficiais
        df_completo = df_completo.rename(columns={'3.01': 'receita_liquida', '3.05': 'ebitda', '3.11': 'lucro_liquido'})
        
        # Formata o dinheiro
        for col in ['receita_liquida', 'ebitda', 'lucro_liquido']:
            if col in df_completo.columns:
                df_completo[col] = df_completo[col].apply(formatar_valor)
            else:
                df_completo[col] = "-"

        # Cria ou substitui o banco SQLite
        print("Salvando dados reais no arquivo cvm_dados.db...")
        conn = sqlite3.connect("cvm_dados.db")
        df_final = df_completo[['DENOM_CIA', 'ano', 'receita_liquida', 'ebitda', 'lucro_liquido']]
        df_final.to_sql('demonstracoes', conn, if_exists='replace', index=False)
        conn.close()
        print("✅ Base CVM Atualizada com Sucesso! Terminal pronto para uso.")
    else:
        print("Falha ao obter os dados.")

if __name__ == "__main__":
    atualizar_base_cvm()