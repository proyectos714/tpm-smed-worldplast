import json
import pandas as pd
import numpy as np
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(
    page_title="TPM-SMED | World Plast",
    page_icon="🏭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# -----------------------------
# Historical baseline validated in the Colab analysis
# Period: July 2024 - June 2025
# These are reference aggregates; live Google Sheets rows are shown separately.
# -----------------------------
HISTORICAL = {
    "period": "Julio 2024 – Junio 2025",
    "programmed_min": 181200.00,
    "downtime_min": 24475.71,
    "maintenance_events": 128,
    "maintenance_min": 10423.34,
    "setup_events": 92,
    "setup_min": 9645.16,
    "environment_events": 30,
    "environment_min": 2235.59,
    "rework_events": 22,
    "rework_min": 2171.62,
    "months": [
        "2024-07", "2024-08", "2024-09", "2024-10",
        "2024-11", "2024-12", "2025-01", "2025-02",
        "2025-03", "2025-04", "2025-05", "2025-06"
    ],
    "availability": [
        89.7484, 91.0251, 90.6858, 87.8194,
        81.7238, 77.5642, 84.6554, 89.0523,
        85.2737, 87.8686, 88.3579, 84.5340
    ],
}
HISTORICAL["operating_min"] = HISTORICAL["programmed_min"] - HISTORICAL["downtime_min"]
HISTORICAL["availability"] = HISTORICAL["operating_min"] / HISTORICAL["programmed_min"] * 100

MOTIVES = pd.DataFrame([
    {"Motivo": "Falta_Mantenimiento", "Eventos": 128, "Minutos": 10423.34, "Acción": "TPM"},
    {"Motivo": "Largos_tiempos_SETUP", "Eventos": 92, "Minutos": 9645.16, "Acción": "SMED"},
    {"Motivo": "Ambiente_Inadecuado", "Eventos": 30, "Minutos": 2235.59, "Acción": "Evaluar condiciones del proceso"},
    {"Motivo": "Reprocesos", "Eventos": 22, "Minutos": 2171.62, "Acción": "Analizar causas de calidad"},
])
MOTIVES_TOTAL = MOTIVES["Minutos"].sum()
MOTIVES["Participación (%)"] = MOTIVES["Minutos"] / MOTIVES_TOTAL * 100
MOTIVES["Acumulado (%)"] = MOTIVES["Participación (%)"].cumsum()

SHEET_ID = "1stqXpgvFta1thfsk8vi6xQOiX-vyuN0O7Q8yo0X9jv4"
WORKSHEET_NAME = "REGISTROS"

@st.cache_data(ttl=300, show_spinner=False)
def read_google_sheet():
    """Read REGISTROS from a published CSV URL or, alternatively, service-account credentials."""
    # Fast path for this deployment: published CSV avoids service-account JSON keys.
    csv_url = st.secrets.get("google_sheets_csv_url", "")
    if csv_url:
        csv_url = str(csv_url).strip()
        is_published_csv = csv_url.startswith("https://docs.google.com/spreadsheets/d/e/") and "output=csv" in csv_url
        is_gviz_csv = csv_url.startswith("https://docs.google.com/spreadsheets/d/") and "/gviz/tq" in csv_url and "tqx=out:csv" in csv_url
        if not (is_published_csv or is_gviz_csv):
            raise RuntimeError(
                "google_sheets_csv_url debe ser la URL CSV publicada o la URL gviz/tq de la pestaña REGISTROS."
            )
        # The sheet has two descriptive rows before the actual column header.
        live_df = pd.read_csv(csv_url, skiprows=2, encoding="utf-8-sig", on_bad_lines="warn")
        live_df.columns = [str(c).replace("\ufeff", "").strip() for c in live_df.columns]
        live_df = live_df.loc[:, ~pd.Index(live_df.columns).astype(str).str.match(r"^Unnamed")]

        # Normalize headers, text, and empty values before counting records.
        live_df.columns = [str(c).replace("\ufeff", "").strip() for c in live_df.columns]
        for col in live_df.select_dtypes(include="object").columns:
            live_df[col] = live_df[col].map(lambda x: x.strip() if isinstance(x, str) else x)
            live_df[col] = live_df[col].replace({"": pd.NA, "None": pd.NA, "none": pd.NA, "NULL": pd.NA, "null": pd.NA, "nan": pd.NA, "NaN": pd.NA})

        # Remove fully empty rows, including rows that only contain spreadsheet placeholders.
        live_df = live_df.dropna(how="all").copy()

        # Identify the event-ID column despite accidental spaces/case differences.
        id_col = next((c for c in live_df.columns if str(c).strip().casefold() == "id_evento"), None)
        if id_col is not None:
            ids = live_df[id_col].fillna("").astype(str).str.strip()
            valid_id = ids.ne("") & ~ids.str.casefold().isin(["nan", "none", "null", "<na>"])
            live_df = live_df[valid_id].copy()
            live_df[id_col] = live_df[id_col].fillna("").astype(str).str.strip()

        # Classify simulations using the whole row, so a shifted/missing column does not hide a test.
        def is_simulated(row):
            values = " ".join(str(v) for v in row.tolist() if pd.notna(v)).upper()
            return (
                "TEST-TPM-" in values
                or "PRUEBA SIMULADA" in values
                or "FICTICIO" in values
                or "SIMULAD" in values
            )

        if not live_df.empty:
            live_df["Clasificacion_Sistema"] = live_df.apply(
                lambda row: "Prueba simulada" if is_simulated(row) else "Pendiente de clasificación",
                axis=1,
            )
        return live_df.reset_index(drop=True), WORKSHEET_NAME

    # Optional fallback for deployments that can use a service account.
    if "gcp_service_account" not in st.secrets:
        raise RuntimeError(
            "Configura google_sheets_csv_url en Streamlit Secrets con el enlace CSV publicado de REGISTROS."
        )
    info = dict(st.secrets["gcp_service_account"])
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet_id = st.secrets.get("google_sheet_id", SHEET_ID)
    worksheet_name = st.secrets.get("google_worksheet", WORKSHEET_NAME)
    worksheet = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)
    records = worksheet.get_all_records()
    return pd.DataFrame(records), worksheet.title

def fmt_num(value, decimals=2):
    return f"{value:,.{decimals}f}"

st.markdown("""
<style>
.block-container {padding-top: 1.5rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background: #f5f7fa; padding: 14px 16px; border-radius: 12px;}
.small-note {font-size: 0.88rem; color: #64748b;}
</style>
""", unsafe_allow_html=True)

st.title("🏭 Sistema analítico TPM–SMED")
st.subheader("World Plast S.R.L. · Línea de extrusión de PVC")
st.caption("Análisis de pérdidas, priorización de intervenciones y simulación de disponibilidad")

with st.sidebar:
    st.header("Navegación")
    page = st.radio(
        "Selecciona una sección",
        ["Resumen ejecutivo", "Pérdidas TPM–SMED", "Simulador TO-BE", "Datos de Google Sheets", "Metodología y límites"],
    )
    st.divider()
    st.caption(f"Base histórica: {HISTORICAL['period']}")
    st.caption("Los escenarios TO-BE son simulaciones, no resultados observados.")

if page == "Resumen ejecutivo":
    st.markdown("### Indicadores históricos AS-IS")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Disponibilidad global", f"{HISTORICAL['availability']:.2f}%")
    c2.metric("Tiempo improductivo", f"{HISTORICAL['downtime_min']:,.2f} min")
    c3.metric("Eventos de mantenimiento", f"{HISTORICAL['maintenance_events']}")
    c4.metric("Eventos de setup", f"{HISTORICAL['setup_events']}")

    left, right = st.columns([1.3, 1])
    with left:
        hist = pd.DataFrame({"Mes": HISTORICAL["months"], "Disponibilidad (%)": HISTORICAL["availability"]})
        fig = px.line(hist, x="Mes", y="Disponibilidad (%)", markers=True,
                      title="Disponibilidad mensual histórica")
        fig.add_hline(y=HISTORICAL["availability"], line_dash="dash",
                      annotation_text=f"Global ponderada: {HISTORICAL['availability']:.2f}%")
        fig.update_layout(yaxis_range=[70, 100], margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        fig2 = px.pie(MOTIVES, names="Motivo", values="Minutos",
                      title="Distribución de pérdidas registradas", hole=0.45)
        fig2.update_layout(margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(fig2, use_container_width=True)

    st.info(
        "La disponibilidad global se calcula como (tiempo programado − tiempo improductivo) / "
        "tiempo programado × 100. El histórico mensual y los registros vivos de Google Sheets "
        "se presentan por separado para no mezclar periodos ni fuentes."
    )

elif page == "Pérdidas TPM–SMED":
    st.markdown("### Priorización de pérdidas")
    st.write("Los dos primeros motivos concentran aproximadamente el 81,99 % del tiempo improductivo registrado en los eventos.")
    left, right = st.columns([1.2, 1])
    with left:
        fig = px.bar(
            MOTIVES.sort_values("Minutos", ascending=True),
            x="Minutos", y="Motivo", orientation="h", text="Minutos",
            title="Tiempo improductivo por motivo"
        )
        fig.update_layout(margin=dict(l=10, r=10, t=55, b=10))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        fig = go.Figure()
        fig.add_trace(go.Bar(x=MOTIVES["Motivo"], y=MOTIVES["Minutos"], name="Minutos"))
        fig.add_trace(go.Scatter(x=MOTIVES["Motivo"], y=MOTIVES["Acumulado (%)"],
                                 name="Acumulado (%)", yaxis="y2", mode="lines+markers"))
        fig.add_hline(y=80, line_dash="dot", yref="y2")
        fig.update_layout(
            title="Pareto de pérdidas",
            yaxis=dict(title="Minutos"),
            yaxis2=dict(title="Acumulado (%)", overlaying="y", side="right", range=[0, 110]),
            margin=dict(l=10, r=10, t=55, b=10)
        )
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(MOTIVES.rename(columns={
        "Minutos": "Tiempo improductivo (min)",
        "Eventos": "N.º eventos",
        "Acción": "Línea de acción"
    }).round(2), use_container_width=True, hide_index=True)
    st.caption("TPM = pérdidas de mantenimiento. SMED = pérdidas de setup. Las otras causas requieren análisis específico.")

elif page == "Simulador TO-BE":
    st.markdown("### Simulador interactivo de mejora")
    st.write("Modifica los supuestos y observa el impacto calculado sobre el periodo histórico.")
    col1, col2 = st.columns(2)
    with col1:
        tpm_pct = st.slider("Reducción hipotética de pérdidas TPM (%)", 0, 40, 20, 5)
    with col2:
        smed_pct = st.slider("Reducción hipotética de pérdidas SMED (%)", 0, 40, 30, 5)

    recovered_tpm = HISTORICAL["maintenance_min"] * tpm_pct / 100
    recovered_smed = HISTORICAL["setup_min"] * smed_pct / 100
    recovered_total = recovered_tpm + recovered_smed
    downtime_tobe = HISTORICAL["downtime_min"] - recovered_total
    availability_tobe = (HISTORICAL["programmed_min"] - downtime_tobe) / HISTORICAL["programmed_min"] * 100
    improvement = availability_tobe - HISTORICAL["availability"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Disponibilidad TO-BE", f"{availability_tobe:.2f}%", f"{improvement:+.2f} pp")
    m2.metric("Recuperación TPM", f"{recovered_tpm:,.2f} min")
    m3.metric("Recuperación SMED", f"{recovered_smed:,.2f} min")
    m4.metric("Recuperación total", f"{recovered_total:,.2f} min")

    comp = pd.DataFrame({
        "Escenario": ["AS-IS", "TO-BE"],
        "Disponibilidad (%)": [HISTORICAL["availability"], availability_tobe],
        "Tiempo improductivo (min)": [HISTORICAL["downtime_min"], downtime_tobe],
    })
    fig = px.bar(comp, x="Escenario", y="Disponibilidad (%)", color="Escenario",
                 text="Disponibilidad (%)", title="Comparación AS-IS frente al escenario simulado")
    fig.update_layout(showlegend=False, yaxis_range=[70, 100], margin=dict(l=10, r=10, t=55, b=10))
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(comp.round(2), use_container_width=True, hide_index=True)
    st.warning(
        "Este resultado es una simulación matemática. Supone categorías de pérdidas separadas, "
        "tiempo programado constante y que las reducciones hipotéticas se alcanzan. "
        "No constituye una mejora observada ni una garantía de resultados."
    )

elif page == "Datos de Google Sheets":
    st.markdown("### Conexión de solo lectura a Google Sheets")
    st.write(f"Hoja prevista: `{WORKSHEET_NAME}`. La conexión actual solo lee registros; no modifica la hoja.")
    if st.button("Actualizar registros desde Google Sheets", type="primary"):
        st.cache_data.clear()
    try:
        with st.spinner("Conectando con Google Sheets..."):
            live_df, tab_name = read_google_sheet()
        st.success(f"Conexión correcta · pestaña: {tab_name} · registros con ID: {len(live_df)}")
        if live_df.empty:
            st.warning("La pestaña existe, pero no contiene registros con ID_Evento.")
        else:
            simulated_mask = live_df.get(
                "Clasificacion_Sistema", pd.Series("Pendiente de clasificación", index=live_df.index)
            ).eq("Prueba simulada")
            simulated_count = int(simulated_mask.sum())
            unclassified_count = int((~simulated_mask).sum())
            if "Estado_Validacion" in live_df.columns:
                validation = live_df["Estado_Validacion"].fillna("").astype(str).str.strip().str.casefold()
                validated_count = int(validation.isin(["validado", "validada", "validado por responsable", "aprobado", "aprobada"]).sum())
                pending_count = len(live_df) - validated_count
            else:
                validated_count = 0
                pending_count = len(live_df)

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Registros con ID", len(live_df))
            m2.metric("Pruebas simuladas", simulated_count)
            m3.metric("Pendientes de clasificación", unclassified_count)
            m4.metric("Pendientes de validación", pending_count)
            st.caption("Los registros vivos se mantienen separados del histórico julio 2024–junio 2025. Ningún registro vivo modifica el Pareto ni la disponibilidad histórica.")
            if simulated_count:
                st.warning(f"Se identificaron {simulated_count} registro(s) de prueba simulada. No deben presentarse como producción real.")
            if unclassified_count:
                st.info(f"Hay {unclassified_count} registro(s) que no se han identificado como prueba; no se consideran confirmados como operacionales hasta revisarlos.")
            if pending_count:
                st.info(f"Hay {pending_count} registro(s) pendientes o sin estado de validación. Revísalos antes de utilizarlos como evidencia operacional.")
            display_df = live_df.copy()
            display_df = display_df.astype(object).where(pd.notna(display_df), "")
            st.dataframe(display_df, use_container_width=True, hide_index=True)
            st.download_button(
                "Descargar registros visibles como CSV",
                data=live_df.to_csv(index=False).encode("utf-8-sig"),
                file_name="registros_tpm_smed.csv",
                mime="text/csv",
            )
    except Exception as e:
        st.error("No se pudo conectar a Google Sheets.")
        st.write("Revisa los pasos de configuración segura incluidos en el README.")
        st.code(str(e))
        st.info(
            "La conexión CSV publicada no requiere cuenta de servicio ni clave JSON. "
            "Verifica que google_sheets_csv_url sea exactamente la URL probada y que la pestaña sea accesible públicamente."
        )

elif page == "Metodología y límites":
    st.markdown("### Metodología del sistema")
    st.markdown("""
    **1. Línea base AS-IS.** Periodo histórico julio de 2024 a junio de 2025, con 12 observaciones mensuales.

    **2. Disponibilidad.** Se calcula como tiempo operativo dividido entre tiempo programado, multiplicado por 100.

    **3. Priorización.** El análisis de eventos identifica mantenimiento y setup como los motivos con mayor contribución al tiempo improductivo registrado.

    **4. Simulación TO-BE.** Los controles TPM y SMED aplican porcentajes independientes a los minutos históricos de cada categoría y recalculan la disponibilidad.

    **5. Datos vivos.** La sección Google Sheets lee la pestaña `REGISTROS` en modo de solo lectura. Los registros vivos se muestran separados de la base histórica para evitar mezclar periodos.
    """)
    st.markdown("### Limitaciones")
    st.warning("""
    - El pronóstico previo es exploratorio: solo se dispone de 12 meses y el modelo base no garantiza el comportamiento futuro.
    - Los escenarios TO-BE son supuestos; su viabilidad debe verificarse operativamente.
    - La suma de categorías y el total mensual deben conciliarse antes de presentar inferencias definitivas.
    - Esta versión no escribe, edita ni elimina datos en Google Sheets.
    """)

st.divider()
st.caption("Prototipo académico · TPM–SMED · World Plast S.R.L. · Acceso a Google Sheets en modo de solo lectura")
