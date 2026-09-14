"""Constrói a base de fundamentos a partir da DFP e do ITR da CVM.

    python atualizar_fundamentos_cvm.py            # DFP + ITR + FRE
    python atualizar_fundamentos_cvm.py 2024 2025  # esses anos, nas três fontes
    python atualizar_fundamentos_cvm.py --so-itr   # só o balanço trimestral
    python atualizar_fundamentos_cvm.py --so-acoes   # só a quantidade de ações
    python atualizar_fundamentos_cvm.py --sem-itr --sem-acoes   # como era antes

Três fontes, três tabelas, por um motivo:

* `fundamentos`, da DFP, é anual e fechada. É o que sustenta pergunta que só
  existe em exercício encerrado — lucro em todos os anos, crescimento de lucro,
  constância de provento.
* `balanco_itr`, do ITR, é o saldo patrimonial trimestral. É o que mantém o
  P/VP atual: sem ele o patrimônio usado no cálculo pode estar até quinze meses
  atrás do preço com que é dividido, o que faz o múltiplo divergir de qualquer
  fonte que acompanhe o trimestre.
* `acoes_cia`, do FRE, é a quantidade de ações declarada pela companhia. É o
  denominador do VPA. Sem ela a conta dependia de lucro/LPA, que some quando a
  companhia não publica LPA — e aí não havia P/VP nenhum.

A DRE do ITR fica de fora: ela é acumulada no ano, e casá-la com a DRE anual
exige reconstruir 12 meses móveis. Enquanto isso não for feito, lucro, receita
e EBIT — e portanto P/L, ROE e margem — continuam vindo só da DFP.

Por que existe: o Yahoo recusa o endpoint de múltiplos (`quoteSummary`) quando
a chamada vem de IP de datacenter — no Render, 0 de 100 papéis retornaram
fundamento. A CVM é fonte oficial, gratuita e não bloqueia servidor. E o número
passa a vir de balanço auditado em vez de um blob agregado.

Este script é para rodar FORA do serviço web (na sua máquina, ou como job
agendado). Ele grava `fundamentos_cvm.db`, que é lido pela API. Rodar uma vez
por trimestre, algumas semanas depois do fim do trimestre, pega o ITR novo.

Duas coisas que a versão anterior do coletor errava e aqui são tratadas:

* ESCALA_MOEDA. A CVM publica valores em MIL ou UNIDADE conforme a empresa.
  Ignorar isso mistura companhias em escalas diferentes na mesma tabela — e
  quebra qualquer conta que cruze com preço, como o P/L.
* "Passivo total". A conta 2 da CVM é o total do passivo E do patrimônio (bate
  com o ativo total). O passivo de terceiros, que é o que entra no Altman, é
  2.01 + 2.02.
"""

import io
import sqlite3
import sys
import zipfile
from collections import defaultdict

import pandas as pd
import requests

URL_DFP = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip"
URL_ITR = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/ITR/DADOS/itr_cia_aberta_{ano}.zip"
URL_FRE = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_{ano}.zip"
BANCO = "fundamentos_cvm.db"
TIMEOUT = 180
TAMANHO_BLOCO = 200_000  # linhas por chunk: a DFP passa de 1 milhão

COLUNAS = ["CNPJ_CIA", "DENOM_CIA", "DT_FIM_EXERC", "ORDEM_EXERC",
           "ESCALA_MOEDA", "CD_CONTA", "DS_CONTA", "VL_CONTA"]

# Conta da CVM -> campo nosso. Só o que o motor de score consome.
CONTAS = {
    "BPA": {
        "1": "ativo_total",
        "1.01": "ativo_circulante",
        "1.01.01": "caixa",
    },
    "BPP": {
        "2.01": "passivo_circulante",
        "2.02": "passivo_nao_circulante",
        "2.03": "patrimonio_liquido",
        # Dívida onerosa. Mapeada SÓ por código, de propósito: a descrição
        # "Empréstimos e Financiamentos" aparece nas duas contas, e registrar
        # a descrição faria as duas caírem no mesmo campo — a de curto prazo
        # sobrescreveria a de longo, ou o contrário, conforme a ordem do
        # arquivo. Por código não há ambiguidade.
        "2.01.04": "divida_curto_prazo",
        "2.02.01": "divida_longo_prazo",
        "2.03.05": "lucros_acumulados",
    },
    "DRE": {
        "3.01": "receita_liquida",
        "3.05": "ebit",
        "3.06": "resultado_financeiro",
        "3.06.02": "despesa_financeira",
        # Impairment. A Vale reconheceu R$ 25,1 bi nesta conta em 2025 e o
        # lucro caiu de ~R$ 35 bi para R$ 11,8 bi — o que fez o scanner emitir
        # VENDA sobre um P/L de 30,7x que era só denominador atípico. Sem esta
        # conta não há como distinguir empresa cara de exercício contaminado.
        "3.04.03": "perdas_nao_recorrentes",
        "3.11": "lucro_liquido",
        "3.99.01.01": "lpa_on",
    },
}

# Empresa que não publica 3.11 costuma trazer o lucro em 3.13 (atribuído aos
# sócios da controladora). Serve como segunda opção, nunca sobrescreve 3.11.
CONTAS_ALTERNATIVAS = {"3.13": "lucro_liquido"}

ESCALAS = {"MIL": 1_000.0, "MILHAR": 1_000.0, "UNIDADE": 1.0, "UNIT": 1.0}

# Casar pela DESCRIÇÃO da conta, não só pelo código. Banco usa plano de contas
# diferente: a conta 2.03 de uma indústria é patrimônio líquido, e na DFP de um
# banco significa outra coisa — foi assim que o Itaú apareceu com patrimônio de
# R$ 2,3 trilhões, que é o ativo dele. A descrição a CVM padroniza.
# Comparação é por igualdade exata do texto normalizado (sem acento, minúsculo).
DESCRICOES = {
    "ativo_total": ("ativo total",),
    "ativo_circulante": ("ativo circulante",),
    "passivo_circulante": ("passivo circulante",),
    "passivo_nao_circulante": ("passivo nao circulante",),
    "patrimonio_liquido": (
        "patrimonio liquido consolidado",
        "patrimonio liquido",
    ),
    "receita_liquida": (
        "receita de venda de bens e/ou servicos",
        "receitas da intermediacao financeira",
        "receita liquida de vendas e servicos",
        "receitas de intermediacao financeira",
    ),
    "lucro_liquido": (
        "lucro/prejuizo consolidado do periodo",
        "lucro ou prejuizo liquido consolidado do periodo",
        "lucro/prejuizo do periodo",
    ),
    "ebit": (
        "resultado antes do resultado financeiro e dos tributos",
    ),
    "resultado_financeiro": ("resultado financeiro",),
    "lucros_acumulados": (
        "lucros/prejuizos acumulados",
        "lucros ou prejuizos acumulados",
        "reservas de lucros",
    ),
    "caixa": (
        "caixa e equivalentes de caixa",
    ),
    "perdas_nao_recorrentes": (
        "perdas pela nao recuperabilidade de ativos",
    ),
}


def _normalizar_texto(bruto):
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(bruto or ""))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    return " ".join(texto.lower().split())


DESCRICAO_PARA_CAMPO = {}
for _campo, _textos in DESCRICOES.items():
    for _texto in _textos:
        DESCRICAO_PARA_CAMPO.setdefault(_normalizar_texto(_texto), _campo)

CAMPOS = ["ativo_total", "ativo_circulante", "passivo_circulante",
          "passivo_nao_circulante", "patrimonio_liquido", "receita_liquida",
          "ebit", "resultado_financeiro", "lucro_liquido", "lpa_on",
          # Crédito: alavancagem, cobertura de juros e Altman Z''. Sem estes
          # o laudo de emissor dependia do Yahoo, que não responde do Render.
          "caixa", "divida_curto_prazo", "divida_longo_prazo",
          "despesa_financeira", "lucros_acumulados",
          # Item não recorrente: separa "empresa cara" de "ano atípico".
          "perdas_nao_recorrentes"]

# Balanço do ITR. Só o que é FOTOGRAFIA DE DATA — saldo em 30/06, em 30/09.
# A DRE do ITR é acumulada no ano (1º de janeiro até a data), então lucro,
# receita e EBIT do trimestre NÃO são comparáveis com os da DFP sem reconstruir
# 12 meses móveis. Misturar os dois produz um P/L que nenhuma das duas fontes
# sustenta. Enquanto isso não existir, a DRE continua vindo só da DFP.
CAMPOS_ITR = ["ativo_total", "ativo_circulante", "caixa", "passivo_circulante",
              "passivo_nao_circulante", "patrimonio_liquido",
              "divida_curto_prazo", "divida_longo_prazo", "lucros_acumulados"]


def _baixar(url, rotulo):
    print(f"[{rotulo}] baixando {url}")
    resposta = requests.get(url, timeout=TIMEOUT)
    if resposta.status_code != 200:
        print(f"[{rotulo}] CVM respondeu {resposta.status_code} — pulando")
        return None
    return zipfile.ZipFile(io.BytesIO(resposta.content))


def baixar_zip(ano):
    return _baixar(URL_DFP.format(ano=ano), ano)


def baixar_zip_itr(ano):
    return _baixar(URL_ITR.format(ano=ano), f"ITR {ano}")


def baixar_zip_fre(ano):
    return _baixar(URL_FRE.format(ano=ano), f"FRE {ano}")


# ---------------------------------------------------------------- FRE: ações
#
# Por que o FRE entra. O VPA era calculado com um número de ações DEDUZIDO:
# lucro / LPA. Isso tem três defeitos, e os três aparecem no P/VP:
#
# * Companhia que não publica a conta 3.99.01.01 ficava sem P/VP NENHUM — não
#   um P/VP velho, nenhum. Era a maior fonte de "não apurado" do radar.
# * lucro/LPA dá a média ponderada do exercício. VPA é conceito de data: o que
#   cabe ali é a quantidade emitida no fechamento.
# * Recompra e follow-on no meio do ano ficavam invisíveis.
#
# Por que FRE e não FCA. O FCA é cadastral — auditor, endereço, escriturador,
# DRI — e o único arquivo dele que fala de papel, `valor_mobiliario`, registra
# ONDE cada ação negocia, sem quantidade nenhuma. Capital social é item 17.1 do
# Anexo 24 da ICVM 480, e isso vive no FRE.
#
# O que o total do 17.1 NÃO desconta: ações em tesouraria. É o emitido, então o
# VPA sai levemente subestimado e o P/VP levemente superestimado. O erro é da
# ordem de 1-2% do capital na maioria das companhias, e empurra para o lado
# conservador — um papel nunca vai parecer mais barato do que é por causa disso.

# A convenção de nome de coluna do FRE não é a da DFP (CNPJ_CIA vira
# CNPJ_Companhia). Em vez de fixar uma grafia e quebrar quando ela mudar, o
# cabeçalho real é normalizado e casado contra estes candidatos.
#
# NENHUM candidato tem "circulação" no nome, e isso é deliberado. No item 15.3
# do mesmo formulário, "ações em circulação" significa FREE FLOAT — exclui
# controlador e administração. Usar free float como denominador do VPA
# encolheria a contagem em algo entre 30% e 80% conforme a companhia, inflaria
# o patrimônio por ação na mesma proporção e faria papel de controle
# concentrado aparecer como o mais barato do radar. É a mesma família de erro
# do capital autorizado, e ela não dá exceção: dá um P/VP plausível e falso.
COLUNAS_CAPITAL = {
    "cnpj": ("CNPJ_COMPANHIA", "CNPJ_CIA"),
    "data": ("DATA_REFERENCIA", "DT_REFER"),
    "versao": ("VERSAO",),
    "nome": ("NOME_EMPRESARIAL", "NOME_COMPANHIA", "DENOM_CIA"),
    "tipo": ("TIPO_CAPITAL",),
    "aprovacao": ("DATA_AUTORIZACAO_APROVACAO", "DATA_APROVACAO"),
    "ordinarias": ("QUANTIDADE_ACOES_ORDINARIAS",),
    "preferenciais": ("QUANTIDADE_ACOES_PREFERENCIAIS",),
    "total": ("QUANTIDADE_TOTAL_ACOES", "QUANTIDADE_ACOES"),
}
# Só cnpj e total são indispensáveis. Data, versão e tipo de capital melhoram o
# desempate mas nem todo formulário os traz, e recusar o arquivo inteiro por
# falta deles seria jogar fora a única fonte de quantidade de ações que existe.
OBRIGATORIAS_CAPITAL = ("cnpj", "total")

# Arquivos do FRE que falam de capital mas NÃO respondem "quantas ações existem
# hoje". Ficam de fora por nome, antes de qualquer tentativa de leitura:
#
# * distribuicao_capital (15.3) — free float, pelo motivo acima.
# * aumento / reducao / desdobramento (17.2 a 17.4) — são EVENTOS, o delta de
#   uma operação, não o saldo. Somar delta como se fosse saldo é absurdo.
# * titulo_conversivel — ações que podem passar a existir, não que existem.
# * classe_acao — quebra por classe; exigiria somar, e o arquivo base já traz
#   o total consolidado.
EXCLUIR_DO_CAPITAL = ("distribuicao", "circulacao", "aumento", "reducao",
                      "desdobramento", "titulo_conversivel", "classe_acao")

# Ordem de preferência do tipo de capital. Integralizado é o que foi de fato
# pago; autorizado é apenas o teto do estatuto e NÃO entra em hipótese alguma —
# usar o teto como se fosse capital existente infla as ações, esvazia o VPA e
# faz a companhia parecer barata.
TIPOS_CAPITAL = ("capital integralizado", "capital subscrito", "capital emitido")

# Faixa absoluta de quantidade de ações de companhia listada na B3. A maior do
# mercado tem pouco mais de 13 bilhões de papéis; o teto de 100 bilhões dá
# folga de quase oito vezes sobre isso e ainda assim derruba o que a primeira
# coleta trouxe — uma companhia com 1,9 QUADRILHÃO de ações declaradas e outra
# com 1,8 trilhão. Números assim não são recompra nem diluição: são o campo
# preenchido em outra unidade, ou em outra coisa que não ação.
ACOES_MINIMO_PLAUSIVEL = 100_000.0
ACOES_MAXIMO_PLAUSIVEL = 100_000_000_000.0


def _mapear_colunas_capital(cabecalho):
    """Cabeçalho real -> {papel: nome da coluna}. Devolve (mapa, faltando)."""
    presentes = {}
    for bruto in cabecalho.split(";"):
        limpo = bruto.strip().strip('"').replace("﻿", "")
        presentes[_normalizar_texto(limpo).upper().replace(" ", "_")] = limpo

    mapa = {}
    for papel, candidatos in COLUNAS_CAPITAL.items():
        for candidato in candidatos:
            if candidato in presentes:
                mapa[papel] = presentes[candidato]
                break
    faltando = [p for p in OBRIGATORIAS_CAPITAL if p not in mapa]
    return mapa, faltando


def _quantidade(bruto):
    """Quantidade de ações, tolerando as duas notações que a CVM usa.

    O ponto só é tratado como separador de milhar quando há vírgula na mesma
    string ("1.234.567,00" é brasileiro). Sem vírgula, "1234567.0" é ponto
    decimal e apagá-lo multiplicaria a quantidade por dez — o que reduziria o
    VPA na mesma proporção e faria a companhia parecer dez vezes mais cara.
    """
    texto = str(bruto or "").strip()
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except (TypeError, ValueError):
        return None
    if ACOES_MINIMO_PLAUSIVEL <= numero <= ACOES_MAXIMO_PLAUSIVEL:
        return numero
    return None


def ler_acoes_capital(arquivo_zip, nome_csv):
    """{cnpj: {data_ref, versao, ordinarias, preferenciais, total, nome}}.

    Fica com o registro de data mais recente, desempatando por versão. Entre
    tipos de capital na mesma data, fica com o primeiro de TIPOS_CAPITAL que
    aparecer com quantidade plausível.
    """
    coletado = {}

    try:
        with arquivo_zip.open(nome_csv) as fluxo:
            cabecalho = fluxo.readline().decode("iso-8859-1", errors="replace")
        mapa, faltando = _mapear_colunas_capital(cabecalho)
        if faltando:
            # Layout diferente do esperado. Imprime o que existe de verdade em
            # vez de seguir com colunas erradas: contagem de ações errada não
            # dá erro, dá um P/VP plausível e falso.
            print(f"   ! {nome_csv}: faltam as colunas {faltando}")
            print(f"     cabeçalho real: {cabecalho.strip()[:300]}")
            return {}

        with arquivo_zip.open(nome_csv) as fluxo:
            blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                 usecols=list(mapa.values()), dtype=str,
                                 chunksize=TAMANHO_BLOCO)
            for bloco in blocos:
                for linha in bloco.to_dict("records"):
                    cnpj = "".join(ch for ch in str(linha.get(mapa["cnpj"])) if ch.isdigit())
                    if len(cnpj) != 14:
                        continue

                    if "tipo" in mapa:
                        tipo = _normalizar_texto(linha.get(mapa["tipo"]))
                        if tipo not in TIPOS_CAPITAL:
                            # Capital autorizado cai aqui, e é para cair: é o
                            # teto do estatuto, não ação emitida.
                            continue
                        posto = TIPOS_CAPITAL.index(tipo)
                    else:
                        posto = 0

                    total = _quantidade(linha.get(mapa["total"]))
                    if total is None:
                        continue

                    data = (_data_referencia(linha.get(mapa["data"]))
                            if "data" in mapa else "") or ""
                    versao = _versao(linha.get(mapa["versao"])) if "versao" in mapa else 0

                    # O 17.1 pode trazer mais de uma linha do mesmo tipo de
                    # capital para a mesma companhia, uma por evento aprovado.
                    # Sem a data de aprovação no desempate, quem vencia era a
                    # PRIMEIRA linha do arquivo — ou seja, a ordem física do
                    # CSV decidia a quantidade de ações. Isso não é critério.
                    aprovacao = (_data_referencia(linha.get(mapa["aprovacao"]))
                                 if "aprovacao" in mapa else "") or ""

                    atual = coletado.get(cnpj)
                    if atual is not None:
                        # Mais recente vence; empatou na data, maior versão;
                        # empatou na versão, o tipo de capital melhor colocado;
                        # empatou no tipo, a aprovação mais recente.
                        chave_nova = (data, versao, -posto, aprovacao)
                        chave_atual = (atual["data_ref"], atual["versao"],
                                       -atual["_posto"], atual["_aprovacao"])
                        if chave_nova <= chave_atual:
                            continue

                    coletado[cnpj] = {
                        "data_ref": data,
                        "versao": versao,
                        "_posto": posto,
                        "_aprovacao": aprovacao,
                        "nome": str(linha.get(mapa.get("nome", ""), "") or "").strip(),
                        "ordinarias": _quantidade(linha.get(mapa.get("ordinarias", ""))),
                        "preferenciais": _quantidade(linha.get(mapa.get("preferenciais", ""))),
                        "total": total,
                    }
    except KeyError:
        print(f"   ! {nome_csv} não está no zip")
    except Exception as exc:  # noqa: BLE001
        print(f"   ! falha lendo {nome_csv}: {type(exc).__name__}: {exc}")

    return coletado


def _csvs_candidatos_capital(arquivo_zip, ano):
    """CSVs do FRE que podem trazer quantidade de ações, do mais provável ao menos.

    O nome do arquivo dentro do ZIP não é estável entre versões do formulário,
    e chutar um nome custou uma coleta inteira. Aqui a lista do próprio ZIP é
    que manda.

    A exclusão vem ANTES da pontuação, e é o que importa nesta função. Vários
    arquivos do FRE têm "capital" no nome e responderiam outra pergunta:
    EXCLUIR_DO_CAPITAL diz quais, e por quê. Um deles — distribuição de capital
    — passaria em todas as checagens de coluna e gravaria free float como se
    fosse o total de ações.
    """
    nomes = [n for n in arquivo_zip.namelist() if n.lower().endswith(".csv")]

    def pontuar(nome):
        baixo = _normalizar_texto(nome.rsplit("/", 1)[-1]).replace(" ", "_")
        if any(veto in baixo for veto in EXCLUIR_DO_CAPITAL):
            return 9
        if "capital_social" in baixo:
            return 0
        if "capital" in baixo:
            return 1
        if baixo == f"fre_cia_aberta_{ano}.csv":
            return 2
        return 9

    pontuados = sorted(((pontuar(n), n) for n in nomes), key=lambda p: (p[0], p[1]))
    return [nome for ponto, nome in pontuados if ponto < 9], nomes


def processar_capital(ano):
    arquivo_zip = baixar_zip_fre(ano)
    if arquivo_zip is None:
        return {}

    candidatos, todos = _csvs_candidatos_capital(arquivo_zip, ano)
    if not candidatos:
        print(f"   ! FRE {ano}: nenhum CSV com cara de capital social. "
              f"Arquivos no zip:")
        for nome in todos:
            print(f"       {nome}")
        return {}

    for nome in candidatos:
        lido = ler_acoes_capital(arquivo_zip, nome)
        if lido:
            print(f"   FRE {ano}: {len(lido)} companhias com quantidade de ações "
                  f"(de {nome})")
            return lido

    print(f"   ! FRE {ano}: nenhum dos candidatos serviu. Arquivos no zip:")
    for nome in todos:
        print(f"       {nome}")
    return {}


def listar_fre(ano):
    """Despeja o conteúdo do ZIP do FRE e o cabeçalho de cada CSV.

        python atualizar_fundamentos_cvm.py --listar-fre 2026

    Existe para não voltar a adivinhar nome de arquivo nem de coluna: isto
    mostra o que a CVM publica de fato.
    """
    arquivo_zip = baixar_zip_fre(ano)
    if arquivo_zip is None:
        return
    for nome in sorted(arquivo_zip.namelist()):
        if not nome.lower().endswith(".csv"):
            print(f"\n{nome}  (não é csv)")
            continue
        try:
            with arquivo_zip.open(nome) as fluxo:
                cabecalho = fluxo.readline().decode("iso-8859-1", errors="replace")
                primeira = fluxo.readline().decode("iso-8859-1", errors="replace")
        except Exception as exc:  # noqa: BLE001
            print(f"\n{nome}  ! {type(exc).__name__}: {exc}")
            continue
        print(f"\n{nome}")
        print(f"   colunas: {cabecalho.strip()[:400]}")
        print(f"   1a linha: {primeira.strip()[:400]}")


def gravar_acoes(registros, banco=BANCO):
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    cursor.execute("DROP TABLE IF EXISTS acoes_cia")
    cursor.execute("""
        CREATE TABLE acoes_cia (
            cnpj TEXT PRIMARY KEY,
            data_ref TEXT,
            versao INTEGER,
            denom_cia TEXT,
            ordinarias REAL,
            preferenciais REAL,
            total REAL NOT NULL,
            total_anterior REAL,
            data_anterior TEXT
        )
    """)
    cursor.executemany(
        "INSERT OR REPLACE INTO acoes_cia "
        "(cnpj, data_ref, versao, denom_cia, ordinarias, preferenciais, total, "
        " total_anterior, data_anterior) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [[cnpj, v.get("data_ref"), v.get("versao"), v.get("nome"),
          v.get("ordinarias"), v.get("preferenciais"), v.get("total"),
          v.get("total_anterior"), v.get("data_anterior")]
         for cnpj, v in registros.items()],
    )
    conexao.commit()
    print(f"\n{len(registros)} companhias com ações gravadas em {banco}")
    conexao.close()


def relatorio_qualidade_acoes(banco=BANCO):
    """Cruza a contagem declarada com a deduzida de lucro/LPA.

    As duas medem coisas ligeiramente diferentes — uma é o emitido na data, a
    outra é a média ponderada do exercício — então divergência pequena é
    esperada. Ordem de grandeza diferente não é: é coluna errada.
    """
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    try:
        linhas = cursor.execute("""
            SELECT a.denom_cia, a.total, f.lucro_liquido / f.lpa_on,
                   a.data_ref, f.ano, a.total_anterior, a.data_anterior
            FROM acoes_cia a
            JOIN fundamentos f ON f.cnpj = a.cnpj
            WHERE f.ano = (SELECT MAX(ano) FROM fundamentos WHERE cnpj = f.cnpj)
              AND f.lpa_on IS NOT NULL AND f.lpa_on <> 0
              AND f.lucro_liquido IS NOT NULL
              AND f.lucro_liquido / f.lpa_on > 0
        """).fetchall()
    except sqlite3.Error as exc:
        print(f"\n! não deu para cruzar FRE com DFP: {exc}")
        conexao.close()
        return
    conexao.close()

    if not linhas:
        print("\nFRE x LPA: nada para cruzar.")
        return

    def plausivel(valor):
        return ACOES_MINIMO_PLAUSIVEL <= valor <= ACOES_MAXIMO_PLAUSIVEL

    ambiguos = []
    so_lpa_ruim = []
    for registro in linhas:
        nome, declarado, deduzido = registro[0], registro[1], registro[2]
        if declarado <= 5.0 * deduzido and deduzido <= 5.0 * declarado:
            continue
        # A divergência sozinha não diz QUEM errou. A faixa absoluta diz, nos
        # casos em que um dos dois está fora do universo do possível.
        if plausivel(declarado) and not plausivel(deduzido):
            so_lpa_ruim.append(registro)
        elif plausivel(declarado) and plausivel(deduzido):
            ambiguos.append(registro)

    print(f"\nFRE x lucro/LPA — {len(linhas)} companhias cruzadas.")

    if so_lpa_ruim:
        print(f"\n   {len(so_lpa_ruim)} em que lucro/LPA é que está fora da faixa "
              f"(o FRE resolve):")
        for registro in sorted(so_lpa_ruim, key=lambda x: -x[1])[:10]:
            print(f"      {(registro[0] or '')[:30]:<30} FRE {registro[1]:>15,.0f}   "
                  f"LPA {registro[2]:>18,.0f}")

    print(f"\n   {len(ambiguos)} em que os DOIS são plausíveis e mesmo assim "
          f"discordam. Hoje o P/VP sai NÃO APURADO nestas.")
    print("   A coluna EVENTO diz se a quantidade MUDOU entre um formulário e")
    print("   outro: se mudou, a divergência pode ser desdobramento ou emissão")
    print("   real — as duas certas, em datas diferentes — e não erro.")
    if not ambiguos:
        print("      nenhuma  <-- esperado")
    for nome, declarado, deduzido, data_fre, ano_dfp, anterior, data_ant in \
            sorted(ambiguos, key=lambda x: -x[1])[:20]:
        razao = declarado / deduzido if deduzido else float("inf")
        if anterior:
            evento = f"SIM {anterior:,.0f} em {data_ant or '?'}"
        else:
            evento = "nao mudou"
        print(f"      {(nome or '')[:26]:<26} FRE {declarado:>15,.0f} ({data_fre})"
              f"  LPA {deduzido:>15,.0f} ({ano_dfp})  {razao:>8.1f}x  {evento}")


def _escala(valor):
    return ESCALAS.get(str(valor or "").strip().upper(), 1.0)


def ler_demonstrativo(arquivo_zip, nome_csv, mapa_contas, alternativas=None):
    """Lê um CSV da DFP em blocos e devolve {(cnpj, ano): {campo: valor}}.

    Filtra cedo — só ORDEM_EXERC 'ÚLTIMO' e as contas que interessam — para o
    arquivo inteiro nunca precisar caber na memória.
    """
    alternativas = alternativas or {}
    interessa = set(mapa_contas) | set(alternativas)
    coletado = defaultdict(dict)

    try:
        with arquivo_zip.open(nome_csv) as fluxo:
            blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                 usecols=COLUNAS, dtype=str, chunksize=TAMANHO_BLOCO)
            for bloco in blocos:
                bloco = bloco[bloco["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                # Aceita por código OU por descrição. Filtrar só por código
                # descartava a linha do banco antes de olhar a descrição — que
                # é justamente o que corrige o plano de contas diferente.
                por_codigo = bloco["CD_CONTA"].str.strip().isin(interessa)
                por_texto = bloco["DS_CONTA"].map(_normalizar_texto).isin(DESCRICAO_PARA_CAMPO)
                bloco = bloco[por_codigo | por_texto]
                if bloco.empty:
                    continue

                for linha in bloco.itertuples(index=False):
                    cnpj = "".join(ch for ch in str(linha.CNPJ_CIA) if ch.isdigit())
                    if len(cnpj) != 14:
                        continue
                    ano = str(linha.DT_FIM_EXERC)[:4]
                    if not ano.isdigit():
                        continue

                    try:
                        valor = float(str(linha.VL_CONTA).replace(",", "."))
                    except (TypeError, ValueError):
                        continue

                    conta = str(linha.CD_CONTA).strip()
                    descricao = _normalizar_texto(linha.DS_CONTA)

                    # Prioridade: descrição padronizada > código da conta.
                    campo = DESCRICAO_PARA_CAMPO.get(descricao)
                    por_descricao = campo is not None
                    alternativo = False
                    if campo is None:
                        campo = mapa_contas.get(conta)
                        if campo is None:
                            campo = alternativas.get(conta)
                            alternativo = campo is not None
                    if not campo:
                        continue

                    # LPA já vem em reais por ação: escala não se aplica.
                    if campo != "lpa_on":
                        valor *= _escala(linha.ESCALA_MOEDA)

                    registro = coletado[(cnpj, int(ano))]
                    # Valor vindo da descrição nunca é sobrescrito por código.
                    if campo in registro and not por_descricao:
                        continue
                    if campo in registro and alternativo:
                        continue
                    registro[campo] = valor
                    registro.setdefault("denom_cia", str(linha.DENOM_CIA).strip())
    except KeyError:
        print(f"   ! {nome_csv} não está no zip")
    except Exception as exc:  # noqa: BLE001
        print(f"   ! falha lendo {nome_csv}: {type(exc).__name__}: {exc}")

    return coletado


def processar_ano(ano):
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return {}

    registros = defaultdict(dict)
    for grupo, mapa in CONTAS.items():
        alternativas = CONTAS_ALTERNATIVAS if grupo == "DRE" else None

        consolidado = ler_demonstrativo(
            arquivo_zip, f"dfp_cia_aberta_{grupo}_con_{ano}.csv", mapa, alternativas)
        print(f"   {grupo} consolidado: {len(consolidado)} companhias")

        # Empresa sem controlada não publica consolidado — só o individual.
        # Era por isso que Sanepar e Assaí não apareciam na base.
        individual = ler_demonstrativo(
            arquivo_zip, f"dfp_cia_aberta_{grupo}_ind_{ano}.csv", mapa, alternativas)
        novas = set(individual) - set(consolidado)
        if novas:
            print(f"   {grupo} individual:   +{len(novas)} companhias sem consolidado")

        for chave, valores in consolidado.items():
            registros[chave].update(valores)
        for chave in novas:
            registros[chave].update(individual[chave])

    return registros


def _data_referencia(bruto):
    """'2026-06-30 00:00:00' -> '2026-06-30'. Qualquer outra coisa -> None."""
    texto = str(bruto or "")[:10]
    if len(texto) != 10 or texto[4] != "-" or texto[7] != "-":
        return None
    if not (texto[:4].isdigit() and texto[5:7].isdigit() and texto[8:].isdigit()):
        return None
    return texto


def _versao(bruto):
    try:
        return int(str(bruto).strip())
    except (TypeError, ValueError):
        return 0


def ler_balanco_itr(arquivo_zip, nome_csv, mapa_contas):
    """Lê um CSV de balanço do ITR: {(cnpj, data): {_versao, campo: valor}}.

    Tem função própria, em vez de um parâmetro em `ler_demonstrativo`, porque
    três coisas mudam de uma vez e cada uma delas erraria em silêncio:

    * A chave é a DATA de referência, não o ano. São quatro balanços por ano;
      chavear por ano faria o 3T sobrescrever o 1T conforme a ordem do arquivo.
    * O ITR tem VERSAO. Uma reapresentação republica o mesmo trimestre com
      versão maior, e o arquivo carrega as duas. Sem comparar a versão, qual
      delas vale passa a depender da ordem das linhas — que não é garantida.
    * Só BPA e BPP entram (ver CAMPOS_ITR).

    A coluna VERSAO é procurada no cabeçalho antes de ser pedida: se um ano
    vier em layout sem ela, a leitura continua sem desempate em vez de morrer
    no `usecols`.
    """
    coletado = {}

    try:
        with arquivo_zip.open(nome_csv) as fluxo:
            cabecalho = fluxo.readline().decode("iso-8859-1", errors="replace")
        colunas = list(COLUNAS)
        tem_versao = "VERSAO" in cabecalho.upper()
        if tem_versao:
            colunas.append("VERSAO")

        with arquivo_zip.open(nome_csv) as fluxo:
            blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                 usecols=colunas, dtype=str, chunksize=TAMANHO_BLOCO)
            for bloco in blocos:
                bloco = bloco[bloco["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                por_codigo = bloco["CD_CONTA"].str.strip().isin(set(mapa_contas))
                por_texto = bloco["DS_CONTA"].map(_normalizar_texto).isin(DESCRICAO_PARA_CAMPO)
                bloco = bloco[por_codigo | por_texto]
                if bloco.empty:
                    continue

                for linha in bloco.itertuples(index=False):
                    cnpj = "".join(ch for ch in str(linha.CNPJ_CIA) if ch.isdigit())
                    if len(cnpj) != 14:
                        continue
                    data = _data_referencia(linha.DT_FIM_EXERC)
                    if data is None:
                        continue

                    try:
                        valor = float(str(linha.VL_CONTA).replace(",", "."))
                    except (TypeError, ValueError):
                        continue

                    descricao = _normalizar_texto(linha.DS_CONTA)
                    campo = DESCRICAO_PARA_CAMPO.get(descricao)
                    por_descricao = campo is not None
                    if campo is None:
                        campo = mapa_contas.get(str(linha.CD_CONTA).strip())
                    if campo not in CAMPOS_ITR:
                        continue

                    versao = _versao(getattr(linha, "VERSAO", 0)) if tem_versao else 0
                    chave = (cnpj, data)
                    registro = coletado.get(chave)
                    if registro is None:
                        registro = {"_versao": versao}
                        coletado[chave] = registro
                    elif versao > registro["_versao"]:
                        # Reapresentação: a versão anterior inteira é descartada,
                        # não corrigida campo a campo — o balanço republicado é
                        # um documento novo, e mesclar os dois cria um balanço
                        # que nunca foi publicado.
                        registro = {"_versao": versao}
                        coletado[chave] = registro
                    elif versao < registro["_versao"]:
                        continue

                    if campo in registro and not por_descricao:
                        continue
                    registro[campo] = valor * _escala(linha.ESCALA_MOEDA)
                    registro.setdefault("denom_cia", str(linha.DENOM_CIA).strip())
    except KeyError:
        print(f"   ! {nome_csv} não está no zip")
    except Exception as exc:  # noqa: BLE001
        print(f"   ! falha lendo {nome_csv}: {type(exc).__name__}: {exc}")

    return coletado


def _fundir_itr(registros, chave, valores):
    """Junta BPA e BPP do mesmo trimestre, respeitando a versão de cada um."""
    atual = registros.get(chave)
    if atual is None or valores.get("_versao", 0) > atual.get("_versao", 0):
        registros[chave] = dict(valores)
        return
    if valores.get("_versao", 0) < atual.get("_versao", 0):
        return
    for campo, valor in valores.items():
        atual.setdefault(campo, valor)


def processar_itr(ano):
    arquivo_zip = baixar_zip_itr(ano)
    if arquivo_zip is None:
        return {}

    registros = {}
    for grupo in ("BPA", "BPP"):
        mapa = CONTAS[grupo]
        consolidado = ler_balanco_itr(
            arquivo_zip, f"itr_cia_aberta_{grupo}_con_{ano}.csv", mapa)
        print(f"   ITR {grupo} consolidado: {len(consolidado)} trimestres")

        individual = ler_balanco_itr(
            arquivo_zip, f"itr_cia_aberta_{grupo}_ind_{ano}.csv", mapa)
        novas = set(individual) - set(consolidado)
        if novas:
            print(f"   ITR {grupo} individual:   +{len(novas)} sem consolidado")
        for chave in novas:
            consolidado[chave] = individual[chave]

        for chave, valores in consolidado.items():
            _fundir_itr(registros, chave, valores)

    return registros


def gravar_itr(registros, banco=BANCO):
    """Grava `balanco_itr`, em tabela separada de propósito.

    A tabela `fundamentos` é lida com `ORDER BY ano DESC LIMIT 1` e por
    `historico_por_cnpj`, que responde "houve lucro em todos os exercícios" —
    pergunta que só faz sentido sobre exercício fechado. Enfiar trimestre ali
    dentro mudaria a resposta de Graham sem ninguém pedir.
    """
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    colunas = ", ".join(f"{campo} REAL" for campo in CAMPOS_ITR)
    cursor.execute("DROP TABLE IF EXISTS balanco_itr")
    cursor.execute(f"""
        CREATE TABLE balanco_itr (
            cnpj TEXT NOT NULL,
            data_ref TEXT NOT NULL,
            versao INTEGER,
            denom_cia TEXT,
            {colunas},
            PRIMARY KEY (cnpj, data_ref)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_itr_cnpj ON balanco_itr(cnpj)")

    linhas = []
    for (cnpj, data), valores in registros.items():
        linhas.append([cnpj, data, valores.get("_versao", 0), valores.get("denom_cia")]
                      + [valores.get(campo) for campo in CAMPOS_ITR])

    marcadores = ", ".join("?" * (4 + len(CAMPOS_ITR)))
    cursor.executemany(
        f"INSERT OR REPLACE INTO balanco_itr "
        f"(cnpj, data_ref, versao, denom_cia, {', '.join(CAMPOS_ITR)}) "
        f"VALUES ({marcadores})",
        linhas,
    )
    conexao.commit()

    print(f"\n{len(linhas)} balanços trimestrais gravados em {banco}")
    recente = cursor.execute("SELECT MAX(data_ref) FROM balanco_itr").fetchone()[0]
    companhias = cursor.execute(
        "SELECT COUNT(DISTINCT cnpj) FROM balanco_itr").fetchone()[0]
    print(f"   companhias: {companhias}   data mais recente: {recente}")
    conexao.close()


def relatorio_qualidade_itr(banco=BANCO):
    """O ITR só vale se bater com a DFP na ordem de grandeza.

    O plano de contas de banco já fez o patrimônio do Itaú aparecer como o
    ativo dele. Se o mesmo erro entrar pelo ITR, o P/VP despenca e o papel vai
    para o topo do radar de descontados. Patrimônio não triplica em um
    trimestre: divergência dessa ordem é mapeamento errado, não notícia.
    """
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    try:
        divergentes = cursor.execute("""
            SELECT i.denom_cia, i.data_ref, i.patrimonio_liquido, f.ano,
                   f.patrimonio_liquido
            FROM balanco_itr i
            JOIN fundamentos f ON f.cnpj = i.cnpj
            WHERE i.data_ref = (SELECT MAX(data_ref) FROM balanco_itr
                                WHERE cnpj = i.cnpj)
              AND f.ano = (SELECT MAX(ano) FROM fundamentos WHERE cnpj = f.cnpj)
              AND i.patrimonio_liquido > 0 AND f.patrimonio_liquido > 0
              AND (i.patrimonio_liquido > 3.0 * f.patrimonio_liquido
                   OR f.patrimonio_liquido > 3.0 * i.patrimonio_liquido)
            ORDER BY i.patrimonio_liquido DESC LIMIT 15
        """).fetchall()
    except sqlite3.Error as exc:
        print(f"\n! não deu para cruzar ITR com DFP: {exc}")
        conexao.close()
        return

    print("\nITR x DFP — patrimônio fora de proporção "
          f"({len(divergentes)} companhia(s)):")
    if not divergentes:
        print("   nenhuma  <-- esperado")
    for nome, data, pl_itr, ano, pl_dfp in divergentes:
        print(f"   {(nome or '')[:30]:<30} {data}  ITR {_bi(pl_itr):>8}bi   "
              f"{ano}  DFP {_bi(pl_dfp):>8}bi   <-- revisar")
    conexao.close()


def gravar(registros, banco=BANCO):
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    colunas = ", ".join(f"{campo} REAL" for campo in CAMPOS)
    # A base é reconstruída inteira a cada execução, e o CREATE IF NOT EXISTS
    # não altera o esquema de uma tabela que já existe. Sem este DROP, adicionar
    # um campo em CAMPOS fazia a coleta inteira morrer no INSERT com
    # "table fundamentos has no column named caixa" — que foi exatamente o que
    # aconteceu ao incluir os campos de crédito.
    cursor.execute("DROP TABLE IF EXISTS fundamentos")
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS fundamentos (
            cnpj TEXT NOT NULL,
            ano INTEGER NOT NULL,
            denom_cia TEXT,
            {colunas},
            PRIMARY KEY (cnpj, ano)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cnpj ON fundamentos(cnpj)")

    linhas = []
    for (cnpj, ano), valores in registros.items():
        linhas.append([cnpj, ano, valores.get("denom_cia")]
                      + [valores.get(campo) for campo in CAMPOS])

    marcadores = ", ".join("?" * (3 + len(CAMPOS)))
    cursor.executemany(
        f"INSERT OR REPLACE INTO fundamentos "
        f"(cnpj, ano, denom_cia, {', '.join(CAMPOS)}) VALUES ({marcadores})",
        linhas,
    )
    conexao.commit()

    print(f"\n{len(linhas)} registros gravados em {banco}")
    for campo in CAMPOS:
        preenchidos = cursor.execute(
            f"SELECT COUNT(*) FROM fundamentos WHERE {campo} IS NOT NULL").fetchone()[0]
        print(f"   {campo:<24} {preenchidos}")
    conexao.close()


def _bi(valor):
    return "—" if valor is None else f"{valor / 1e9:.1f}"


def relatorio_qualidade(banco=BANCO):
    """Checagens que denunciam mapeamento errado de conta.

    Patrimônio maior que o ativo é impossível; patrimônio acima de 90% do ativo
    é implausível para qualquer companhia operacional. Foi assim que o erro dos
    bancos apareceu — vale deixar a checagem no script.
    """
    conexao = sqlite3.connect(banco)
    cursor = conexao.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM fundamentos").fetchone()[0] or 1

    checagens = [
        ("patrimônio > ativo (impossível)",
         "patrimonio_liquido IS NOT NULL AND ativo_total IS NOT NULL "
         "AND patrimonio_liquido > ativo_total"),
        ("patrimônio > 90% do ativo (implausível)",
         "patrimonio_liquido IS NOT NULL AND ativo_total > 0 "
         "AND patrimonio_liquido > 0.9 * ativo_total"),
        ("LPA zerado", "lpa_on = 0"),
        ("sem lucro líquido", "lucro_liquido IS NULL"),
        ("sem patrimônio líquido", "patrimonio_liquido IS NULL"),
    ]
    print("\nQualidade:")
    for rotulo, condicao in checagens:
        n = cursor.execute(f"SELECT COUNT(*) FROM fundamentos WHERE {condicao}").fetchone()[0]
        marca = "  <-- revisar" if n > total * 0.05 else ""
        print(f"   {rotulo:<40} {n:>5}  {n / total:>6.1%}{marca}")

    print("\nAmostra (maiores ativos do exercício mais recente):")
    for nome, ativo, pl, receita, lucro, lpa in cursor.execute(
            """SELECT denom_cia, ativo_total, patrimonio_liquido, receita_liquida,
                      lucro_liquido, lpa_on
               FROM fundamentos WHERE ano = (SELECT MAX(ano) FROM fundamentos)
               ORDER BY ativo_total DESC LIMIT 8"""):
        roe = (lucro / pl * 100) if (lucro is not None and pl) else None
        print(f"   {(nome or '')[:30]:<30} ativo {_bi(ativo):>8}bi  PL {_bi(pl):>8}bi  "
              f"receita {_bi(receita):>7}bi  lucro {_bi(lucro):>7}bi  "
              f"ROE {'—' if roe is None else format(roe, '.1f') + '%':>7}  LPA {lpa}")
    conexao.close()


def inspecionar(cnpj_alvo, ano):
    """Despeja o plano de contas de UMA companhia.

        python atualizar_fundamentos_cvm.py --inspecionar 60872504000123 2025

    Serve para descobrir como um emissor publica as contas quando o mapeamento
    padrão erra — em vez de continuar adivinhando o layout.
    """
    cnpj_alvo = "".join(ch for ch in str(cnpj_alvo) if ch.isdigit())
    arquivo_zip = baixar_zip(ano)
    if arquivo_zip is None:
        return
    for grupo in ("BPA", "BPP", "DRE"):
        nome_csv = f"dfp_cia_aberta_{grupo}_con_{ano}.csv"
        print(f"\n===== {grupo} =====")
        try:
            with arquivo_zip.open(nome_csv) as fluxo:
                blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                     usecols=COLUNAS, dtype=str, chunksize=TAMANHO_BLOCO)
                for bloco in blocos:
                    alvo = bloco[
                        bloco["CNPJ_CIA"].str.replace(r"\D", "", regex=True) == cnpj_alvo]
                    alvo = alvo[alvo["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                    for linha in alvo.itertuples(index=False):
                        conta = str(linha.CD_CONTA).strip()
                        # Topo da árvore, MAIS o LPA. O comentário antigo dizia
                        # incluir 3.99.01.01, mas o corte por número de pontos
                        # derrubava justamente essa conta — três pontos. O LPA
                        # é o denominador da contagem de ações deduzida, então
                        # era o único número que esta ferramenta precisava
                        # mostrar e era o único que ela escondia.
                        if conta.count(".") > 2 and not conta.startswith("3.99"):
                            continue
                        try:
                            valor = float(str(linha.VL_CONTA).replace(",", "."))
                        except (TypeError, ValueError):
                            continue
                        # Valor por ação sai com casa decimal: R$ 22,27 virava
                        # "22" no formato inteiro, e é exatamente a casa que
                        # diz se o LPA está na escala do balanço ou em reais.
                        formatado = (f"{valor:>18,.4f}" if conta.startswith("3.99")
                                     else f"{valor:>18,.0f}")
                        print(f"   {conta:<12} {str(linha.DS_CONTA)[:52]:<52} "
                              f"{formatado}  [{linha.ESCALA_MOEDA}]")
        except Exception as exc:  # noqa: BLE001
            print(f"   ! {type(exc).__name__}: {exc}")


def coletar_dfp(anos):
    registros = {}
    for ano in anos:
        parcial = processar_ano(ano)
        for chave, valores in parcial.items():
            registros.setdefault(chave, {}).update(valores)
    if not registros:
        raise SystemExit("Nenhum dado obtido da CVM.")
    gravar(registros)
    relatorio_qualidade()


def coletar_itr(anos):
    registros = {}
    for ano in anos:
        for chave, valores in processar_itr(ano).items():
            _fundir_itr(registros, chave, valores)
    if not registros:
        print("\n! Nenhum balanço trimestral obtido — a base segue só com a DFP.")
        return
    gravar_itr(registros)
    relatorio_qualidade_itr()


def coletar_capital(anos):
    """Fica com a declaração mais recente de cada companhia entre os anos.

    Guarda também a quantidade ANTERIOR, quando ela existe e é diferente. Sem
    isso não há como distinguir as duas causas de um FRE que discorda da DFP:

    * erro de preenchimento, e aí a divergência é ruído;
    * desdobramento, grupamento ou emissão entre uma data e outra, e aí as
      DUAS estão certas, cada uma na sua data — e a do FRE é a que casa com o
      preço de hoje.

    A diferença entre os dois casos aparece na série: quantidade que mudou de
    um formulário para o outro é evento; quantidade estável que mesmo assim
    discorda da DFP é outra coisa.
    """
    registros = {}
    for ano in sorted(anos):
        for cnpj, valores in processar_capital(ano).items():
            atual = registros.get(cnpj)
            if atual is None or (valores["data_ref"], valores["versao"]) >= \
                    (atual["data_ref"], atual["versao"]):
                if atual is not None and atual.get("total") != valores.get("total"):
                    valores = dict(valores)
                    valores["total_anterior"] = atual.get("total")
                    valores["data_anterior"] = atual.get("data_ref")
                registros[cnpj] = valores
    if not registros:
        print("\n! Nenhuma quantidade de ações obtida — o VPA segue saindo de "
              "lucro/LPA, como antes.")
        return
    gravar_acoes(registros)
    relatorio_qualidade_acoes()


def inspecionar_fre(cnpj_alvo, ano):
    """Despeja TODAS as linhas de capital social de UMA companhia no FRE.

        python atualizar_fundamentos_cvm.py --inspecionar-fre 43776517000180 2026

    Mostra o registro inteiro, com todas as colunas, sem filtro de tipo de
    capital nem de data — inclusive as linhas que a coleta descarta. É o que
    responde "por que esta companhia veio com esta quantidade" sem hipótese.
    """
    cnpj_alvo = "".join(ch for ch in str(cnpj_alvo) if ch.isdigit())
    arquivo_zip = baixar_zip_fre(ano)
    if arquivo_zip is None:
        return
    candidatos, todos = _csvs_candidatos_capital(arquivo_zip, ano)
    if not candidatos:
        print("   ! nenhum CSV de capital social. Arquivos no zip:")
        for nome in todos:
            print(f"       {nome}")
        return

    nome_csv = candidatos[0]
    print(f"\n===== {nome_csv} =====")
    try:
        with arquivo_zip.open(nome_csv) as fluxo:
            cabecalho = fluxo.readline().decode("iso-8859-1", errors="replace")
        colunas = [c.strip().strip('"') for c in cabecalho.strip().split(";")]
        coluna_cnpj = next(
            (c for c in colunas
             if _normalizar_texto(c).upper().replace(" ", "_")
             in ("CNPJ_COMPANHIA", "CNPJ_CIA")), None)
        if coluna_cnpj is None:
            print(f"   ! não achei coluna de CNPJ em: {cabecalho.strip()[:300]}")
            return

        with arquivo_zip.open(nome_csv) as fluxo:
            blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                 dtype=str, chunksize=TAMANHO_BLOCO)
            achou = False
            for bloco in blocos:
                alvo = bloco[bloco[coluna_cnpj].str.replace(r"\D", "", regex=True)
                             == cnpj_alvo]
                for registro in alvo.to_dict("records"):
                    achou = True
                    print("   " + "-" * 60)
                    for chave, valor in registro.items():
                        if valor is not None and str(valor).strip() not in ("", "nan"):
                            print(f"   {str(chave)[:34]:<34} {str(valor)[:60]}")
            if not achou:
                print("   (nada para este CNPJ)")
    except Exception as exc:  # noqa: BLE001
        print(f"   ! {type(exc).__name__}: {exc}")


def inspecionar_itr(cnpj_alvo, ano):
    """Despeja o balanço de UMA companhia no ITR, consolidado e individual.

        python atualizar_fundamentos_cvm.py --inspecionar-itr 33592510000154 2026

    Serve para as divergências do relatório de qualidade: mostra qual linha foi
    lida, de qual arquivo e em que escala, em vez de deixar a explicação no
    campo da hipótese.
    """
    cnpj_alvo = "".join(ch for ch in str(cnpj_alvo) if ch.isdigit())
    arquivo_zip = baixar_zip_itr(ano)
    if arquivo_zip is None:
        return
    for grupo in ("BPA", "BPP"):
        for base in ("con", "ind"):
            nome_csv = f"itr_cia_aberta_{grupo}_{base}_{ano}.csv"
            print(f"\n===== {grupo} {base} =====")
            try:
                with arquivo_zip.open(nome_csv) as fluxo:
                    blocos = pd.read_csv(fluxo, sep=";", encoding="iso-8859-1",
                                         dtype=str, chunksize=TAMANHO_BLOCO)
                    achou = False
                    for bloco in blocos:
                        alvo = bloco[bloco["CNPJ_CIA"].str.replace(r"\D", "", regex=True)
                                     == cnpj_alvo]
                        alvo = alvo[alvo["ORDEM_EXERC"].str.strip().str.upper() == "ÚLTIMO"]
                        for linha in alvo.itertuples(index=False):
                            conta = str(linha.CD_CONTA).strip()
                            if conta.count(".") > 1:
                                continue
                            try:
                                valor = float(str(linha.VL_CONTA).replace(",", "."))
                            except (TypeError, ValueError):
                                continue
                            achou = True
                            versao = getattr(linha, "VERSAO", "?")
                            print(f"   {str(linha.DT_FIM_EXERC)[:10]}  v{versao}  "
                                  f"{conta:<8} {str(linha.DS_CONTA)[:44]:<44} "
                                  f"{valor:>18,.0f}  [{linha.ESCALA_MOEDA}]")
                    if not achou:
                        print("   (nada para este CNPJ)")
            except Exception as exc:  # noqa: BLE001
                print(f"   ! {type(exc).__name__}: {exc}")


def main(anos, anos_itr=None, com_dfp=True, com_itr=True, com_acoes=True):
    if com_dfp:
        coletar_dfp(anos)
    if com_itr:
        coletar_itr(anos_itr or anos)
    if com_acoes:
        coletar_capital(anos_itr or anos)


if __name__ == "__main__":
    if "--inspecionar" in sys.argv:
        posicao = sys.argv.index("--inspecionar")
        restante = sys.argv[posicao + 1:]
        if not restante:
            raise SystemExit("uso: --inspecionar <cnpj> [ano]")
        cnpj = restante[0]
        ano_alvo = int(restante[1]) if len(restante) > 1 else 2025
        inspecionar(cnpj, ano_alvo)
        raise SystemExit(0)

    from datetime import date
    atual = date.today().year

    if "--inspecionar-itr" in sys.argv:
        posicao = sys.argv.index("--inspecionar-itr")
        restante = [a for a in sys.argv[posicao + 1:] if not a.startswith("--")]
        if not restante:
            raise SystemExit("uso: --inspecionar-itr <cnpj> [ano]")
        inspecionar_itr(restante[0],
                        int(restante[1]) if len(restante) > 1 else atual)
        raise SystemExit(0)

    if "--inspecionar-fre" in sys.argv:
        posicao = sys.argv.index("--inspecionar-fre")
        restante = [a for a in sys.argv[posicao + 1:] if not a.startswith("--")]
        if not restante:
            raise SystemExit("uso: --inspecionar-fre <cnpj> [ano]")
        inspecionar_fre(restante[0],
                        int(restante[1]) if len(restante) > 1 else atual)
        raise SystemExit(0)

    if "--listar-fre" in sys.argv:
        posicao = sys.argv.index("--listar-fre")
        restante = [a for a in sys.argv[posicao + 1:] if a.isdigit()]
        listar_fre(int(restante[0]) if restante else atual)
        raise SystemExit(0)

    argumentos = [a for a in sys.argv[1:] if a.isdigit()]
    if argumentos:
        anos_alvo = [int(a) for a in argumentos]
        anos_itr_alvo = anos_alvo
    else:
        anos_alvo = [atual - 3, atual - 2, atual - 1]
        # O ITR vale pelo trimestre mais recente. O ano anterior entra junto
        # para que uma coleta rodada em janeiro, antes do 1T sair, ainda tenha
        # o 3T do ano passado em vez de nada.
        anos_itr_alvo = [atual - 1, atual]

    so_itr = "--so-itr" in sys.argv
    sem_itr = "--sem-itr" in sys.argv
    so_acoes = "--so-acoes" in sys.argv
    sem_acoes = "--sem-acoes" in sys.argv
    if so_itr and sem_itr:
        raise SystemExit("--so-itr e --sem-itr se cancelam: escolha um.")
    if so_acoes and sem_acoes:
        raise SystemExit("--so-acoes e --sem-acoes se cancelam: escolha um.")
    if so_itr and so_acoes:
        raise SystemExit("--so-itr e --so-acoes se cancelam: escolha um.")

    com_dfp = not (so_itr or so_acoes)
    com_itr = not (sem_itr or so_acoes)
    com_acoes = not (sem_acoes or so_itr)

    if com_dfp:
        print(f"Exercícios (DFP): {anos_alvo}")
    if com_itr:
        print(f"Trimestres (ITR): {anos_itr_alvo}")
    if com_acoes:
        print(f"Ações (FRE):      {anos_itr_alvo}")
    main(anos_alvo, anos_itr_alvo, com_dfp=com_dfp, com_itr=com_itr, com_acoes=com_acoes)
