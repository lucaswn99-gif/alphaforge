"""Gestão de Patrimônio: radar de FIIs/ETFs e otimização de carteira.

Duas correções estruturais em relação à versão anterior:

1. O dividend yield passa pelo mesmo `normalizar_dy` do módulo de renda
   variável. A conta antiga (`dividendYield * 100`, dividindo de novo se
   passasse de 100) transformava um DY de 0,5% em 50% — e DY é justamente o
   critério de ordenação da tabela de FIIs.
2. Preço e histórico vêm de uma chamada vetorizada, não de 30 `.info` em
   paralelo. `.info` é o endpoint que o Yahoo limita; 30 threads nele é o
   caminho mais curto para a tabela voltar vazia.
"""

import concurrent.futures

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, Query

from modules import fundamentos_fii, identidade, otimizador, taxas
from routers.equity import (carimbo_de_coleta, dy_da_serie, fatiar_precos,
                            normalizar_dy)

router = APIRouter(prefix="/wealth", tags=["Gestão de Patrimônio & Fundos"])

FIIS_TIJOLO = ["HGLG11", "BTLG11", "XPML11", "VISC11", "ALZR11",
               "KNRI11", "VILG11", "MALL11", "PVBI11", "BRCR11"]
FIIS_PAPEL = ["KNIP11", "KNCR11", "IRDM11", "CPTS11", "MXRF11",
              "HGCR11", "MCCI11", "RECR11", "CVBI11", "VRTA11"]
ETFS_B3 = ["BOVA11", "IVVB11", "SMAL11", "NASD11", "HASH11",
           "DIVO11", "SPXI11", "GOLD11", "XINA11", "BINA11"]

MAX_WORKERS_INFO = 6
SMA_ETF = 20
DIAS_PIOR_CASO = 90

# A taxa livre de risco vem do Banco Central (ver modules/taxas.py). O
# parâmetro `selic_aa` do endpoint sobrescreve quando você quiser testar outro
# cenário; sem ele, vale a meta vigente.


def _fundamentos_fii(simbolo):
    """P/VP e DY de um FII. Só isto exige `.info`."""
    try:
        info = yf.Ticker(simbolo).info or {}
    except Exception:  # noqa: BLE001
        return None
    if not info:
        return None
    pvp = info.get("priceToBook")
    try:
        pvp = float(pvp) if pvp is not None else None
    except (TypeError, ValueError):
        pvp = None
    return {"pvp": pvp, "dy": normalizar_dy(info),
            "nome": info.get("shortName") or simbolo.replace(".SA", "")}


def _baixar_precos(tickers, periodo="2y"):
    """2 anos e actions=True: o histórico maior é o que permite somar 12 meses
    de proventos, e os dividendos vêm na mesma requisição do preço."""
    simbolos = [f"{t}.SA" for t in tickers]
    try:
        return yf.download(simbolos, period=periodo, interval="1d",
                           progress=False, group_by="ticker", auto_adjust=True,
                           actions=True)
    except Exception:  # noqa: BLE001
        return None


def extrair_fechamentos(bruto, simbolos):
    """Matriz de fechamentos, seja qual for o layout devolvido pelo yfinance.

    O yfinance devolve (campo, ticker) sem `group_by`, (ticker, campo) com
    `group_by="ticker"`, e um DataFrame simples para um ticker só. O resto do
    projeto usa a segunda forma; o otimizador assumia a primeira. Em vez de
    escolher uma e torcer para o padrão não mudar de versão, aceitamos as três.
    """
    if bruto is None or getattr(bruto, "empty", True):
        return None

    if isinstance(bruto, pd.Series):
        return bruto.to_frame(name=simbolos[0])

    colunas = bruto.columns
    if isinstance(colunas, pd.MultiIndex):
        nivel_zero = set(colunas.get_level_values(0))
        if "Close" in nivel_zero:                 # (campo, ticker)
            fechamentos = bruto["Close"]
        elif nivel_zero & set(simbolos):          # (ticker, campo)
            series = {}
            for simbolo in simbolos:
                if simbolo in nivel_zero and "Close" in bruto[simbolo].columns:
                    series[simbolo] = bruto[simbolo]["Close"]
            fechamentos = pd.DataFrame(series) if series else None
        else:
            return None
    elif "Close" in colunas:                      # ticker único
        fechamentos = bruto[["Close"]].rename(columns={"Close": simbolos[0]})
    else:
        fechamentos = bruto

    if isinstance(fechamentos, pd.Series):
        fechamentos = fechamentos.to_frame(name=simbolos[0])
    return fechamentos


def _recomendacao_fii(pvp):
    """FII negociado abaixo do valor patrimonial é o gatilho de entrada."""
    if pvp is None or pvp <= 0:
        return None, None
    if pvp < 1.0:
        return "DESCONTO", "Negociando abaixo do valor patrimonial"
    if pvp <= 1.05:
        return "NEUTRO", "Próximo do valor patrimonial"
    return "ÁGIO", "Negociando acima do valor patrimonial"


def _montar_fiis(tickers, df_precos, fundamentos):
    linhas = []
    for ticker in tickers:
        simbolo = f"{ticker}.SA"
        sub = fatiar_precos(df_precos, simbolo) if df_precos is not None else None
        if sub is None or sub.empty:
            continue
        preco = float(sub["Close"].iloc[-1])

        dados = fundamentos.get(simbolo) or {}
        pvp = dados.get("pvp")
        dy = dados.get("dy")
        origens = ["quoteSummary"] if dados else []
        competencia_vp = None
        vp_cota = None

        # P/VP do Informe Mensal da CVM quando o Yahoo não traz. É o gatilho de
        # entrada do FII: sem ele a linha não tem recomendação nenhuma.
        if pvp is None:
            informe = fundamentos_fii.pvp_do_fii(ticker, preco)
            if informe.get("pvp") is not None:
                pvp = informe["pvp"]
                vp_cota = informe.get("vp_por_cota")
                competencia_vp = informe.get("competencia")
                origens.append(f"CVM {competencia_vp}" if competencia_vp else "CVM")

        # DY pelos proventos que vieram junto do preço: provento pago é fato.
        if dy is None:
            dy_serie = dy_da_serie(sub, preco)
            if dy_serie is not None:
                dy = dy_serie
                origens.append("proventos")

        recomendacao, racional = _recomendacao_fii(pvp)

        linhas.append({
            "ticker": ticker,
            "nome": dados.get("nome") or ticker,
            "preco": round(preco, 2),
            "pvp": round(pvp, 2) if pvp is not None else None,
            "dy": round(dy, 2) if dy is not None else None,
            # Valor patrimonial por cota implícito no P/VP: é o preço em que o
            # fundo negociaria a 1,00x.
            "ponto_entrada": round(vp_cota, 2) if vp_cota else (
                round(preco / pvp, 2) if pvp else None),
            "vp_por_cota": round(vp_cota, 2) if vp_cota else None,
            "competencia_vp": competencia_vp,
            "recomendacao": recomendacao or "SEM DADOS",
            "racional": racional or "P/VP indisponível na fonte",
            "desconto": bool(pvp and 0 < pvp < 1),
            "origem_fundamentos": " + ".join(origens) if origens else None,
            "fundamentos_disponiveis": pvp is not None or dy is not None,
        })
    # None por último: papel sem DY não pode encabeçar um ranking de DY.
    linhas.sort(key=lambda linha: (linha["dy"] is None, -(linha["dy"] or 0.0)))
    return linhas


def _montar_etfs(df_precos):
    linhas = []
    for ticker in ETFS_B3:
        simbolo = f"{ticker}.SA"
        sub = fatiar_precos(df_precos, simbolo) if df_precos is not None else None
        if sub is None or len(sub) < SMA_ETF:
            continue
        fechamentos = sub["Close"]
        preco = float(fechamentos.iloc[-1])
        sma = float(fechamentos.rolling(SMA_ETF).mean().iloc[-1])
        if pd.isna(sma):
            continue
        linhas.append({
            "ticker": ticker,
            "preco": round(preco, 2),
            "ponto_entrada": round(sma, 2),
            "recomendacao": "ABAIXO DA MÉDIA" if preco <= sma else "ACIMA DA MÉDIA",
            "racional": f"Pullback na média de {SMA_ETF} pregões",
        })
    linhas.sort(key=lambda linha: linha["ticker"])
    return linhas


@router.get("/fundos")
def radar_fundos():
    """FIIs por desconto patrimonial e ETFs por pullback na média de 20."""
    todos_fiis = FIIS_TIJOLO + FIIS_PAPEL
    df_fiis = _baixar_precos(todos_fiis)
    df_etfs = _baixar_precos(ETFS_B3)

    fundamentos = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS_INFO) as pool:
        futuros = {pool.submit(_fundamentos_fii, f"{t}.SA"): f"{t}.SA" for t in todos_fiis}
        for futuro in concurrent.futures.as_completed(futuros):
            simbolo = futuros[futuro]
            try:
                dados = futuro.result()
            except Exception:  # noqa: BLE001
                dados = None
            if dados:
                fundamentos[simbolo] = dados

    tijolo = _montar_fiis(FIIS_TIJOLO, df_fiis, fundamentos)
    papel = _montar_fiis(FIIS_PAPEL, df_fiis, fundamentos)
    etfs = _montar_etfs(df_etfs)

    return {
        **carimbo_de_coleta(),
        "tijolo": tijolo,
        "papel": papel,
        "etfs": etfs,
        "com_fundamentos": sum(1 for l in tijolo + papel if l["fundamentos_disponiveis"]),
        "com_pvp": sum(1 for l in tijolo + papel if l["pvp"] is not None),
        "total_fiis": len(tijolo) + len(papel),
    }


@router.get("/otimizar-portfolio")
def otimizar_markowitz(
    tickers: str,
    selic_aa: float = Query(None, description="Taxa livre de risco anual em %; vazio usa a meta Selic do BCB"),
    objetivo: str = Query("sharpe", description="sharpe | minima_variancia | paridade_risco"),
    teto_ativo: float = Query(0.25, ge=0.05, le=1.0, description="Peso máximo por ativo"),
    teto_grupo: float = Query(0.40, ge=0.10, le=1.0, description="Peso máximo por setor ou classe"),
):
    """Otimização com restrição de concentração, encolhimento e comparação com o CDI.

    A versão anterior sorteava 20 mil vetores de peso e ficava com o melhor.
    Isso não é otimização — é loteria em cinco dimensões, e produzia 50% num
    único ativo porque nada impedia. Ver `modules/otimizador.py` para o
    raciocínio completo.

    Três objetivos: `sharpe` (máximo prêmio por unidade de risco),
    `minima_variancia` (não usa retorno esperado, e por isso costuma ir melhor
    fora da amostra) e `paridade_risco` (cada ativo contribui com a mesma
    parcela do risco).
    """
    lista = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    lista = list(dict.fromkeys(lista))
    if len(lista) < 2:
        return {"erro": "Insira pelo menos 2 ativos distintos, separados por vírgula."}

    simbolos = [t if t.endswith(".SA") else f"{t}.SA" for t in lista]

    try:
        bruto = yf.download(simbolos, period="2y", interval="1d",
                            progress=False, auto_adjust=True)
    except Exception as exc:  # noqa: BLE001
        return {"erro": f"Falha ao baixar o histórico: {type(exc).__name__}: {exc}"}

    if bruto is None or bruto.empty:
        return {"erro": "O Yahoo não devolveu histórico para esses ativos."}

    fechamentos = extrair_fechamentos(bruto, simbolos)
    if fechamentos is None or fechamentos.empty:
        return {"erro": "Resposta do Yahoo sem coluna de fechamento."}

    ausentes = [s for s in simbolos if s not in fechamentos.columns]
    fechamentos = fechamentos[[s for s in simbolos if s in fechamentos.columns]]

    # ffill().dropna() antes tolerava qualquer buraco; um ativo listado há
    # poucos meses zerava a interseção e a conta saía sobre quase nada.
    fechamentos = fechamentos.ffill().dropna()
    if len(fechamentos) < DIAS_PIOR_CASO:
        return {"erro": f"Histórico comum insuficiente: {len(fechamentos)} pregões "
                        f"em comum, mínimo de {DIAS_PIOR_CASO}.",
                "ativos_sem_dados": ausentes}
    if fechamentos.shape[1] < 2:
        return {"erro": "Menos de 2 ativos com histórico utilizável.",
                "ativos_sem_dados": ausentes}

    retornos = fechamentos.pct_change().dropna()
    retornos_medios = retornos.mean() * 252
    covariancia = retornos.cov() * 252

    # Coerção em vez de `is None`: chamada direta (teste, script) recebe o
    # objeto Query como default, que não é None e passaria batido.
    try:
        selic_informada = float(selic_aa)
    except (TypeError, ValueError):
        selic_informada = None

    if selic_informada is None:
        selic = taxas.obter_selic_meta()
        selic_aa, origem_selic, data_selic = selic["valor"], selic["origem"], selic["data"]
    else:
        selic_aa, origem_selic, data_selic = selic_informada, "parametro", None
    taxa_livre = float(selic_aa) / 100.0
    n = fechamentos.shape[1]

    # Chamada direta (teste, script) recebe o objeto Query como default, que
    # não é None e passaria batido — o mesmo defeito que já fez `bool(Query(False))`
    # valer True neste projeto. Coerção explícita, sempre.
    def _texto(valor, padrao):
        return valor.strip().lower() if isinstance(valor, str) and valor.strip() else padrao

    def _fracao(valor, padrao):
        try:
            numero = float(valor)
        except (TypeError, ValueError):
            return padrao
        return numero if 0.0 < numero <= 1.0 else padrao

    objetivo = _texto(objetivo, "sharpe")
    teto_ativo = _fracao(teto_ativo, 0.25)
    teto_grupo = _fracao(teto_grupo, 0.40)

    grupos = [identidade.grupo_de_concentracao(c.replace(".SA", ""))
              for c in fechamentos.columns]

    resultado = otimizador.otimizar(
        retornos_medios.to_numpy(), covariancia.to_numpy(), taxa_livre,
        objetivo=objetivo, teto=teto_ativo, teto_grupo=teto_grupo, grupos=grupos,
    )
    pesos = resultado["pesos"]

    alocacao = [{
        "ativo": col.replace(".SA", ""),
        "peso_pct": round(float(p) * 100, 2),
        "grupo": g,
        # Onde o RISCO está, que quase nunca é onde o peso está: um ativo com
        # 20% de peso e 45% do risco é a informação que falta na maioria das
        # telas de alocação.
        "risco_pct": round(float(r) * 100, 2),
    } for col, p, g, r in zip(fechamentos.columns, pesos, grupos,
                              resultado["contribuicao_risco"])]
    alocacao.sort(key=lambda item: item["peso_pct"], reverse=True)

    curva = otimizador.fronteira(
        otimizador.encolher_retornos(retornos_medios.to_numpy()),
        otimizador.encolher_covariancia(covariancia.to_numpy()),
        teto=teto_ativo, teto_grupo=teto_grupo, grupos=grupos)

    # Teto de grupo que FORÇA alocação, em vez de limitá-la: com poucos grupos
    # o limite dos outros vira piso deste. Precisa ser dito antes que o
    # usuário conclua que o otimizador gostou do ativo.
    forcados = resultado["minimos_forcados_por_grupo"]
    concentracao = []
    if forcados:
        detalhe = ", ".join(f"{g} ≥ {v:.0f}%" for g, v in forcados.items())
        concentracao.append(
            f"Só há {resultado['grupos_distintos']} grupos distintos nesta lista. "
            f"Com teto de {resultado['teto_por_grupo'] * 100:.0f}% por grupo, o limite "
            f"dos outros vira piso deste: {detalhe}. O peso não é escolha do "
            "otimizador — é imposição da restrição. Acrescente ativos de outros "
            "setores ou afrouxe o teto.")

    # Peso pequeno com risco grande é o que a tabela de pesos esconde.
    for linha in alocacao:
        if linha["risco_pct"] >= 40.0 and linha["peso_pct"] <= 25.0:
            concentracao.append(
                f"{linha['ativo']} tem {linha['peso_pct']:.0f}% do peso mas "
                f"{linha['risco_pct']:.0f}% do risco da carteira. Diversificação de "
                "peso não é diversificação de risco.")

    sharpe = resultado["sharpe"]
    if not resultado["supera_cdi"]:
        veredito = (
            f"Esta carteira entrega Sharpe de {sharpe:.2f}. Com a taxa livre de risco "
            f"em {float(selic_aa):.2f}% a.a., o prêmio pelo risco assumido é pequeno "
            "demais para justificar a carteira: o CDI puro entrega retorno parecido "
            "sem volatilidade. Otimizar a combinação não resolve — o problema é que "
            "os ativos escolhidos não pagam o risco no cenário de juros atual.")
    elif sharpe < 0.5:
        veredito = (f"Sharpe de {sharpe:.2f}: a carteira supera o CDI, mas com margem "
                    "modesta. Vale comparar com a alternativa de renda fixa antes de montar.")
    else:
        veredito = (f"Sharpe de {sharpe:.2f}: a combinação entrega prêmio relevante "
                    "sobre a taxa livre de risco no período analisado.")

    return {
        **carimbo_de_coleta(),
        "objetivo": resultado["objetivo"],
        "retorno_esperado_aa": round(resultado["retorno_esperado"] * 100, 2),
        "retorno_historico_bruto_aa": round(resultado["retorno_historico_bruto"] * 100, 2),
        "volatilidade_aa": round(resultado["volatilidade"] * 100, 2),
        "sharpe_ratio": round(sharpe, 2),
        "supera_cdi": resultado["supera_cdi"],
        "veredito": veredito,
        "taxa_livre_risco_aa": round(float(selic_aa), 2),
        "origem_taxa_livre_risco": origem_selic,
        "vigencia_taxa_livre_risco": data_selic,
        "teto_por_ativo_pct": round(resultado["teto_por_ativo"] * 100, 1),
        "teto_por_grupo_pct": round(resultado["teto_por_grupo"] * 100, 1),
        "peso_por_grupo": resultado["peso_por_grupo"],
        "encolhimento_aplicado": resultado["encolhimento_aplicado"],
        "pregoes_utilizados": int(len(fechamentos)),
        "ativos_sem_dados": ausentes,
        "alocacao_otima": alocacao,
        "fronteira": curva,
        "alertas_concentracao": concentracao,
        "minimos_forcados_por_grupo": forcados,
        "ressalvas": [
            "Retornos esperados encolhidos 50% na direção da média do conjunto: "
            "média histórica é estimador ruim, e otimizar sobre ela concentra a "
            "carteira no ativo que mais subiu na janela.",
            f"Teto de {resultado['teto_por_ativo'] * 100:.0f}% por ativo e "
            f"{resultado['teto_por_grupo'] * 100:.0f}% por setor ou classe. Sem teto "
            "de setor, quatro bancos a 25% viram 100% em bancos.",
            "Fronteira e carteira usam as MESMAS restrições — comparar solução "
            "restrita com fronteira irrestrita compararia coisas diferentes.",
        ],
    }
