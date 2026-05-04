import pandas as pd
import streamlit as st
import plotly.express as px
from rapidfuzz import process, fuzz
import folium
from folium.plugins import HeatMap
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import time

# --------------------------
# CONFIGURAÇÃO DA PÁGINA
# --------------------------

st.set_page_config(
    page_title="Visitas Escolares",
    page_icon="🏫",
    layout="wide"
)

# --------------------------
# CARREGAMENTO DOS DADOS
# --------------------------

@st.cache_data
def carregar_dados():
    df = pd.read_csv("dados.csv")

    # --------------------------
    # PADRONIZAÇÃO GERAL
    # --------------------------

    # remover espaços extras dos nomes das colunas
    df.columns = df.columns.str.strip()

    # padronizar cidades: strip, title case, espaços extras
    df["Cidade"] = (
        df["Cidade"]
        .astype(str)
        .str.strip()
        .str.title()
        .str.replace(r"\s+", " ", regex=True)
    )

    # --------------------------
    # LIMPEZA GENÉRICA DE SUFIXOS DE ESTADO
    # Remove qualquer variante de " MG", "/ MG", ", MG", " - MG" no final.
    # Ex: "Betim, Mg" -> "Betim" | "Betim - Mg" -> "Betim" | "Betim/Mg" -> "Betim"
    # --------------------------
    df["Cidade"] = (
        df["Cidade"]
        .str.replace(r"[\s,/\-]+Mg\s*$", "", regex=True)
        .str.strip()
    )

    # --------------------------
    # CORREÇÕES MANUAIS (casos específicos que não são cobertos pelo regex acima)
    # Aplicadas ANTES e DEPOIS do fuzzy para garantir cobertura total.
    # --------------------------
    correcoes_manuais = {
        # distritos / grafias alternativas
        "Martinho Campos - Distrito Ibitira": "Martinho Campos",
        "Ibitira - Martinho Campos":          "Martinho Campos",
        # endereço lançado no lugar da cidade
        "Rua José Bahia Capanema, 440. Bairro Papa João Paulo Ii": "Pará De Minas",
    }

    def aplicar_correcoes(serie, mapa):
        """Aplica o dicionário de correções de forma case-insensitive."""
        mapa_lower = {k.lower(): v for k, v in mapa.items()}
        return serie.apply(
            lambda x: mapa_lower.get(str(x).strip().lower(), x)
        )

    df["Cidade"] = aplicar_correcoes(df["Cidade"], correcoes_manuais)

    # --------------------------
    # FUZZY MATCHING DE CIDADES
    # --------------------------
    # Cria lista de cidades canônicas (as mais frequentes ganham prioridade)
    df["Cidade"] = normalizar_cidades_fuzzy(df["Cidade"])

    # Aplica limpeza de sufixos e correções manuais novamente após fuzzy,
    # caso o fuzzy tenha reintroduzido variantes
    df["Cidade"] = (
        df["Cidade"]
        .str.replace(r"[\s,/\-]+Mg\s*$", "", regex=True)
        .str.strip()
    )
    df["Cidade"] = aplicar_correcoes(df["Cidade"], correcoes_manuais)

    # --------------------------
    # RENOMEAR COLUNAS
    # --------------------------
    rename_map = {}
    if "Horário da visita:" in df.columns:
        rename_map["Horário da visita:"] = "Horario"
    if "Número de alunos:" in df.columns:
        rename_map["Número de alunos:"] = "Qtd_Alunos"
    if "Número de alunos do ensino medio" in df.columns:
        rename_map["Número de alunos do ensino medio"] = "Qtd_EM"
    if "Número de alunos do ensino fundamental" in df.columns:
        rename_map["Número de alunos do ensino fundamental"] = "Qtd_EF"
    df = df.rename(columns=rename_map)

    # --------------------------
    # CONVERTER TIPOS
    # --------------------------
    if "Qtd_Alunos" in df.columns:
        df["Qtd_Alunos"] = pd.to_numeric(df["Qtd_Alunos"], errors="coerce")
    if "Qtd_EM" in df.columns:
        df["Qtd_EM"] = pd.to_numeric(df["Qtd_EM"], errors="coerce")
    if "Qtd_EF" in df.columns:
        df["Qtd_EF"] = pd.to_numeric(df["Qtd_EF"], errors="coerce")

    # --------------------------
    # PREENCHER Qtd_Alunos ZERADA/NULA COM EF + EM
    # --------------------------
    # Quando "Número de alunos:" está 0 ou vazio (ex: registros de 2025),
    # recalcula o total somando ensino fundamental + ensino médio.
    if "Qtd_Alunos" in df.columns and "Qtd_EF" in df.columns and "Qtd_EM" in df.columns:
        soma_ef_em = df["Qtd_EF"].fillna(0) + df["Qtd_EM"].fillna(0)
        mask_vazio = df["Qtd_Alunos"].isna() | (df["Qtd_Alunos"] == 0)
        df.loc[mask_vazio, "Qtd_Alunos"] = soma_ef_em[mask_vazio]

    # --------------------------
    # PADRONIZAÇÃO DE ESCOLAS
    # --------------------------
    # Mapeamento de variações para o nome padronizado
    padronizacao_escolas = {
        # EE Serafim Ribeiro de Rezende
        "ee serafim ribeiro de rezende": "EE Serafim Ribeiro de Rezende",
        "ee serafim ribeiro de rezende": "EE Serafim Ribeiro de Rezende",
        
        # EE Joaquim Nabuco
        "ee joaquim nabuco": "EE Joaquim Nabuco",
        "e e joaquim nabuco": "EE Joaquim Nabuco",
        
        # Centro de Referência do Professor
        "centro de referência do professor": "Centro de Referência do Professor",
        "secretaria municipal de educação - (centro de referência do professor)": "Centro de Referência do Professor",
        
        # Escola Padre João Parreiras Villaça
        "escola padre joão parreiras villaça": "Escola Padre João Parreiras Villaça",
        "escola estadual padre joão parreiras villaça": "Escola Padre João Parreiras Villaça",
        
        # Escola Municipal Regina Célia de Oliveira Costa
        "escola municipal regina célia de oliveira costa": "Escola Municipal Regina Célia de Oliveira Costa",
        '"escola municipal "regina célia de oliveira costa""': "Escola Municipal Regina Célia de Oliveira Costa",
        
        # EM Raul Saraiva Ribeiro
        "em raul saraiva ribeiro": "EM Raul Saraiva Ribeiro",
        "e m raul saraiva ribeiro": "EM Raul Saraiva Ribeiro",
        "e.m.raul saraiva ribeiro": "EM Raul Saraiva Ribeiro",
        
        # Escola Municipal Sebastião Ferreira de Oliveira
        "escola municipal sebastião ferreira de oliveira": "Escola Municipal Sebastião Ferreira de Oliveira",
        "escola municipal sebastião ferreira de oliveira": "Escola Municipal Sebastião Ferreira de Oliveira",
        
        # Escola Estadual Santo Tomaz de Aquino
        "escola estadual santo tomaz de aquino": "Escola Estadual Santo Tomaz de Aquino",
        "escola estadual santo tomaz de aquino": "Escola Estadual Santo Tomaz de Aquino",
        
        # Colégio Tiradentes da PMMG (Unidade Bom Despacho)
        "colégio tiradentes da pmmg (unidade bom despacho)": "Colégio Tiradentes da PMMG (Unidade Bom Despacho)",
        "colégio tiradentes da pmmg unidade de bom despacho": "Colégio Tiradentes da PMMG (Unidade Bom Despacho)",
        "colégio tiradentes da pmmg - unidade de bom despacho": "Colégio Tiradentes da PMMG (Unidade Bom Despacho)",
        "colégio tiradentes da pmmg - unidade bom despacho": "Colégio Tiradentes da PMMG (Unidade Bom Despacho)",
        
        # Escola Municipal Padre Moacir Cândido Rodrigues
        "escola municipal padre moacir cândido rodrigues": "Escola Municipal Padre Moacir Cândido Rodrigues",
        "escola municipal padre moacir cândido rodrigues": "Escola Municipal Padre Moacir Cândido Rodrigues",
        
        # Escola Municipal Geraldo Jorge Meira
        "escola municipal geraldo jorge meira": "Escola Municipal Geraldo Jorge Meira",
        "escola municipal geraldo jorge meira": "Escola Municipal Geraldo Jorge Meira",
        
        # Escola Municipal Maria Cândida de Jesus
        "escola municipal maria cândida de jesus": "Escola Municipal Maria Cândida de Jesus",
        "em maria cândida de jesus": "Escola Municipal Maria Cândida de Jesus",
        
        # EE Leonardo Gonçalves Nogueira
        "ee leonardo gonçalves nogueira": "EE Leonardo Gonçalves Nogueira",
        "ee leonardo gonçalves nogueira": "EE Leonardo Gonçalves Nogueira",
        
        # E.E. Hermenegildo Vilaça
        "e.e. hermenegildo vilaça": "EE Hermenegildo Vilaça",
        "e.e. hermenegildo vilaça": "EE Hermenegildo Vilaça",
        
        # EE Dr. José Gonçalves
        "ee dr. josé gonçalves": "EE Dr. José Gonçalves",
        "e.e. dr. josé gonçalves": "EE Dr. José Gonçalves",
        "ee dr josé gonçalves": "EE Dr. José Gonçalves",
        
        # EE Domingos Justino Ribeiro
        "ee domingos justino ribeiro": "EE Domingos Justino Ribeiro",
        "escola estadual domingos justino ribeiro": "EE Domingos Justino Ribeiro",
        
        # Escola Estadual Fernando Otávio
        "escola estadual fernando otávio": "Escola Estadual Fernando Otávio",
        "escola estadual fernando otavio": "Escola Estadual Fernando Otávio",
        
        # Escola Estadual Joaquim Corrêa
        "escola estadual joaquim corrêa": "Escola Estadual Joaquim Corrêa",
        "escola estadual joaquim correa": "Escola Estadual Joaquim Corrêa",
        
        # EE Melo Viana
        "ee melo viana": "EE Melo Viana",
        "escola estadual melo viana": "EE Melo Viana",
        
        # EM José Pires Montes
        "em josé pires montes": "EM José Pires Montes",
        "e.m. josé pires montes": "EM José Pires Montes",
        "em jose pires montes": "EM José Pires Montes",
        "escola municipal josé pires montes": "EM José Pires Montes",
        
        # Escola Municipal Conceição Maria Moreira
        "escola municipal conceição maria moreira": "Escola Municipal Conceição Maria Moreira",
        "em conceição maria moreira": "Escola Municipal Conceição Maria Moreira",
        
        # Escola Estadual Emília Cerdeira
        "escola estadual emília cerdeira": "Escola Estadual Emília Cerdeira",
        "e.e. emília cerdeira": "Escola Estadual Emília Cerdeira",
        "escola estadual emília cerdeira": "Escola Estadual Emília Cerdeira",
        
        # Escola Municipal José Antônio Júnior
        "escola municipal josé antônio júnior": "Escola Municipal José Antônio Júnior",
        "e. m. jose antonio junior": "Escola Municipal José Antônio Júnior",
        
        # Escola Municipal Dona Vina
        "escola municipal dona vina": "Escola Municipal Dona Vina",
        "escola municipal dona vina": "Escola Municipal Dona Vina",
        
        # Escola Municipal Maria Renilda Ferreira
        "escola municipal maria renilda ferreira": "Escola Municipal Maria Renilda Ferreira",
        "escola municipal maria renilda ferreira": "Escola Municipal Maria Renilda Ferreira",
        
        # Escola Municipal Maria Luzia de Andrade
        "escola municipal maria luzia de andrade": "Escola Municipal Maria Luzia de Andrade",
        "escola municipal maria luzia de andrade": "Escola Municipal Maria Luzia de Andrade",
        
        # Escola Municipal José Vilaça Guimarães
        "escola municipal josé vilaça guimarães": "Escola Municipal José Vilaça Guimarães",
        "e m josé vilaça guimarães": "Escola Municipal José Vilaça Guimarães",
        
        # Escola Municipal Manuel Antônio dos Santos
        "escola municipal manuel antônio dos santos": "Escola Municipal Manuel Antônio dos Santos",
        "escola municipal manuel antonio dos santos": "Escola Municipal Manuel Antônio dos Santos",
        
        # Escola Municipal Marechal Deodoro
        "escola municipal marechal deodoro": "Escola Municipal Marechal Deodoro",
        "em marechal deodoro": "Escola Municipal Marechal Deodoro",
    }
    
    # Identificar coluna de escola
    col_escola = None
    possiveis_nomes_escola = ["escola", "school", "instituição", "instituicao", "nome da escola", "colegio", "colégio"]
    for col in df.columns:
        col_lower = col.lower().strip()
        if any(p in col_lower for p in possiveis_nomes_escola):
            col_escola = col
            break
    
    # Aplicar padronização se a coluna existir
    if col_escola is not None:
        df[col_escola] = df[col_escola].astype(str).str.strip()
        # Aplicar o mapeamento
        df[col_escola] = df[col_escola].apply(
            lambda x: padronizacao_escolas.get(x.lower().strip(), x)
        )

    # Extrair ano a partir de colunas de data, se existir
    # Prioridade: colunas de data/datetime -> coluna "Ano" numérica -> coluna Horario
    colunas_data = [c for c in df.columns if "data" in c.lower() or "date" in c.lower()]
    colunas_ano  = [c for c in df.columns if c.lower() == "ano"]

    if colunas_data:
        # Coluna contém datas completas — parsear normalmente
        col_data = colunas_data[0]
        df["Data_Parsed"] = pd.to_datetime(df[col_data], dayfirst=True, errors="coerce")
        df["Ano"] = df["Data_Parsed"].dt.year
    elif colunas_ano:
        # Coluna já contém o ano como inteiro (ex: 2023, 2024, 2025)
        col_ano = colunas_ano[0]
        anos_numericos = pd.to_numeric(df[col_ano], errors="coerce")
        if anos_numericos.between(1900, 2100).any():
            # É realmente um ano — usar direto
            df["Ano"] = anos_numericos.astype("Int64")
        else:
            # Pode ser data em texto — tentar parsear
            df["Data_Parsed"] = pd.to_datetime(df[col_ano], dayfirst=True, errors="coerce")
            df["Ano"] = df["Data_Parsed"].dt.year
    elif "Horario" in df.columns:
        df["Data_Parsed"] = pd.to_datetime(df["Horario"], dayfirst=True, errors="coerce")
        df["Ano"] = df["Data_Parsed"].dt.year

    return df, col_escola


def normalizar_cidades_fuzzy(serie: pd.Series, threshold: int = 88) -> pd.Series:
    """
    Agrupa nomes de cidades semelhantes usando fuzzy matching.
    Cidades com similaridade >= threshold são unificadas sob o
    nome canônico (o mais frequente do grupo).

    Parameters
    ----------
    serie     : pd.Series com nomes de cidades já pré-processados
    threshold : pontuação mínima de similaridade (0-100)
    """
    contagem = serie.value_counts()
    cidades = contagem.index.tolist()  # ordenadas por frequência (maior primeiro)

    mapeamento = {}       # cidade_original -> cidade_canônica
    canonicas = []        # lista de cidades já "fixadas" como canônicas

    for cidade in cidades:
        if cidade in mapeamento:
            continue  # já foi mapeada

        # busca a melhor correspondência entre as canônicas já fixadas
        if canonicas:
            resultado = process.extractOne(
                cidade,
                canonicas,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=threshold
            )
        else:
            resultado = None

        if resultado:
            # cidade é variação de uma canônica existente
            mapeamento[cidade] = resultado[0]
        else:
            # cidade vira nova canônica
            mapeamento[cidade] = cidade
            canonicas.append(cidade)

    return serie.map(mapeamento)


# --------------------------
# CARREGAR
# --------------------------
try:
    df, col_escola = carregar_dados()
    dados_ok = True
except FileNotFoundError:
    dados_ok = False
    col_escola = None
    st.error("Arquivo **dados.csv** não encontrado. Coloque-o na mesma pasta que este app.py.")

# --------------------------
# NAVEGAÇÃO
# --------------------------
if dados_ok:
    pagina = st.sidebar.radio(
        "Navegação",
        ["📊 Alunos por Ano", "🏫 Escolas por ano", "🗺️ Alunos por Cidade", "🏫 Alunos por Escola", "🎯 Mostra 2025", "📋 Dados Gerais"],
        index=0
    )

    # ==============================
    # PÁGINA 1 — ALUNOS POR ANO
    # ==============================
    if pagina == "📊 Alunos por Ano":
        st.title("📊 Alunos por Ano")

        if "Qtd_Alunos" in df.columns:
            total_geral = int(df["Qtd_Alunos"].sum())
            st.metric("Total de alunos (todos os anos)", total_geral)

        if "Ano" not in df.columns or df["Ano"].isna().all():
            st.warning(
                "Nenhuma coluna de data foi encontrada no CSV. "
                "Adicione uma coluna com datas (ex: 'Data da visita') para ver este gráfico."
            )
        elif "Qtd_Alunos" not in df.columns:
            st.warning("Coluna de quantidade de alunos não encontrada.")
        else:
            df_ano = (
                df.groupby("Ano", dropna=True)["Qtd_Alunos"]
                .sum()
                .reset_index()
                .rename(columns={"Qtd_Alunos": "Total_Alunos"})
                .sort_values("Ano")
            )
            df_ano["Ano"] = df_ano["Ano"].astype(int)
            anos_ticks = df_ano["Ano"].tolist()

            # Paleta de azuis bem distintos — um tom por barra
            BLUES_PALETTE = [
                "#03045e",  # azul-marinho profundo
                "#0077b6",  # azul-oceano
                "#00b4d8",  # ciano-azulado
                "#48cae4",  # azul-claro vibrante
                "#023e8a",  # índigo-escuro
                "#0096c7",  # azul-médio
                "#90e0ef",  # azul-gelo
                "#caf0f8",  # azul muito claro
                "#4361ee",  # azul-elétrico
                "#4895ef",  # azul-royal
            ]
            n_anos = len(df_ano)
            cores_anos = {
                str(ano): BLUES_PALETTE[i % len(BLUES_PALETTE)]
                for i, ano in enumerate(df_ano["Ano"].tolist())
            }
            df_ano["Ano_str"] = df_ano["Ano"].astype(str)

            fig = px.bar(
                df_ano,
                x="Ano_str",
                y="Total_Alunos",
                text="Total_Alunos",
                title="Total de Alunos por Ano",
                labels={"Ano_str": "Ano", "Total_Alunos": "Total de Alunos"},
                color="Ano_str",
                color_discrete_map=cores_anos,
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                showlegend=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    title="Ano",
                    categoryorder="array",
                    categoryarray=[str(a) for a in anos_ticks],
                ),
                yaxis=dict(gridcolor="rgba(0,0,0,0.08)")
            )
            st.plotly_chart(fig, use_container_width=True)

            # Linha de tendência
            if len(df_ano) > 2:
                fig2 = px.line(
                    df_ano,
                    x="Ano",
                    y="Total_Alunos",
                    markers=True,
                    title="Tendência de Alunos ao Longo dos Anos",
                    labels={"Ano": "Ano", "Total_Alunos": "Total de Alunos"},
                )
                fig2.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(
                        tickmode="array",
                        tickvals=anos_ticks,
                        ticktext=[str(a) for a in anos_ticks],
                        dtick=1,
                    ),
                )
                st.plotly_chart(fig2, use_container_width=True)

            st.subheader("Tabela resumo")
            st.dataframe(df_ano, use_container_width=True)

    # ==============================
    # PÁGINA 2 — ESCOLAS POR ANO
    # ==============================
    elif pagina == "🏫 Escolas por ano":
        st.title("🏫 Escolas por ano")

        if col_escola is None:
            st.warning("Nenhuma coluna de escola foi encontrada no CSV. Adicione uma coluna como 'Escola' ou 'Instituição' para visualizar esta página.")
        elif "Ano" not in df.columns or df["Ano"].isna().all():
            st.warning(
                "Nenhuma coluna de data foi encontrada no CSV. "
                "Adicione uma coluna com datas (ex: 'Data da visita') para ver este gráfico."
            )
        else:
            # Contagem de escolas únicas por ano
            df_escolas_ano = (
                df.dropna(subset=[col_escola, "Ano"])
                .groupby("Ano")[col_escola]
                .nunique()
                .reset_index()
                .rename(columns={col_escola: "Total_Escolas"})
                .sort_values("Ano")
            )
            
            if df_escolas_ano.empty:
                st.info("Nenhum dado disponível para exibir o gráfico de escolas por ano.")
            else:
                df_escolas_ano["Ano"] = df_escolas_ano["Ano"].astype(int)
                anos_ticks = df_escolas_ano["Ano"].tolist()
                
                # Métrica: total de escolas únicas no período
                total_escolas = df[col_escola].nunique()
                st.metric("Total de escolas únicas (todos os anos)", total_escolas)
                
                # Paleta de azuis (mesma paleta da página de alunos por ano)
                BLUES_PALETTE = [
                    "#03045e", "#0077b6", "#00b4d8", "#48cae4", "#023e8a",
                    "#0096c7", "#90e0ef", "#caf0f8", "#4361ee", "#4895ef"
                ]
                cores_anos = {
                    str(ano): BLUES_PALETTE[i % len(BLUES_PALETTE)]
                    for i, ano in enumerate(df_escolas_ano["Ano"].tolist())
                }
                df_escolas_ano["Ano_str"] = df_escolas_ano["Ano"].astype(str)
                
                fig = px.bar(
                    df_escolas_ano,
                    x="Ano_str",
                    y="Total_Escolas",
                    text="Total_Escolas",
                    title="Número de Escolas por Ano",
                    labels={"Ano_str": "Ano", "Total_Escolas": "Número de Escolas"},
                    color="Ano_str",
                    color_discrete_map=cores_anos,
                )
                fig.update_traces(textposition="outside")
                fig.update_layout(
                    showlegend=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(
                        title="Ano",
                        categoryorder="array",
                        categoryarray=[str(a) for a in anos_ticks],
                    ),
                    yaxis=dict(gridcolor="rgba(0,0,0,0.08)", tickformat="d")
                )
                st.plotly_chart(fig, use_container_width=True)
                
                # Linha de tendência (se houver mais de 2 anos)
                if len(df_escolas_ano) > 2:
                    fig2 = px.line(
                        df_escolas_ano,
                        x="Ano",
                        y="Total_Escolas",
                        markers=True,
                        title="Tendência de Escolas ao Longo dos Anos",
                        labels={"Ano": "Ano", "Total_Escolas": "Número de Escolas"},
                    )
                    fig2.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(
                            tickmode="array",
                            tickvals=anos_ticks,
                            ticktext=[str(a) for a in anos_ticks],
                            dtick=1,
                        ),
                        yaxis=dict(tickformat="d")
                    )
                    st.plotly_chart(fig2, use_container_width=True)
                
                st.subheader("Tabela resumo")
                st.dataframe(df_escolas_ano, use_container_width=True)

    # ==============================
    # PÁGINA 3 — ALUNOS POR CIDADE
    # ==============================
    elif pagina == "🗺️ Alunos por Cidade":
        st.title("🗺️ Alunos por Cidade")

        if "Qtd_Alunos" in df.columns:
            total_geral = int(df["Qtd_Alunos"].sum())
            st.metric("Total de alunos (todos os anos)", total_geral)

        if "Qtd_Alunos" not in df.columns:
            st.warning("Coluna de quantidade de alunos não encontrada.")
        else:
            # --------------------------
            # FILTROS — na sidebar com checkboxes estilizados
            # --------------------------
            st.sidebar.markdown("---")
            st.sidebar.markdown("#### Filtros")

            # CSS — checkboxes discretos em azul
            st.markdown("""
            <style>
            /* checkboxes: marca azul, texto cinza-azulado suave */
            section[data-testid="stSidebar"] input[type=checkbox] {
                accent-color: #3b82f6;
                width: 14px; height: 14px;
            }
            section[data-testid="stSidebar"] .stCheckbox > label {
                font-size: 0.82rem;
                color: #64748b;
                gap: 6px;
            }
            section[data-testid="stSidebar"] .stCheckbox > label:hover {
                color: #3b82f6;
            }
            /* labels de grupo */
            .filter-group-label {
                font-size: 0.7rem;
                font-weight: 600;
                letter-spacing: 0.06em;
                text-transform: uppercase;
                color: #93c5fd;
                margin: 10px 0 2px 0;
            }
            </style>
            """, unsafe_allow_html=True)

            def _lbl(text):
                st.sidebar.markdown(f"<p class='filter-group-label'>{text}</p>", unsafe_allow_html=True)

            # Filtro Ano
            if "Ano" in df.columns:
                anos_disp = sorted(df["Ano"].dropna().astype(int).unique().tolist())
                _lbl("Ano")
                anos_sel = [a for a in anos_disp if st.sidebar.checkbox(str(a), value=True, key=f"ano_{a}")]
            else:
                anos_sel = None

            # Filtro Horário
            if "Horario" in df.columns:
                _lbl("Horário da visita")
                h_manha = st.sidebar.checkbox("Manhã", value=True, key="h_manha")
                h_tarde = st.sidebar.checkbox("Tarde", value=True, key="h_tarde")
                horario_sel = []
                if h_manha: horario_sel.append("Manhã")
                if h_tarde: horario_sel.append("Tarde")
            else:
                horario_sel = None

            # --------------------------
            # APLICAR FILTROS
            # --------------------------
            df_f = df.copy()

            if anos_sel is not None and "Ano" in df_f.columns:
                df_f = df_f[df_f["Ano"].astype("Int64").isin(anos_sel)]

            if horario_sel is not None and "Horario" in df_f.columns:
                def match_horario(val, selecao):
                    v = str(val).strip().lower()
                    manha = "manhã" in selecao or "manha" in [s.lower() for s in selecao]
                    tarde = "tarde" in [s.lower() for s in selecao]
                    if "manhã e tarde" in v or "manha e tarde" in v:
                        return manha or tarde
                    if "manhã" in v or "manha" in v:
                        return manha
                    if "tarde" in v:
                        return tarde
                    return True

                df_f = df_f[df_f["Horario"].apply(lambda x: match_horario(x, horario_sel))]

            # --------------------------
            # AGREGAR POR CIDADE
            # --------------------------
            df_cidade = (
                df_f.groupby("Cidade", dropna=True)["Qtd_Alunos"]
                .sum()
                .reset_index()
                .rename(columns={"Qtd_Alunos": "Total_Alunos"})
                .sort_values("Total_Alunos", ascending=False)
                .pipe(lambda d: d[d["Total_Alunos"] > 0])
            )

            # ── Seção 0: Mapa de calor ─────────────────────────────
            st.subheader("Mapa de calor")
            st.caption("Concentração de alunos visitantes por município. Intensidade proporcional ao total de alunos.")

            @st.cache_data(show_spinner="Geocodificando cidades…")
            def geocodificar(cidades: tuple):
                geolocator = Nominatim(user_agent="visitas_escolares_app")
                geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1)
                coords = {}
                for cidade in cidades:
                    try:
                        loc = geocode(f"{cidade}, Minas Gerais, Brasil")
                        if loc:
                            coords[cidade] = (loc.latitude, loc.longitude)
                    except Exception:
                        pass
                return coords

            cidades_unicas = tuple(df_cidade["Cidade"].tolist())
            coords_map = geocodificar(cidades_unicas)

            heat_data = []
            for _, row in df_cidade.iterrows():
                cidade = row["Cidade"]
                if cidade in coords_map:
                    lat, lon = coords_map[cidade]
                    heat_data.append([lat, lon, float(row["Total_Alunos"])])

            if heat_data:
                import math
                m = folium.Map(
                    location=[-19.9, -44.0],
                    zoom_start=7,
                    tiles="CartoDB positron",
                )
                max_val = max(v for _, _, v in heat_data) or 1
                
                # =========================================================
                # 1º PASSO: Desenhar os pontos VERMELHOS (sem visitas) primeiro
                # para que fiquem na camada inferior e não sobreponham os azuis
                # =========================================================
                
                # ── Cidades de MG não presentes no dataset ──────────
                # Lê todas as cidades de MG a partir do arquivo TXT
                @st.cache_data
                def carregar_cidades_mg(caminho="cidades_mg.txt"):
                    cidades = []
                    try:
                        with open(caminho, encoding="utf-8") as f:
                            for linha in f:
                                linha = linha.strip()
                                if not linha:
                                    continue
                                partes = linha.split(",")
                                if len(partes) != 3:
                                    continue
                                nome = partes[0].strip()
                                try:
                                    lat = float(partes[1])
                                    lon = float(partes[2])
                                    cidades.append((nome, lat, lon))
                                except ValueError:
                                    continue
                    except FileNotFoundError:
                        st.warning("Arquivo **cidades_mg.txt** não encontrado. Coloque-o na mesma pasta que app.py.")
                    return cidades

                TODAS_CIDADES_MG = carregar_cidades_mg()

                # Filtra apenas as cidades que NÃO estão no dataset (comparação case-insensitive)
                cidades_no_dataset = {c.strip().lower() for c in cidades_unicas}
                cidades_ausentes_filtradas = [
                    (nome, lat, lon)
                    for nome, lat, lon in TODAS_CIDADES_MG
                    if nome.strip().lower() not in cidades_no_dataset
                ]

                # Desenha os pontos VERMELHOS (sem visitas) primeiro - camada inferior
                for nome, lat, lon in cidades_ausentes_filtradas:
                    folium.CircleMarker(
                        location=[lat, lon],
                        radius=4,
                        color="#fca5a5",
                        weight=0.8,
                        fill=True,
                        fill_color="#fca5a5",
                        fill_opacity=0.35,
                        tooltip=folium.Tooltip(
                            f"<b>{nome}</b><br><i>Sem visitas registradas</i>",
                            style="font-size:13px;font-family:sans-serif;color:#7f1d1d;"
                        ),
                    ).add_to(m)
                
                # =========================================================
                # 2º PASSO: Desenhar os pontos AZUIS (com visitas) por cima
                # =========================================================
                
                for lat, lon, val in heat_data:
                    # raio entre 10 e 40 px em escala logarítmica
                    radius = 10 + 30 * (math.log1p(val) / math.log1p(max_val))
                    # opacidade entre 0.55 e 0.9
                    opacity = 0.55 + 0.35 * (val / max_val)
                    # encontra o nome da cidade para o tooltip
                    cidade_nome = next(
                        (c for c, coord in coords_map.items() if coord == (lat, lon)), ""
                    )
                    folium.CircleMarker(
                        location=[lat, lon],
                        radius=radius,
                        color="#1d4ed8",
                        weight=1.5,
                        fill=True,
                        fill_color="#3b82f6",
                        fill_opacity=opacity,
                        tooltip=folium.Tooltip(
                            f"<b>{cidade_nome}</b><br>{int(val):,} alunos",
                            style="font-size:13px;font-family:sans-serif;"
                        ),
                    ).add_to(m)

                # Legenda com contraste melhorado e mais legível
                legenda_html = """
                <div style="
                    position: fixed; bottom: 30px; left: 30px; z-index: 1000;
                    background: #1e293b; 
                    padding: 12px 18px; 
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0,0,0,0.3); 
                    font-family: 'Segoe UI', sans-serif; 
                    font-size: 13px;
                    backdrop-filter: blur(2px);
                    border-left: 4px solid #3b82f6;
                ">
                    <b style="display:block;margin-bottom:8px;color:#f8fafc;font-size:14px;">📌 Legenda</b>
                    <span style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">
                        <span style="display:inline-block;width:16px;height:16px;border-radius:50%;
                            background:#3b82f6;border:2px solid #60a5fa;box-shadow:0 0 2px rgba(0,0,0,0.3);"></span>
                        <span style="color:#e2e8f0;">Com visitas registradas</span>
                    </span>
                    <span style="display:flex;align-items:center;gap:10px;">
                        <span style="display:inline-block;width:14px;height:14px;border-radius:50%;
                            background:#fca5a5;border:1px solid #f87171;opacity:0.8;"></span>
                        <span style="color:#e2e8f0;">Sem visitas registradas (MG)</span>
                    </span>
                </div>
                """
                m.get_root().html.add_child(folium.Element(legenda_html))

                st_folium(m, use_container_width=True, height=500, returned_objects=[])
            else:
                st.info("Não foi possível geocodificar as cidades. Verifique sua conexão com a internet.")

            st.divider()

            # ── Seção 1: Pizza ─────────────────────────────────────
            st.subheader("Participação por cidade")
            st.caption("Top 7 cidades com maior número de alunos visitantes; demais agrupadas em Outros.")

            top7 = df_cidade.head(7)
            outros_total = df_cidade.iloc[7:]["Total_Alunos"].sum()
            if outros_total > 0:
                outros_row = pd.DataFrame([{"Cidade": "Outros", "Total_Alunos": outros_total}])
                df_pizza = pd.concat([top7, outros_row], ignore_index=True)
            else:
                df_pizza = top7

            fig_pizza = px.pie(
                df_pizza,
                names="Cidade",
                values="Total_Alunos",
                hole=0.4,
                title="Participação por Cidade — Top 7 + Outros",
            )
            fig_pizza.update_traces(
                textposition="inside",
                textinfo="percent+label",
                hovertemplate="%{label}<br>%{value:,} alunos<br>%{percent}",
            )
            fig_pizza.update_layout(
                showlegend=False,
                margin=dict(t=20, b=20, l=20, r=20),
            )
            st.plotly_chart(fig_pizza, use_container_width=True)

            st.divider()

            # ── Seção 2: Barras horizontais ────────────────────────
            st.subheader("Ranking de cidades")
            top_n = st.slider(
                "Número de cidades exibidas",
                min_value=5,
                max_value=max(5, len(df_cidade)),
                value=min(20, len(df_cidade)),
                label_visibility="visible",
            )
            df_top = df_cidade.head(top_n)

            fig_bar = px.bar(
                df_top,
                x="Total_Alunos",
                y="Cidade",
                orientation="h",
                text="Total_Alunos",
                title=f"Ranking de Cidades por Total de Alunos (Top {top_n})",
                labels={"Total_Alunos": "Total de alunos", "Cidade": ""},
                color="Total_Alunos",
                color_continuous_scale="Teal",
            )
            fig_bar.update_traces(textposition="outside")
            fig_bar.update_layout(
                coloraxis_showscale=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)"),
                xaxis=dict(gridcolor="rgba(0,0,0,0.06)"),
                margin=dict(t=10, b=10),
                height=max(400, top_n * 30),
            )
            st.plotly_chart(fig_bar, use_container_width=True)

            st.divider()

            # ── Seção 3: Cidades únicas ────────────────────────────
            st.subheader("Cidades únicas")
            st.caption("Todos os municípios distintos presentes nos dados (após normalização).")
            cidades_unicas_lista = sorted(df_f["Cidade"].dropna().unique().tolist())
            st.write(f"**Total: {len(cidades_unicas_lista)} cidades**")
            st.dataframe(
                pd.DataFrame({"Cidade": cidades_unicas_lista}),
                use_container_width=True,
                hide_index=True,
            )

    # ==============================
    # PÁGINA 4 — ALUNOS POR ESCOLA
    # ==============================
    elif pagina == "🏫 Alunos por Escola":
        st.title("🏫 Alunos por Escola")

        if col_escola is None:
            st.warning("Nenhuma coluna de escola foi encontrada no CSV. Adicione uma coluna como 'Escola' ou 'Instituição' para visualizar esta página.")
        elif "Qtd_Alunos" not in df.columns:
            st.warning("Coluna de quantidade de alunos não encontrada.")
        else:
            # Métricas gerais
            col1, col2, col3 = st.columns(3)
            col1.metric("Total de registros", len(df))
            col2.metric("Total de alunos", int(df["Qtd_Alunos"].sum()))
            col3.metric("Escolas únicas", df[col_escola].nunique())
            
            st.divider()
            
            # =========================================================
            # FILTROS
            # =========================================================
            
            # Filtro de ano (opcional)
            anos_disponiveis = []
            if "Ano" in df.columns:
                anos_disponiveis = sorted(df["Ano"].dropna().astype(int).unique().tolist())
            
            # Filtro de cidade (discreto - multiselect)
            cidades_disponiveis = sorted(df["Cidade"].dropna().unique().tolist())
            
            # Layout dos filtros em colunas
            col_filtros = st.columns([1, 2])
            
            with col_filtros[0]:
                st.subheader("🔍 Filtros")
                
                # Filtro de cidade
                cidades_sel = st.multiselect(
                    "🏙️ Filtrar por cidade",
                    options=cidades_disponiveis,
                    default=[],
                    placeholder="Selecione uma ou mais cidades...",
                    help="Selecione as cidades desejadas. Deixe vazio para mostrar todas."
                )
                
                # Filtro de ano
                if anos_disponiveis:
                    anos_sel = st.multiselect(
                        "📅 Filtrar por ano",
                        options=anos_disponiveis,
                        default=[],
                        placeholder="Selecione um ou mais anos...",
                        help="Selecione os anos desejados. Deixe vazio para mostrar todos."
                    )
                else:
                    anos_sel = []
            
            with col_filtros[1]:
                st.subheader("🔎 Buscar escola")
                busca_escola = st.text_input(
                    "Digite o nome da escola",
                    placeholder="Ex: EE Serafim Ribeiro de Rezende",
                    help="Digite parte do nome da escola para buscar"
                )
            
            # Aplicar filtros
            df_f = df.copy()
            
            # Aplicar filtro de cidade
            if cidades_sel:
                df_f = df_f[df_f["Cidade"].isin(cidades_sel)]
            
            # Aplicar filtro de ano
            if anos_sel and "Ano" in df_f.columns:
                df_f = df_f[df_f["Ano"].astype("Int64").isin(anos_sel)]
            
            # Agrupar por escola para o ranking geral (considerando filtros)
            df_escola_agrupado = (
                df_f.groupby(col_escola, dropna=True)["Qtd_Alunos"]
                .sum()
                .reset_index()
                .rename(columns={col_escola: "Escola", "Qtd_Alunos": "Total_Alunos"})
                .sort_values("Total_Alunos", ascending=False)
                .pipe(lambda d: d[d["Total_Alunos"] > 0])
            )
            
            # =========================================================
            # RESULTADO EM FORMA DE TABELA (busca ou filtros)
            # =========================================================
            
            if busca_escola:
                st.divider()
                st.subheader(f"📚 Resultados da busca por: \"{busca_escola}\"")
                
                # Busca escola (case insensitive)
                escolas_encontradas = df_escola_agrupado[
                    df_escola_agrupado["Escola"].str.contains(busca_escola, case=False, na=False)
                ]
                
                if escolas_encontradas.empty:
                    st.info(f"Nenhuma escola encontrada com o nome \"{busca_escola}\".")
                else:
                    # Tabela com os resultados da busca
                    df_exibicao_busca = escolas_encontradas.copy()
                    df_exibicao_busca["Total_Alunos"] = df_exibicao_busca["Total_Alunos"].apply(lambda x: f"{int(x):,}")
                    
                    st.dataframe(
                        df_exibicao_busca,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Escola": st.column_config.TextColumn("Escola", width="large"),
                            "Total_Alunos": st.column_config.TextColumn("Total de Alunos", width="medium"),
                        }
                    )
                    
                    # Informação adicional sobre quantas escolas foram encontradas
                    st.caption(f"🔍 {len(escolas_encontradas)} escola(s) encontrada(s)")
                
                # Mostrar também a tabela completa abaixo
                st.divider()
                st.subheader("📋 Lista completa de escolas (com filtros aplicados)")
            
            # =========================================================
            # TABELA COMPLETA DE ESCOLAS (sempre exibida)
            # =========================================================
            if df_escola_agrupado.empty:
                st.info("Nenhuma escola encontrada com os filtros selecionados.")
            else:
                # Indicador de quantas escolas estão sendo exibidas
                st.caption(f"Exibindo {len(df_escola_agrupado)} escolas" + 
                          (f" (filtradas por cidade: {', '.join(cidades_sel)})" if cidades_sel else "") +
                          (f" (anos: {', '.join(map(str, anos_sel))})" if anos_sel else ""))
                
                # Campo de busca para filtrar a lista completa
                busca_lista = st.text_input(
                    "🔍 Filtrar lista de escolas",
                    placeholder="Digite parte do nome da escola...",
                    key="busca_lista_escolas"
                )
                
                if busca_lista:
                    df_exibicao = df_escola_agrupado[
                        df_escola_agrupado["Escola"].str.contains(busca_lista, case=False, na=False)
                    ]
                    if df_exibicao.empty:
                        st.info(f"Nenhuma escola encontrada com \"{busca_lista}\"")
                        df_exibicao = pd.DataFrame()
                    else:
                        st.caption(f"🔍 {len(df_exibicao)} escola(s) encontrada(s) com \"{busca_lista}\"")
                else:
                    df_exibicao = df_escola_agrupado
                
                if not df_exibicao.empty:
                    # Formatar números
                    df_exibicao_formatado = df_exibicao.copy()
                    df_exibicao_formatado["Total_Alunos"] = df_exibicao_formatado["Total_Alunos"].apply(lambda x: f"{int(x):,}")
                    
                    st.dataframe(
                        df_exibicao_formatado,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Escola": st.column_config.TextColumn("Escola", width="large"),
                            "Total_Alunos": st.column_config.TextColumn("Total de Alunos", width="medium"),
                        }
                    )
                
                # Botão para baixar dados
                csv = df_escola_agrupado.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Baixar dados (CSV)",
                    data=csv,
                    file_name="alunos_por_escola.csv",
                    mime="text/csv",
                )
                
                # =========================================================
                # GRÁFICO DE RANKING DE ESCOLAS (igual ao de cidades)
                # =========================================================
                st.divider()
                st.subheader("📊 Ranking de Escolas por Total de Alunos")
                
                top_n_escola = st.slider(
                    "Número de escolas exibidas no gráfico",
                    min_value=5,
                    max_value=max(5, len(df_escola_agrupado)),
                    value=min(20, len(df_escola_agrupado)),
                    key="escola_ranking_top_n"
                )
                
                df_top_escola = df_escola_agrupado.head(top_n_escola)
                
                fig_bar_escola = px.bar(
                    df_top_escola,
                    x="Total_Alunos",
                    y="Escola",
                    orientation="h",
                    text="Total_Alunos",
                    title=f"Ranking de Escolas por Total de Alunos (Top {top_n_escola})",
                    labels={"Total_Alunos": "Total de alunos", "Escola": ""},
                    color="Total_Alunos",
                    color_continuous_scale="Blues",
                )
                fig_bar_escola.update_traces(textposition="outside")
                fig_bar_escola.update_layout(
                    coloraxis_showscale=False,
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    yaxis=dict(autorange="reversed", gridcolor="rgba(0,0,0,0)"),
                    xaxis=dict(gridcolor="rgba(0,0,0,0.06)", title="Número de alunos"),
                    margin=dict(t=30, b=10),
                    height=max(400, top_n_escola * 35),
                )
                st.plotly_chart(fig_bar_escola, use_container_width=True)

    # ==============================
    # PÁGINA 5 — MOSTRA 2025
    # ==============================
    elif pagina == "🎯 Mostra 2025":
        st.title("🎯 Mostra 2025")
        st.caption("Visão exclusiva dos dados de visitas registradas em 2025.")

        if "Ano" not in df.columns:
            st.warning("Coluna de ano não encontrada.")
        else:
            df25 = df[df["Ano"].astype("Int64") == 2025].copy()

            if df25.empty:
                st.info("Nenhum registro encontrado para 2025.")
            else:
                # métricas rápidas
                col1, col2, col3 = st.columns(3)
                col1.metric("Registros", len(df25))
                if "Qtd_Alunos" in df25.columns:
                    col2.metric("Total de alunos", int(df25["Qtd_Alunos"].sum()))
                if "Qtd_EF" in df25.columns and "Qtd_EM" in df25.columns:
                    col3.metric("EF + EM", int(df25["Qtd_EF"].fillna(0).sum() + df25["Qtd_EM"].fillna(0).sum()))

                st.divider()

                # ── Gráfico 1: Preferência de turno ───────────────
                st.subheader("Preferência de turno")
                st.caption("Distribuição das visitas por horário registrado em 2025.")

                if "Horario" in df25.columns and "Qtd_Alunos" in df25.columns:

                    # Soma alunos por turno:
                    # "Manhã e Tarde" => alunos entram nos dois turnos
                    # "Manhã e Tarde": metade dos alunos para cada turno,
                    # pois a visita foi dividida entre os dois períodos.
                    turno_alunos = {"Manh\u00e3": 0.0, "Tarde": 0.0}
                    for _, row in df25[["Horario", "Qtd_Alunos"]].dropna().iterrows():
                        v = str(row["Horario"]).strip().lower()
                        qtd = float(row["Qtd_Alunos"]) if pd.notna(row["Qtd_Alunos"]) else 0.0
                        if "manh" in v and "tarde" in v:
                            turno_alunos["Manh\u00e3"] += qtd / 2
                            turno_alunos["Tarde"]  += qtd / 2
                        elif "manh" in v:
                            turno_alunos["Manh\u00e3"] += qtd
                        elif "tarde" in v:
                            turno_alunos["Tarde"] += qtd

                    contagem_turno = pd.DataFrame([
                        {"Turno": "Manh\u00e3", "Alunos": round(turno_alunos["Manh\u00e3"], 1)},
                        {"Turno": "Tarde",  "Alunos": round(turno_alunos["Tarde"], 1)},
                    ])

                    col_a, col_b = st.columns([1, 1])

                    fig_turno_pie = px.pie(
                        contagem_turno, names="Turno", values="Alunos", hole=0.45,
                        color="Turno",
                        color_discrete_map={"Manh\u00e3": "#3b82f6", "Tarde": "#f59e0b"},
                        title="Distribuição de Alunos por Turno",
                    )
                    fig_turno_pie.update_traces(
                        textposition="inside", textinfo="percent+label",
                        hovertemplate="%{label}<br>%{value:,} alunos<br>%{percent}",
                    )
                    fig_turno_pie.update_layout(showlegend=False, margin=dict(t=20, b=20, l=10, r=10))
                    col_a.plotly_chart(fig_turno_pie, use_container_width=True)

                    fig_turno_bar = px.bar(
                        contagem_turno, x="Turno", y="Alunos", text="Alunos",
                        color="Turno",
                        color_discrete_map={"Manh\u00e3": "#3b82f6", "Tarde": "#f59e0b"},
                        title="Total de Alunos por Turno",
                    )
                    fig_turno_bar.update_traces(textposition="outside")
                    fig_turno_bar.update_layout(
                        showlegend=False, plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(title=""),
                        yaxis=dict(gridcolor="rgba(0,0,0,0.06)", title="No de alunos", tickformat="d"),
                        margin=dict(t=30, b=10),
                    )
                    col_b.plotly_chart(fig_turno_bar, use_container_width=True)
                else:
                    st.info("Colunas de horario ou alunos nao encontradas.")

                st.divider()

                # Grafico 2: EF vs EM
                st.subheader("Alunos por nivel de ensino")
                st.caption("Comparativo entre alunos do Ensino Fundamental e Ensino Medio em 2025.")

                if "Qtd_EF" in df25.columns and "Qtd_EM" in df25.columns:
                    total_ef = int(df25["Qtd_EF"].fillna(0).sum())
                    total_em = int(df25["Qtd_EM"].fillna(0).sum())

                    df_nivel = pd.DataFrame({
                        "Nivel": ["Ens. Fundamental", "Ens. Medio"],
                        "Total": [total_ef, total_em],
                    })

                    col_c, col_d = st.columns([1, 1])

                    fig_nivel_pie = px.pie(
                        df_nivel, names="Nivel", values="Total", hole=0.45,
                        color_discrete_sequence=["#6366f1", "#06b6d4"],
                        title="Distribuição por Nível de Ensino",
                    )
                    fig_nivel_pie.update_traces(
                        textposition="inside", textinfo="percent+label",
                        hovertemplate="%{label}<br>%{value:,} alunos<br>%{percent}",
                    )
                    fig_nivel_pie.update_layout(showlegend=False, margin=dict(t=20, b=20, l=10, r=10))
                    col_c.plotly_chart(fig_nivel_pie, use_container_width=True)

                    fig_nivel_bar = px.bar(
                        df_nivel, x="Nivel", y="Total", text="Total",
                        color="Nivel", color_discrete_sequence=["#6366f1", "#06b6d4"],
                        title="Total de Alunos por Nível de Ensino",
                    )
                    fig_nivel_bar.update_traces(textposition="outside")
                    fig_nivel_bar.update_layout(
                        showlegend=False, plot_bgcolor="rgba(0,0,0,0)",
                        xaxis=dict(title=""),
                        yaxis=dict(gridcolor="rgba(0,0,0,0.06)", title="No de alunos", tickformat="d"),
                        margin=dict(t=30, b=10),
                    )
                    col_d.plotly_chart(fig_nivel_bar, use_container_width=True)
                else:
                    st.info("Colunas de EF/EM nao encontradas.")

                st.divider()

                # Grafico 3: Alunos por ano (EF e EM)
                st.subheader("Alunos por ano escolar")
                st.caption("Alunos do EF somados pela coluna Qtd_EF divididos igualmente entre os anos mencionados; idem para EM com Qtd_EM.")

                COL_EF_ANOS = next((c for c in df25.columns if "quais" in c.lower() and "fundamental" in c.lower()), None)
                COL_EM_ANOS = next((c for c in df25.columns if "quais" in c.lower() and ("medio" in c.lower() or "m\u00e9dio" in c.lower())), None)

                ORDEM_EF = ["6\u00ba ano", "7\u00ba ano", "8\u00ba ano", "9\u00ba ano"]
                ORDEM_EM = ["1\u00ba ano", "2\u00ba ano", "3\u00ba ano"]

                # Valores exatos conhecidos no dataset — mapeados para lista de anos
                MAP_EF = {
                    "9\u00ba ano":                               ["9\u00ba ano"],
                    "8\u00ba ano, 9\u00ba ano":                  ["8\u00ba ano", "9\u00ba ano"],
                    "7\u00ba ano, 8\u00ba ano, 9\u00ba ano":     ["7\u00ba ano", "8\u00ba ano", "9\u00ba ano"],
                    "6\u00ba ano, 7\u00ba ano, 8\u00ba ano, 9\u00ba ano": ["6\u00ba ano", "7\u00ba ano", "8\u00ba ano", "9\u00ba ano"],
                }
                MAP_EM = {
                    "1\u00ba ano":                               ["1\u00ba ano"],
                    "2\u00ba ano":                               ["2\u00ba ano"],
                    "3\u00ba ano":                               ["3\u00ba ano"],
                    "1\u00ba ano, 2\u00ba ano":                  ["1\u00ba ano", "2\u00ba ano"],
                    "2\u00ba ano, 3\u00ba ano":                  ["2\u00ba ano", "3\u00ba ano"],
                    "1\u00ba ano, 2\u00ba ano, 3\u00ba ano":     ["1\u00ba ano", "2\u00ba ano", "3\u00ba ano"],
                }
                AUSENCIA_EF = "n\u00e3o levaremos estudantes do ensino fundamental"
                AUSENCIA_EM = "n\u00e3o levaremos estudantes do ensino m\u00e9dio"

                def somar_alunos_por_ano(df_src, col_anos, col_qtd, mapa_anos, ausencia_str, ordem):
                    totais = {ano: 0.0 for ano in ordem}
                    for _, row in df_src[[col_anos, col_qtd]].iterrows():
                        val_ano = str(row[col_anos]).strip() if pd.notna(row[col_anos]) else ""
                        qtd = float(row[col_qtd]) if pd.notna(row[col_qtd]) else 0.0
                        if val_ano.lower() == ausencia_str or qtd == 0:
                            continue
                        anos_lista = mapa_anos.get(val_ano)
                        if anos_lista is None:
                            # fallback: divide por ", "
                            anos_lista = [a.strip() for a in val_ano.split(",") if a.strip() in ordem]
                        if not anos_lista:
                            continue
                        por_ano = qtd / len(anos_lista)
                        for ano in anos_lista:
                            if ano in totais:
                                totais[ano] += por_ano
                    return pd.DataFrame([{"Ano": a, "Alunos": int(round(totais[a]))} for a in ordem])

                has_ef = COL_EF_ANOS is not None and "Qtd_EF" in df25.columns
                has_em = COL_EM_ANOS is not None and "Qtd_EM" in df25.columns

                if has_ef or has_em:
                    col_ef_col, col_em_col = st.columns(2)

                    if has_ef:
                        df_ef_anos = somar_alunos_por_ano(df25, COL_EF_ANOS, "Qtd_EF", MAP_EF, AUSENCIA_EF, ORDEM_EF)
                        fig_ef = px.bar(
                            df_ef_anos, x="Ano", y="Alunos", text="Alunos",
                            title="Ensino Fundamental",
                            color_discrete_sequence=["#6366f1"],
                        )
                        fig_ef.update_traces(textposition="outside", marker_color="#6366f1")
                        fig_ef.update_layout(
                            plot_bgcolor="rgba(0,0,0,0)",
                            xaxis=dict(title="", categoryorder="array", categoryarray=ORDEM_EF),
                            yaxis=dict(gridcolor="rgba(0,0,0,0.06)", title="No de alunos", tickformat="d"),
                            margin=dict(t=40, b=10), showlegend=False,
                        )
                        col_ef_col.plotly_chart(fig_ef, use_container_width=True)

                    if has_em:
                        df_em_anos = somar_alunos_por_ano(df25, COL_EM_ANOS, "Qtd_EM", MAP_EM, AUSENCIA_EM, ORDEM_EM)
                        fig_em = px.bar(
                            df_em_anos, x="Ano", y="Alunos", text="Alunos",
                            title="Ensino M\u00e9dio",
                            color_discrete_sequence=["#06b6d4"],
                        )
                        fig_em.update_traces(textposition="outside", marker_color="#06b6d4")
                        fig_em.update_layout(
                            plot_bgcolor="rgba(0,0,0,0)",
                            xaxis=dict(title="", categoryorder="array", categoryarray=ORDEM_EM),
                            yaxis=dict(gridcolor="rgba(0,0,0,0.06)", title="No de alunos", tickformat="d"),
                            margin=dict(t=40, b=10), showlegend=False,
                        )
                        col_em_col.plotly_chart(fig_em, use_container_width=True)
                else:
                    st.info("Colunas de anos por nivel de ensino nao encontradas.")

    # ==============================
    # PÁGINA 6 — DADOS GERAIS
    # ==============================
    elif pagina == "📋 Dados Gerais":
        st.title("📋 Dados Gerais")

        col1, col2 = st.columns(2)
        col1.metric("Total de registros", len(df))
        if "Qtd_Alunos" in df.columns:
            col2.metric("Total de alunos", int(df["Qtd_Alunos"].sum()))

        st.subheader("Tabela completa")
        st.dataframe(df, use_container_width=True)
