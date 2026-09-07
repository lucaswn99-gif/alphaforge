import pandas as pd
import requests
import zipfile
import io
import sqlite3
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def baixar_dados_cvm(ano="2025"):
    print(f"[*] Conectando ao Portal de Dados Abertos da CVM (Ano Base: {ano})...")
    url = f"https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
    
    r = requests.get(url, stream=True, verify=False)
    if r.status_code != 200:
        print("[!] Erro ao baixar dados da CVM. O site pode estar instável.")
        return
        
    print("[*] Arquivo ZIP baixado com sucesso. Extraindo planilhas em memória...")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    
    # Lendo Balanço Patrimonial Ativo (BPA), Passivo (BPP) e DRE consolidado
    bpa = pd.read_csv(z.open(f'dfp_cia_aberta_BPA_con_{ano}.csv'), sep=';', encoding='iso-8859-1')
    bpp = pd.read_csv(z.open(f'dfp_cia_aberta_BPP_con_{ano}.csv'), sep=';', encoding='iso-8859-1')
    dre = pd.read_csv(z.open(f'dfp_cia_aberta_DRE_con_{ano}.csv'), sep=';', encoding='iso-8859-1')

    print("[*] Processando as demonstrações financeiras...")
    # Juntando tudo e pegando só os dados do fechamento do ano
    df = pd.concat([bpa, bpp, dre])
    df = df[df['ORDEM_EXERC'] == 'ÚLTIMO']
    
    # A CVM organiza os números por códigos (CD_CONTA)
    # 1: Ativo Total | 1.01: Ativo Circulante | 2: Passivo Total 
    # 2.01: Passivo Circulante | 2.03: Patr. Líquido | 3.05: EBIT (Proxy de EBITDA) | 3.06: Despesa Financeira
    contas_alvo = ['1', '1.01', '2', '2.01', '2.03', '3.05', '3.06']
    df_filtro = df[df['CD_CONTA'].isin(contas_alvo)]
    
    # Cruzando as contas para formar uma tabela limpa
    tabela_final = df_filtro.pivot_table(
        index=['CNPJ_CIA', 'DENOM_CIA'], 
        columns='CD_CONTA', 
        values='VL_CONTA', 
        aggfunc='last'
    ).reset_index()
    
    # Renomeando as colunas para o padrão do nosso motor de crédito
    tabela_final.rename(columns={
        '1': 'ativo_total',
        '1.01': 'ativo_circulante',
        '2': 'passivo_total',
        '2.01': 'passivo_circulante',
        '2.03': 'patrimonio_liquido',
        '3.05': 'ebitda',
        '3.06': 'despesa_financeira_anual'
    }, inplace=True)
    
    tabela_final.fillna(0, inplace=True)
    
    # Cálculo de Dívida (Aproximação conservadora)
    tabela_final['divida_total'] = tabela_final['passivo_total'] - tabela_final['patrimonio_liquido']
    tabela_final['divida_liquida'] = tabela_final['divida_total'] - (tabela_final['ativo_circulante'] * 0.2)
    
    # Salvando no banco de dados SQLite
    print("[*] Salvando os dados no banco local (dados_mercado.db)...")
    conn = sqlite3.connect('dados_mercado.db')
    tabela_final.to_sql('cvm_balancos', conn, if_exists='replace', index=False)
    conn.close()
    
    print(f"[OK] Sucesso! Banco de dados atualizado com o balanço de {len(tabela_final)} emissores (Abertos e Fechados).")

if __name__ == "__main__":
    # Puxamos 2025 pois é o último ano completo garantido na base DFP consolidada anual
    baixar_dados_cvm("2025")