"""Identidade visual dos papéis: logo, setor e cor.

O terminal mostrava só o código do papel. Numa tabela de 100 linhas, ler
"CMIG4" e "CPLE6" e "CPFE3" exige atenção que devia estar no número. Um logo
ao lado do ticker resolve isso em milissegundos de leitura.

Como funciona, e por que assim:

  ticker -> raiz -> domínio da companhia -> logo (buscado pelo NAVEGADOR)
                 -> setor -> cor do monograma (reserva)

O logo é buscado pelo navegador do usuário, não pelo servidor: o Render não
precisa de rede extra, não há imagem para armazenar e nada quebra o deploy se
o serviço de ícones sair do ar. Quando a imagem não carrega, o front-end
esconde o `img` e o monograma que já estava embaixo aparece — nunca aparece
ícone quebrado.

O mapa é curado à mão. Domínio errado mostra o logo da empresa errada ao lado
do papel, o que num terminal de assessoria é pior que não mostrar logo nenhum;
por isso companhia sobre a qual eu não tinha certeza do domínio ficou de fora
e cai no monograma, que é sempre correto.
"""

import re

# raiz -> (domínio, setor). Setor é nosso, não do Yahoo: com a CVM como fonte
# primária de fundamentos o campo `sector` do Yahoo vem vazio na maioria dos
# papéis, e a cor do monograma ficaria toda cinza.
EMPRESAS = {
    "ABEV": ("ambev.com.br", "Consumo"),
    "ABCB": ("abcbrasil.com.br", "Financeiro"),
    "ALPA": ("alpargatas.com.br", "Consumo"),
    "ALUP": ("alupar.com.br", "Energia"),
    "ASAI": ("assai.com.br", "Varejo"),
    "AURE": ("auren.com.br", "Energia"),
    "AXIA": ("eletrobras.com", "Energia"),
    "B3SA": ("b3.com.br", "Financeiro"),
    "BBAS": ("bb.com.br", "Financeiro"),
    "BBDC": ("bradesco.com.br", "Financeiro"),
    "BBSE": ("bbseguridade.com.br", "Financeiro"),
    "BEEF": ("minervafoods.com", "Consumo"),
    "BPAC": ("btgpactual.com", "Financeiro"),
    "BRAP": ("bradespar.com.br", "Materiais"),
    "BRSR": ("banrisul.com.br", "Financeiro"),
    "CEAB": ("cea.com.br", "Varejo"),
    "CMIG": ("cemig.com.br", "Energia"),
    "CMIN": ("csnmineracao.com.br", "Materiais"),
    "COGN": ("cogna.com.br", "Educação"),
    "CPFE": ("cpfl.com.br", "Energia"),
    "CPLE": ("copel.com", "Energia"),
    "CSAN": ("cosan.com.br", "Energia"),
    "CSMG": ("copasa.com.br", "Saneamento"),
    "CSNA": ("csn.com.br", "Materiais"),
    "CXSE": ("caixaseguridade.com.br", "Financeiro"),
    "CYRE": ("cyrela.com.br", "Construção"),
    "DIRR": ("direcional.com.br", "Construção"),
    "ECOR": ("ecorodovias.com.br", "Transporte"),
    "EGIE": ("engie.com.br", "Energia"),
    "ELET": ("eletrobras.com", "Energia"),
    "EMBJ": ("embraer.com", "Industrial"),
    "EMBR": ("embraer.com", "Industrial"),
    "ENEV": ("eneva.com.br", "Energia"),
    "ENGI": ("energisa.com.br", "Energia"),
    "EQTL": ("equatorialenergia.com.br", "Energia"),
    "EVEN": ("even.com.br", "Construção"),
    "EZTC": ("eztec.com.br", "Construção"),
    "FLRY": ("fleury.com.br", "Saúde"),
    "GGBR": ("gerdau.com", "Materiais"),
    "GMAT": ("grupomateus.com.br", "Varejo"),
    "GOAU": ("gerdau.com", "Materiais"),
    "HAPV": ("hapvida.com.br", "Saúde"),
    "HYPE": ("hypera.com.br", "Saúde"),
    "IGTI": ("iguatemi.com.br", "Imobiliário"),
    "IRBR": ("irbre.com", "Financeiro"),
    "ITSA": ("itausa.com.br", "Financeiro"),
    "ITUB": ("itau.com.br", "Financeiro"),
    "JBSS": ("jbs.com.br", "Consumo"),
    "JHSF": ("jhsf.com.br", "Imobiliário"),
    "JSLG": ("jsl.com.br", "Transporte"),
    "KLBN": ("klabin.com.br", "Materiais"),
    "LREN": ("lojasrenner.com.br", "Varejo"),
    "MBRF": ("marfrig.com.br", "Consumo"),
    "MGLU": ("magazineluiza.com.br", "Varejo"),
    "MOVI": ("movida.com.br", "Transporte"),
    "MRFG": ("marfrig.com.br", "Consumo"),
    "MRVE": ("mrv.com.br", "Construção"),
    "MULT": ("multiplan.com.br", "Imobiliário"),
    "NATU": ("natura.com.br", "Consumo"),
    "NTCO": ("natura.com.br", "Consumo"),
    "PETR": ("petrobras.com.br", "Petróleo"),
    "POMO": ("marcopolo.com.br", "Industrial"),
    "PRIO": ("prio3.com.br", "Petróleo"),
    "PSSA": ("portoseguro.com.br", "Financeiro"),
    "RADL": ("rd.com.br", "Saúde"),
    "RAIL": ("rumolog.com", "Transporte"),
    "RDOR": ("rededor.com.br", "Saúde"),
    "RECV": ("petroreconcavo.com.br", "Petróleo"),
    "RENT": ("localiza.com", "Transporte"),
    "SANB": ("santander.com.br", "Financeiro"),
    "SAPR": ("sanepar.com.br", "Saneamento"),
    "SBSP": ("sabesp.com.br", "Saneamento"),
    "SIMH": ("simpar.com.br", "Transporte"),
    "SLCE": ("slcagricola.com.br", "Agro"),
    "SMFT": ("smartfit.com.br", "Consumo"),
    "SMTO": ("saomartinho.com.br", "Agro"),
    "STBP": ("santosbrasil.com.br", "Transporte"),
    "SUZB": ("suzano.com.br", "Materiais"),
    "TAEE": ("taesa.com.br", "Energia"),
    "TEND": ("construtoratenda.com.br", "Construção"),
    "TIMS": ("tim.com.br", "Telecom"),
    "TOTS": ("totvs.com", "Tecnologia"),
    "TUPY": ("tupy.com.br", "Industrial"),
    "UGPA": ("ultra.com.br", "Petróleo"),
    "USIM": ("usiminas.com", "Materiais"),
    "VALE": ("vale.com", "Materiais"),
    "VAMO": ("grupovamos.com.br", "Transporte"),
    "VBBR": ("vibraenergia.com.br", "Petróleo"),
    "VIVA": ("vivara.com.br", "Varejo"),
    "VIVT": ("vivo.com.br", "Telecom"),
    "WEGE": ("weg.net", "Industrial"),
    "YDUQ": ("yduqs.com.br", "Educação"),
}

# Paleta do terminal: verde é a cor da casa, o resto acompanha sem competir.
CORES_SETOR = {
    "Financeiro": "#1e6f5c",
    "Energia": "#2f7d32",
    "Petróleo": "#14532d",
    "Materiais": "#4d6b1f",
    "Consumo": "#7a5c1e",
    "Varejo": "#8a4b1e",
    "Saúde": "#1f6b6b",
    "Construção": "#5a4a2a",
    "Imobiliário": "#3f5f7a",
    "Transporte": "#3b5f3b",
    "Telecom": "#2b5f7f",
    "Tecnologia": "#2a6f8f",
    "Saneamento": "#1f6f7f",
    "Educação": "#6b3f6b",
    "Agro": "#4f7a2a",
    "Industrial": "#4a5a6a",
    "Outros": "#3a4450",
}

# Serviço de ícones consultado pelo NAVEGADOR. Trocar aqui troca em toda a
# tela; se sair do ar, todo mundo cai no monograma e nada quebra.
MODELO_LOGO = "https://icons.duckduckgo.com/ip3/{dominio}.ico"

_RAIZ = re.compile(r"^([A-Z0-9]{4})\d{1,2}$")


def raiz_do_ticker(ticker):
    casou = _RAIZ.match((ticker or "").upper().strip())
    return casou.group(1) if casou else None


def identidade(ticker):
    """{ticker, raiz, setor, cor, logo, monograma}. Nunca levanta, nunca None.

    `logo` vem None quando não há domínio curado — e aí a tela usa só o
    monograma, em vez de tentar adivinhar um domínio e mostrar a marca errada.
    """
    codigo = (ticker or "").upper().strip()
    raiz = raiz_do_ticker(codigo)
    dominio, setor = EMPRESAS.get(raiz or "", (None, "Outros"))
    return {
        "ticker": codigo,
        "raiz": raiz,
        "setor": setor,
        "cor": CORES_SETOR.get(setor, CORES_SETOR["Outros"]),
        "logo": MODELO_LOGO.format(dominio=dominio) if dominio else None,
        # Duas letras leem melhor que quatro num tile de 22px.
        "monograma": (raiz or codigo)[:2],
    }


def mapa(tickers):
    return {t: identidade(t) for t in tickers or []}


def cobertura(tickers):
    """Quantos papéis da lista têm logo. Usado no /diagnostico."""
    total = len(tickers or [])
    com_logo = sum(1 for t in tickers or [] if identidade(t)["logo"])
    return {"total": total, "com_logo": com_logo,
            "sem_logo": [t for t in tickers or [] if not identidade(t)["logo"]]}
