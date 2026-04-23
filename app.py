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
    # CORREÇÕES MANUAIS (casos muito específicos)
    # --------------------------
    correcoes_manuais = {
        "Piranga Mg": "Piranga",
        "Martinho Campos - Distrito Ibitira": "Martinho Campos",
        "Rua José Bahia Capanema, 440. Bairro Papa João Paulo Ii": "Pará De Minas"
    }
    df["Cidade"] = df["Cidade"].replace(correcoes_manuais)

    # --------------------------
    # FUZZY MATCHING DE CIDADES
    # --------------------------
    # Cria lista de cidades canônicas (as mais frequentes ganham prioridade)
    df["Cidade"] = normalizar_cidades_fuzzy(df["Cidade"])

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

    return df


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
    df = carregar_dados()
    dados_ok = True
except FileNotFoundError:
    dados_ok = False
    st.error("Arquivo **dados.csv** não encontrado. Coloque-o na mesma pasta que este app.py.")

# --------------------------
# NAVEGAÇÃO
# --------------------------
if dados_ok:
    pagina = st.sidebar.radio(
        "Navegação",
        ["📊 Alunos por Ano", "🗺️ Alunos por Cidade", "🎯 Mostra 2025", "📋 Dados Gerais"],
        index=0
    )

    # ==============================
    # PÁGINA 2 — ALUNOS POR ANO
    # ==============================
    if pagina == "📊 Alunos por Ano":
        st.title("📊 Alunos por Ano")

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

            fig = px.bar(
                df_ano,
                x="Ano",
                y="Total_Alunos",
                text="Total_Alunos",
                title="Total de Alunos por Ano",
                labels={"Ano": "Ano", "Total_Alunos": "Total de Alunos"},
                color="Total_Alunos",
                color_continuous_scale="Blues",
            )
            fig.update_traces(textposition="outside")
            fig.update_layout(
                coloraxis_showscale=False,
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(
                    tickmode="array",
                    tickvals=anos_ticks,
                    ticktext=[str(a) for a in anos_ticks],
                    dtick=1,
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
    # PÁGINA 2 — ALUNOS POR CIDADE
    # ==============================
    elif pagina == "🗺️ Alunos por Cidade":
        st.title("🗺️ Alunos por Cidade")

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

    # ==============================
    # PÁGINA 3 — DADOS GERAIS
    # ==============================
    elif pagina == "📋 Dados Gerais":
        st.title("📋 Dados Gerais")

        col1, col2 = st.columns(2)
        col1.metric("Total de registros", len(df))
        if "Qtd_Alunos" in df.columns:
            col2.metric("Total de alunos", int(df["Qtd_Alunos"].sum()))

        st.subheader("Tabela completa")
        st.dataframe(df, use_container_width=True)

    # ==============================
    # PÁGINA 4 — MOSTRA 2025
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