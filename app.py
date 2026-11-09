import pandas as pd
import numpy as np
import re
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
    "monthly_availability": [
        89.7484, 91.0251, 90.6858, 87.8194,
        81.7238, 77.5642, 84.6554, 89.0523,
        85.2737, 87.8686, 88.3579, 84.5340
    ],
}
HISTORICAL["operating_min"] = HISTORICAL["programmed_min"] - HISTORICAL["downtime_min"]
HISTORICAL["global_availability"] = (
    HISTORICAL["operating_min"] / HISTORICAL["programmed_min"] * 100
)

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
        # REGISTROS contains three introductory rows (title, note and blank row).
        # The fourth row contains the actual field names.
        live_df = pd.read_csv(csv_url, skiprows=3, encoding="utf-8-sig", on_bad_lines="warn")
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

        # Classify simulations robustly using the event ID and the full row.
        # The TEST-TPM prefix is decisive even if other columns are blank or shifted.
        def is_simulated(row):
            id_value = ""
            for column in live_df.columns:
                if str(column).strip().casefold() == "id_evento":
                    id_value = str(row.get(column, "") if pd.notna(row.get(column, pd.NA)) else "").strip().upper()
                    break
            values = " ".join(str(v) for v in row.tolist() if pd.notna(v)).upper()
            return (
                id_value.startswith("TEST-TPM-")
                or "TEST-TPM-" in values
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
    values = worksheet.get_all_values()
    if len(values) < 4:
        return pd.DataFrame(), worksheet.title
    headers = [str(value).replace("\ufeff", "").strip() for value in values[3]]
    records = values[4:]
    frame = pd.DataFrame(records, columns=headers)
    frame = frame.loc[:, [c for c in frame.columns if c and not str(c).startswith("Unnamed")]]
    return frame, worksheet.title

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
        ["Resumen ejecutivo", "Pérdidas TPM–SMED", "Simulador TO-BE", "Datos de Google Sheets", "Prueba de dataset", "Metodología y límites"],
    )
    st.divider()
    st.caption(f"Base histórica: {HISTORICAL['period']}")
    st.caption("Los escenarios TO-BE son simulaciones, no resultados observados.")

if page == "Resumen ejecutivo":
    st.markdown("### Indicadores históricos AS-IS")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Disponibilidad global", f"{HISTORICAL['global_availability']:.2f}%")
    c2.metric("Tiempo improductivo", f"{HISTORICAL['downtime_min']:,.2f} min")
    c3.metric("Eventos de mantenimiento", f"{HISTORICAL['maintenance_events']}")
    c4.metric("Eventos de setup", f"{HISTORICAL['setup_events']}")

    left, right = st.columns([1.3, 1])
    with left:
        hist = pd.DataFrame({"Mes": HISTORICAL["months"], "Disponibilidad (%)": HISTORICAL["monthly_availability"]})
        fig = px.line(hist, x="Mes", y="Disponibilidad (%)", markers=True,
                      title="Disponibilidad mensual histórica")
        fig.add_hline(y=HISTORICAL["global_availability"], line_dash="dash",
                      annotation_text=f"Global ponderada: {HISTORICAL['global_availability']:.2f}%")
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
    improvement = availability_tobe - HISTORICAL["global_availability"]

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Disponibilidad TO-BE", f"{availability_tobe:.2f}%", f"{improvement:+.2f} pp")
    m2.metric("Recuperación TPM", f"{recovered_tpm:,.2f} min")
    m3.metric("Recuperación SMED", f"{recovered_smed:,.2f} min")
    m4.metric("Recuperación total", f"{recovered_total:,.2f} min")

    comp = pd.DataFrame({
        "Escenario": ["AS-IS", "TO-BE"],
        "Disponibilidad (%)": [HISTORICAL["global_availability"], availability_tobe],
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
            # Detect simulated test events from every cell, not just one column.
            # Normalize Unicode dashes and spaces because spreadsheet exports can vary.
            def normalize_test_text(value):
                text = "" if pd.isna(value) else str(value)
                text = text.upper().replace("\u00a0", " ")
                for dash in ("‐", "‑", "‒", "–", "—", "―", "−"):
                    text = text.replace(dash, "-")
                return text

            all_row_text = live_df.fillna("").astype(str).apply(
                lambda column: column.map(normalize_test_text)
            ).agg(" ".join, axis=1)

            # Normalize the entire row to letters/digits. This catches IDs even
            # if an export changes dash characters, inserts spaces, or splits punctuation.
            compact_row_text = all_row_text.str.replace(r"[^A-Z0-9]", "", regex=True)
            test_id_pattern = r"TESTTPM\d{3}"
            row_test_id_mask = compact_row_text.str.contains(
                test_id_pattern, regex=True, na=False
            )

            id_column = next(
                (c for c in live_df.columns if str(c).strip().casefold() == "id_evento"),
                None,
            )
            if id_column is not None:
                normalized_ids = live_df[id_column].map(normalize_test_text).str.replace(
                    r"[^A-Z0-9]", "", regex=True
                )
                id_test_mask = normalized_ids.str.contains(
                    test_id_pattern, regex=True, na=False
                )
            else:
                id_test_mask = pd.Series(False, index=live_df.index)

            # The source/observation can also mark a row as simulated even if its ID is malformed.
            simulation_note_mask = all_row_text.str.contains(
                r"PRUEBA\s*SIMULADA|FICTICI|DATOS\s*FICTICIOS|REGISTRO\s*FICTICIO",
                regex=True,
                na=False,
            )
            prior_classification_mask = live_df.get(
                "Clasificacion_Sistema",
                pd.Series("", index=live_df.index),
            ).astype(str).str.strip().str.casefold().eq("prueba simulada")

            simulated_mask = (
                row_test_id_mask
                | id_test_mask
                | simulation_note_mask
                | prior_classification_mask
            )
            live_df["Clasificacion_Sistema"] = np.where(
                simulated_mask, "Prueba simulada", "Pendiente de clasificación"
            )
            simulated_count = int(simulated_mask.sum())
            unclassified_count = int((~simulated_mask).sum())
            if "Estado_Validacion" in live_df.columns:
                validation = live_df["Estado_Validacion"].fillna("").astype(str).str.strip().str.casefold()
                validated_count = int(validation.isin(["validado", "validada", "validado por responsable", "aprobado", "aprobada"]).sum())
                pending_count = len(live_df) - validated_count
            else:
                validated_count = 0
                pending_count = len(live_df)

            # Basic structural checks: these do not certify the truth of a record.
            duplicate_ids = 0
            if id_column is not None:
                ids_for_check = live_df[id_column].fillna("").astype(str).str.strip()
                duplicate_ids = int(ids_for_check.duplicated(keep=False).sum())
            required_fields = ["ID_Evento", "Fecha", "Tipo_Evento", "Fuente_Registro"]
            existing_required = [c for c in required_fields if c in live_df.columns]
            incomplete_rows = 0
            if existing_required:
                incomplete_rows = int(live_df[existing_required].isna().any(axis=1).sum())
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Registros con ID", len(live_df))
            m2.metric("Pruebas simuladas", simulated_count)
            m3.metric("Pendientes de clasificación", unclassified_count)
            m4.metric("Pendientes de validación", pending_count)
            if simulated_count:
                detected_ids = []
                if id_column is not None:
                    detected_ids = live_df.loc[simulated_mask, id_column].fillna("").astype(str).str.strip().tolist()
                detected_ids = [value for value in detected_ids if value and value.casefold() not in {"nan", "none", "<na>"}]
                st.caption("IDs de filas clasificadas como prueba: " + (", ".join(detected_ids) if detected_ids else "se detectaron filas simuladas sin ID legible"))
            st.caption("Los registros vivos se mantienen separados del histórico julio 2024–junio 2025. Ningún registro vivo modifica el Pareto ni la disponibilidad histórica.")
            if simulated_count:
                st.warning(f"Se identificaron {simulated_count} registro(s) de prueba simulada. No deben presentarse como producción real.")
            if unclassified_count:
                st.info(f"Hay {unclassified_count} registro(s) que no se han identificado como prueba; no se consideran confirmados como operacionales hasta revisarlos.")
            if pending_count:
                st.info(f"Hay {pending_count} registro(s) pendientes o sin estado de validación. Revísalos antes de utilizarlos como evidencia operacional.")
            if duplicate_ids:
                st.error(f"Control de calidad: {duplicate_ids} fila(s) presentan identificadores duplicados.")
            if incomplete_rows:
                st.warning(f"Control de calidad: {incomplete_rows} fila(s) tienen campos básicos incompletos ({', '.join(existing_required)}).")
            if not duplicate_ids and not incomplete_rows:
                st.success("Controles estructurales básicos superados: no se detectaron IDs duplicados ni faltantes en los campos básicos disponibles. Esto no sustituye la verificación documental.")
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

elif page == "Prueba de dataset":
    st.markdown("### Prueba funcional con dataset independiente")
    st.caption(
        "Carga archivos CSV o Excel. El sistema identifica si el conjunto corresponde a "
        "una prueba sintética o a registros históricos y conserva esa procedencia."
    )
    uploaded = st.file_uploader(
        "Selecciona un dataset (.csv o .xlsx)",
        type=["csv", "xlsx"],
        help="Se aceptan los encabezados de la plantilla de prueba y los de la hoja histórica R_Tiempos_motivos."
    )

    def _norm_col(value):
        import unicodedata
        value = unicodedata.normalize("NFKD", str(value))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]+", "", value.casefold())

    def _find_col(frame, candidates):
        lookup = {_norm_col(c): c for c in frame.columns}
        for candidate in candidates:
            found = lookup.get(_norm_col(candidate))
            if found is not None:
                return found
        return None

    if uploaded is not None:
        try:
            if uploaded.name.lower().endswith(".csv"):
                raw_df = pd.read_csv(uploaded, encoding="utf-8-sig")
            else:
                raw_df = pd.read_excel(uploaded, engine="openpyxl")
            raw_df.columns = [str(c).replace("\ufeff", "").strip() for c in raw_df.columns]
            raw_df = raw_df.dropna(how="all").copy()

            # Map either the synthetic test schema or the user's actual historical schema.
            id_col = _find_col(raw_df, ["ID_Evento", "ID", "N° Falla"])
            type_col = _find_col(raw_df, ["Tipo_Evento", "Tipo de evento"])
            category_col = _find_col(raw_df, [
                "Categoria_Perdida", "Motivos detectados", "Motivos_detectados",
                "Categoría de pérdida", "Categoria de perdida"
            ])
            time_col = _find_col(raw_df, [
                "Tiempo_Parada_Min", "Tiempor improductivo (Minutos)",
                "Tiempo improductivo (Minutos)", "Tiempo_improductivo_min", "Duracion_min"
            ])
            date_col = _find_col(raw_df, ["Fecha"])
            cause_col = _find_col(raw_df, ["Causas identificadas", "Causas_identificadas"])
            root_col = _find_col(raw_df, ["Causas raíces categorizadas", "Causas_raices_categorizadas"])
            source_col = _find_col(raw_df, ["Fuente_Registro", "Origen_dato", "Fuente"])

            missing = []
            if category_col is None: missing.append("Categoría de pérdida / Motivos detectados")
            if time_col is None: missing.append("Tiempo de parada / Tiempo improductivo")
            if missing:
                st.error("No se encontraron los campos necesarios: " + "; ".join(missing))
                st.caption("Se requieren una categoría de pérdida y un tiempo numérico. Los nombres pueden variar; el sistema reconoce equivalencias.")
            else:
                data = raw_df.copy()
                data["ID_Evento_Sistema"] = (
                    data[id_col].astype("string").str.strip()
                    if id_col is not None
                    else pd.Series([f"ROW-{i+1:03d}" for i in range(len(data))], index=data.index, dtype="string")
                )
                # Source type is determined from explicit metadata/name, never by assuming historical rows are synthetic.
                source_text = uploaded.name.casefold()
                if "sintetico" in source_text or "sintético" in source_text:
                    provenance = "Dataset sintético"
                elif "historico" in source_text or "histórico" in source_text or "julio_2024" in source_text:
                    provenance = "Dataset histórico piloto"
                elif source_col is not None and data[source_col].fillna("").astype(str).str.contains(
                    r"histor|real", case=False, regex=True
                ).any():
                    provenance = "Dataset histórico"
                elif source_col is not None and data[source_col].fillna("").astype(str).str.contains(
                    r"sint[eé]tic|fictici|simulad", case=False, regex=True
                ).any():
                    provenance = "Dataset sintético"
                else:
                    provenance = "Procedencia pendiente de confirmar"

                data["Categoria_Perdida_Sistema"] = data[category_col].fillna("Sin categoría").astype(str).str.strip()
                data["Tiempo_Parada_Min_Sistema"] = pd.to_numeric(data[time_col], errors="coerce")
                if type_col is not None:
                    data["Tipo_Evento_Sistema"] = data[type_col].fillna("Evento de pérdida").astype(str)
                else:
                    data["Tipo_Evento_Sistema"] = "Evento de pérdida"
                if date_col is not None:
                    data["Fecha_Sistema"] = pd.to_datetime(data[date_col], errors="coerce", dayfirst=True)

                valid_time = data["Tiempo_Parada_Min_Sistema"].notna() & (data["Tiempo_Parada_Min_Sistema"] >= 0)
                valid_category = data["Categoria_Perdida_Sistema"].ne("") & data["Categoria_Perdida_Sistema"].ne("Sin categoría")
                id_series = data["ID_Evento_Sistema"].fillna("").astype(str).str.strip()
                valid_ids = id_series.ne("") & ~id_series.duplicated(keep=False)

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Registros recibidos", len(data))
                k2.metric("IDs no duplicados", int(valid_ids.sum()))
                k3.metric("Tiempos válidos", int(valid_time.sum()))
                k4.metric("Tiempo improductivo", f"{data.loc[valid_time, 'Tiempo_Parada_Min_Sistema'].sum():,.2f} min")

                st.info(f"Procedencia identificada: **{provenance}**. Esta carga es independiente de Google Sheets y no modifica la base histórica.")
                if not valid_time.all():
                    st.error(f"{int((~valid_time).sum())} registro(s) tienen tiempos vacíos, no numéricos o negativos.")
                if not valid_category.all():
                    st.warning(f"{int((~valid_category).sum())} registro(s) no tienen categoría de pérdida reconocible.")
                if not valid_ids.all():
                    st.warning("Hay identificadores vacíos o repetidos. Se muestran los registros, pero deben revisarse antes de validar la prueba.")
                if valid_time.all() and valid_category.all() and valid_ids.all():
                    st.success("Validación estructural superada: identificadores, categorías y tiempos son utilizables para el análisis de pérdidas.")

                valid_data = data.loc[valid_time & valid_category].copy()
                summary = (
                    valid_data.groupby("Categoria_Perdida_Sistema", dropna=False)
                    .agg(
                        Eventos=("ID_Evento_Sistema", "count"),
                        Tiempo_Parada_Min=("Tiempo_Parada_Min_Sistema", "sum")
                    )
                    .reset_index()
                    .sort_values("Tiempo_Parada_Min", ascending=False)
                )
                if not summary.empty:
                    total = summary["Tiempo_Parada_Min"].sum()
                    summary["Participacion_%"] = np.where(total > 0, summary["Tiempo_Parada_Min"] / total * 100, 0)
                    summary["Acumulado_%"] = summary["Participacion_%"].cumsum()
                    st.markdown("#### Priorización de pérdidas del archivo cargado")
                    st.dataframe(summary.round(2), use_container_width=True, hide_index=True)
                    left, right = st.columns(2)
                    with left:
                        fig = px.bar(
                            summary.sort_values("Tiempo_Parada_Min"),
                            x="Tiempo_Parada_Min", y="Categoria_Perdida_Sistema",
                            orientation="h", text="Tiempo_Parada_Min",
                            title="Tiempo improductivo por categoría"
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    with right:
                        fig = go.Figure()
                        fig.add_trace(go.Bar(x=summary["Categoria_Perdida_Sistema"], y=summary["Tiempo_Parada_Min"], name="Minutos"))
                        fig.add_trace(go.Scatter(x=summary["Categoria_Perdida_Sistema"], y=summary["Acumulado_%"], name="Acumulado (%)", yaxis="y2", mode="lines+markers"))
                        fig.add_hline(y=80, line_dash="dot", yref="y2")
                        fig.update_layout(
                            title="Pareto del archivo cargado",
                            yaxis=dict(title="Minutos"),
                            yaxis2=dict(title="Acumulado (%)", overlaying="y", side="right", range=[0, 110]),
                            margin=dict(l=10, r=10, t=55, b=10)
                        )
                        st.plotly_chart(fig, use_container_width=True)

                st.markdown("#### Registros reconocidos")
                display_cols = ["ID_Evento_Sistema"]
                if date_col is not None: display_cols.append(date_col)
                display_cols += ["Tipo_Evento_Sistema", "Categoria_Perdida_Sistema", "Tiempo_Parada_Min_Sistema"]
                for optional_col in [cause_col, root_col]:
                    if optional_col is not None and optional_col not in display_cols:
                        display_cols.append(optional_col)
                st.dataframe(data[display_cols], use_container_width=True, hide_index=True)
                st.download_button(
                    "Descargar datos normalizados",
                    data=data.to_csv(index=False).encode("utf-8-sig"),
                    file_name="dataset_tpm_smed_normalizado.csv",
                    mime="text/csv"
                )
        except ImportError as exc:
            st.error("Falta una dependencia para leer este formato.")
            st.code(str(exc))
            st.info("Instala las dependencias incluidas en requirements.txt y vuelve a ejecutar la aplicación.")
        except Exception as exc:
            st.error("No se pudo procesar el archivo. Revisa el formato y los encabezados.")
            st.code(str(exc))

elif page == "Metodología y límites":
    st.markdown("### Metodología del sistema")
    st.markdown("""
    **1. Línea base AS-IS.** Periodo histórico julio de 2024 a junio de 2025, con 12 observaciones mensuales.

    **2. Disponibilidad.** La disponibilidad global se calcula como tiempo operativo total dividido entre tiempo programado total, multiplicado por 100. La serie mensual se conserva como una serie independiente; no se reemplaza por el indicador global ni se utiliza su promedio simple como disponibilidad global.

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
