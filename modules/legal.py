"""Textos legais em um lugar só.

Existe porque o mesmo aviso precisa aparecer em quatro lugares — rodapé da
tela, faixa de primeira visita, páginas públicas e resposta de API para o app
Android — e um aviso que diverge entre as cópias não protege ninguém. Aqui é
a fonte única; a tela e as rotas leem daqui.

Nada neste arquivo é parecer jurídico. É o texto padrão de mercado para
ferramenta de informação; antes de publicar comercialmente, passe por
advogado de mercado de capitais.
"""

import os

VERSAO = "1.0"
ATUALIZADO_EM = "2026-09-09"

# Editáveis por ambiente: o e-mail e o foro são dados do titular, não do
# código. Sem CONTATO_LEGAL definido, a página diz que o contato não foi
# configurado — melhor do que publicar um endereço errado.
CONTATO = os.environ.get("CONTATO_LEGAL", "").strip()
FORO = os.environ.get("FORO_LEGAL", "Comarca de Porto Alegre/RS").strip()
TITULAR = os.environ.get("TITULAR_LEGAL", "o titular do AlphaForge").strip()

# --------------------------------------------------------------------------- #
# Aviso principal
# --------------------------------------------------------------------------- #
AVISO_CURTO = ("O AlphaForge é ferramenta de informação e cálculo. "
               "Não constitui recomendação de investimento.")

AVISO_TITULO = "Aviso — não é recomendação de investimento"

AVISO_CVM = [
    ("O AlphaForge é uma ferramenta de tecnologia para organização, cálculo e "
     "visualização de informações públicas sobre o mercado de capitais. Seu "
     "conteúdo tem caráter exclusivamente informativo e educacional."),

    ("O AlphaForge não presta serviço de análise de valores mobiliários "
     "(Resolução CVM nº 20/2021), de consultoria de valores mobiliários "
     "(Resolução CVM nº 19/2021) nem de administração de carteiras "
     "(Resolução CVM nº 21/2021), e não é credenciado para tanto. Nada aqui "
     "constitui recomendação, aconselhamento, oferta ou solicitação de compra "
     "ou venda de qualquer ativo."),

    ("Filtros, notas, rankings, classificações e vereditos exibidos são "
     "resultado determinístico de critérios quantitativos públicos e "
     "parametrizados — aplicados igualmente a todos os ativos, sem juízo sobre "
     "o mérito do investimento e sem consideração ao perfil, aos objetivos ou "
     "à situação financeira de qualquer pessoa."),

    ("Retorno esperado, probabilidade de lucro, preço-teto e projeções "
     "decorrem inteiramente das premissas informadas por quem usa a "
     "ferramenta. São o resultado aritmético dessas premissas levadas ao "
     "horizonte escolhido — não são previsão, promessa ou garantia."),

    ("Os dados vêm de fontes públicas (CVM, B3, Banco Central do Brasil) e de "
     "terceiros (Yahoo Finance, com atraso de até 15 minutos; veículos de "
     "imprensa). Não há garantia de exatidão, completude, atualidade ou "
     "disponibilidade. Erros de origem se propagam."),

    ("Investimentos em renda variável envolvem risco de perda. Operações com "
     "derivativos podem gerar perdas superiores ao capital aplicado, "
     "especialmente em posições vendidas a descoberto. Rentabilidade passada "
     "não representa garantia de rentabilidade futura."),

    ("As decisões de investimento são de responsabilidade exclusiva de quem as "
     "toma. Antes de investir, leia os documentos oficiais do produto e "
     "consulte profissional habilitado."),

    ("O AlphaForge não representa, não atua em nome de e não se manifesta por "
     "qualquer instituição integrante do sistema de distribuição de valores "
     "mobiliários."),
]

# --------------------------------------------------------------------------- #
# Termos de uso
# --------------------------------------------------------------------------- #
TERMOS_TITULO = "Termos de uso"

TERMOS = [
    ("1. Objeto",
     ["O AlphaForge é disponibilizado como ferramenta de consulta e cálculo "
      "sobre informações públicas do mercado de capitais brasileiro. O uso "
      "implica concordância integral com estes termos e com o aviso de que o "
      "serviço não constitui recomendação de investimento."]),

    ("2. Licença de uso",
     ["É concedida licença pessoal, revogável, não exclusiva e "
      "intransferível para uso da ferramenta.",
      "É vedado revender, sublicenciar, redistribuir em massa ou extrair "
      "sistematicamente o conteúdo por meios automatizados sem autorização "
      "expressa."]),

    ("3. Ausência de garantia",
     ["O serviço é fornecido no estado em que se encontra, sem garantia de "
      "disponibilidade, exatidão ou adequação a qualquer finalidade "
      "específica.",
      "Fontes de dados de terceiros podem falhar, atrasar ou ser "
      "descontinuadas sem aviso."]),

    ("4. Limitação de responsabilidade",
     ["Na máxima extensão permitida pela legislação aplicável, não haverá "
      "responsabilidade por perdas, danos diretos ou indiretos, lucros "
      "cessantes ou prejuízos decorrentes de decisões tomadas com base no "
      "conteúdo exibido."]),

    ("5. Propriedade intelectual",
     ["O código, a identidade visual e os modelos de cálculo do AlphaForge "
      "pertencem ao seu titular. Os dados públicos exibidos pertencem às "
      "respectivas fontes."]),

    ("6. Alterações",
     ["Estes termos podem ser alterados a qualquer tempo. A versão vigente é "
      "sempre a publicada nesta página, com a data de atualização indicada."]),

    ("7. Foro",
     ["Fica eleito o foro da {foro} para dirimir controvérsias decorrentes "
      "destes termos."]),
]

# --------------------------------------------------------------------------- #
# Política de privacidade — exigida pelo Google Play
# --------------------------------------------------------------------------- #
PRIVACIDADE_TITULO = "Política de privacidade"

PRIVACIDADE = [
    ("Resumo",
     ["O AlphaForge não exige cadastro, não pede dados pessoais e não vende "
      "informação a ninguém. O que ele guarda é o mínimo técnico para "
      "funcionar e se defender de abuso."]),

    ("Dados que coletamos",
     ["Registros técnicos de acesso: endereço IP, data e hora, rota "
      "acessada e identificação do navegador. Servem para segurança, "
      "diagnóstico e limitação de abuso.",
      "Consultas feitas na ferramenta (por exemplo, o código do ativo "
      "pesquisado), de forma não associada a pessoa identificada.",
      "Preferências de uso guardadas no armazenamento local do próprio "
      "navegador — como a aba aberta por último e a dispensa do aviso "
      "inicial. Esses dados não saem do dispositivo."]),

    ("Dados que NÃO coletamos",
     ["Nome, CPF, e-mail, telefone ou endereço.",
      "Dados de conta em corretora, senha, chave de API ou saldo.",
      "Posição de carteira: os valores digitados nas calculadoras e no "
      "otimizador são processados para devolver o resultado e não são "
      "armazenados."]),

    ("Compartilhamento",
     ["Não há compartilhamento de dados pessoais com terceiros para fins "
      "publicitários ou comerciais.",
      "Ao usar a ferramenta, requisições são feitas a fontes públicas e de "
      "terceiros (CVM, B3, Banco Central, Yahoo Finance e veículos de "
      "imprensa). O uso dessas fontes segue as políticas de cada uma."]),

    ("Retenção",
     ["Registros técnicos são mantidos pelo prazo necessário à segurança e "
      "ao diagnóstico do serviço, e então descartados."]),

    ("Seus direitos",
     ["Nos termos da Lei nº 13.709/2018 (LGPD), você pode solicitar "
      "confirmação de tratamento, acesso, correção ou eliminação de dados a "
      "seu respeito, pelo canal de contato indicado abaixo."]),

    ("Contato",
     ["{contato}"]),
]


def _resolver(texto):
    return (texto.replace("{foro}", FORO)
                 .replace("{contato}", CONTATO or
                          "Canal de contato ainda não configurado neste "
                          "ambiente (defina CONTATO_LEGAL).")
                 .replace("{titular}", TITULAR))


def aviso():
    """O aviso completo, já resolvido. Usado pela API e pelas páginas."""
    return {
        "versao": VERSAO,
        "atualizado_em": ATUALIZADO_EM,
        "titulo": AVISO_TITULO,
        "resumo": AVISO_CURTO,
        "paragrafos": [_resolver(p) for p in AVISO_CVM],
    }


def secoes(bloco):
    return [(titulo, [_resolver(p) for p in paragrafos])
            for titulo, paragrafos in bloco]
