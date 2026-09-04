"""
Adjudicaciones CV — Asistente de Adjudicaciones de Personal Docente

Flujo controlado en 4 fases:
  Fase 1 — Lazy loading: sin PDF, nada se ejecuta (st.stop)
  Fase 2 — Parseo cacheado del PDF + extracción de filtros (1 sola vez)
  Fase 3 — Botón maestro: OSRM solo se ejecuta tras clic explícito
  Fase 4 — Session State: resultados persisten al cambiar opciones visuales

Ejecutar:
    pip install -r requirements.txt
    streamlit run app.py
"""

from pathlib import Path
import json
import hashlib

import streamlit as st
import pandas as pd

from adjudicacion import (
    parse_adjudicacion,
    filter_positions,
    get_especialidades,
    get_observacion_tags,
    to_clean_table,
)
from adjudicacion import (
    FILTRO_TODOS,
    FILTRO_TODAS,
    SIN_REQUISITO,
    SIN_OBSERVACIONES,
)
from bloques import generar_bloques
from match_coords import inject_coordinates

# ---------------------------------------------------------------------------
# Estilos
# ---------------------------------------------------------------------------
# Evita que los desplegables al final del sidebar (p. ej. "Observaciones")
# queden recortados por el contenedor con overflow.
SIDEBAR_DROPDOWN_CSS = """
<style>
section[data-testid="stSidebar"] div[data-testid="stSidebarContent"] {
    padding-bottom: 9rem;
}
section[data-testid="stSidebar"] div[data-baseweb="popover"] {
    z-index: 1000000 !important;
}
section[data-testid="stSidebar"] div[data-baseweb="popover"] ul[role="listbox"] {
    max-height: min(45vh, 300px);
    overflow-y: auto;
}
</style>
"""

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
MUNICIPIOS_FILE = SCRIPT_DIR / "municipios_cv.json"
ZONAS_FILE = SCRIPT_DIR / "zonas.json"
AREAS_SUBAREAS_FILE = SCRIPT_DIR / "areas_subareas.json"


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data
def load_municipios() -> list[dict]:
    """Carga la lista de municipios desde el JSON."""
    try:
        with open(MUNICIPIOS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        st.error(f"No se encontró el archivo: {MUNICIPIOS_FILE}")
        return []
    except json.JSONDecodeError as e:
        st.error(f"Error al leer municipios_cv.json: {e}")
        return []


def _sanitize_for_cache(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure every column contains only hashable primitives so
    @st.cache_data can serialize the DataFrame without TypeError.

    The Obs_Tags column is produced as a Python list by parse_adjudicacion();
    lists are unhashable and break Streamlit's internal hashing.
    """
    if df.empty:
        return df
    if "Obs_Tags" in df.columns:
        df["Obs_Tags"] = df["Obs_Tags"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else x
        )
    return df


@st.cache_data
def load_adjudicacion_data(pdf_bytes: bytes) -> pd.DataFrame:
    """
    Parse adjudicaciones from uploaded PDF bytes.
    Cacheado: solo se re-ejecuta si cambian los bytes.
    """
    try:
        df = parse_adjudicacion(pdf_bytes)
        _sanitize_for_cache(df)
        return df
    except Exception as e:
        st.error(f"Error al parsear el PDF: {e}")
        return pd.DataFrame()


@st.cache_data
def extract_filter_options(df: pd.DataFrame) -> dict:
    """
    Extrae las opciones disponibles de cada filtro desde el DataFrame parseado.

    Envuelto en @st.cache_data para que solo se ejecute una vez por PDF
    cargado, evitando parpadeos en la interfaz al interactuar con los filtros.
    """
    ling_vals = df["Req_Lingüístic"].fillna("").astype(str).str.strip()
    ling_reales = sorted(v for v in ling_vals.unique().tolist() if v)
    ling_hay_vacios = ling_vals.eq("").any()

    obs_blank = df["Obs_Tags"].apply(
        lambda x: (
            (len(x) == 0) if isinstance(x, list)
            else (not isinstance(x, str) or x.strip() == "")
        )
    )
    obs_vacio = obs_blank.any()

    ling_options = [FILTRO_TODOS]
    if ling_hay_vacios:
        ling_options.append(SIN_REQUISITO)
    ling_options += ling_reales

    return {
        "especialidades": get_especialidades(df),
        "obs_tags": get_observacion_tags(df),
        "provincias": [FILTRO_TODAS]
        + sorted(df["Provincia"].dropna().unique().tolist()),
        "tipo_options": sorted(df["Tipo"].dropna().unique().tolist()),
        "ling_options": ling_options,
        "ling_hay_vacios": ling_hay_vacios,
        "obs_vacio": obs_vacio,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_municipio(nombre: str, municipios: list[dict]) -> dict | None:
    """Busca un municipio por nombre con coincidencia flexible."""
    lower = nombre.lower().strip()
    for m in municipios:
        if m["nombre"].lower() == lower:
            return m
    for m in municipios:
        if lower in m["nombre"].lower():
            return m
    return None


def _make_fingerprint(
    origen: str,
    sort_by: str,
    filter_kwargs: dict,
    pdf_bytes: bytes | None,
) -> str:
    """
    Genera una huella MD5 de los inputs relevantes.

    Se usa para detectar cuándo los resultados almacenados en session_state
    están obsoletos (el usuario cambió origen, filtros, PDF u ordenación).
    """
    pdf_key = hashlib.md5(pdf_bytes).hexdigest() if pdf_bytes else ""
    parts = [
        origen or "",
        sort_by or "",
        json.dumps(filter_kwargs, sort_keys=True, default=str),
        pdf_key,
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Sidebar: Origin + Upload
# ---------------------------------------------------------------------------

def render_sidebar(municipios: list[dict]):
    """Render all sidebar controls. Returns a dict with current selections."""

    with st.sidebar:
        st.header("Origen")
        nombres = sorted({m["nombre"] for m in municipios})
        origen_nombre = st.selectbox(
            "Municipio de origen",
            options=nombres,
            index=nombres.index("Aldaia") if "Aldaia" in nombres else 0,
            placeholder="Escribe para buscar...",
        )

        origen_data = find_municipio(origen_nombre, municipios)
        if origen_data:
            st.caption(
                f"{origen_data['nombre']} — {origen_data['provincia']}  \n"
                f"Lat: {origen_data['latitud']:.5f} · Lon: {origen_data['longitud']:.5f}"
            )

        st.divider()

        st.header("PDF de Adjudicaciones")
        uploaded_file = st.file_uploader(
            "Sube el PDF",
            type=["pdf"],
            help="Archivo PDF de adjudicaciones de la Conselleria",
        )

        pdf_bytes = None

        if uploaded_file is not None:
            pdf_bytes = uploaded_file.read()
            st.success(f"PDF cargado: {uploaded_file.name}")

        st.divider()

        st.header("Ordenación")
        sort_by = st.radio(
            "Criterio de ordenación",
            options=["distancia", "tiempo"],
            format_func=lambda x: (
                "Distancia (km)" if x == "distancia"
                else "Tiempo de trayecto (min)"
            ),
            horizontal=True,
            key="sort_by",
        )

    return {
        "origen_nombre": origen_nombre,
        "origen_data": origen_data,
        "pdf_bytes": pdf_bytes,
        "sort_by": sort_by,
    }


# ---------------------------------------------------------------------------
# Sidebar: Filters  (Fase 2 — recibe opciones pre-extraídas y cacheadas)
# ---------------------------------------------------------------------------

def render_filters(filter_options: dict) -> dict:
    """
    Render filter controls in sidebar using pre-extracted options.

    Acepta un dict con las opciones ya extraídas (cacheadas por
    extract_filter_options), NO el DataFrame completo.
    """

    if not filter_options:
        return {}

    with st.sidebar:
        st.divider()
        st.header("Filtros")

        esp_selected = st.multiselect(
            "Especialidad",
            options=filter_options["especialidades"],
            default=[],
            placeholder="Todas",
        )

        tipo_selected = st.multiselect(
            "Tipo",
            options=filter_options["tipo_options"],
            default=[],
            placeholder="Todos",
        )

        iti_options = [FILTRO_TODOS, "SI", "NO"]
        iti_selected = st.selectbox("Itinerancia (ITI)", options=iti_options, index=0)

        prov_selected = st.selectbox(
            "Provincia", options=filter_options["provincias"], index=0
        )

        municipi_text = st.text_input("Municipio (parcial)", placeholder="ej: València")

        ling_selected = st.selectbox(
            "Requisito Lingüístico",
            options=filter_options["ling_options"],
            index=0,
        )

        obs_options = [FILTRO_TODAS]
        if filter_options.get("obs_vacio"):
            obs_options.append(SIN_OBSERVACIONES)
        obs_options += filter_options["obs_tags"]
        obs_selected = st.selectbox("Observaciones", options=obs_options, index=0)

    kwargs = {}
    if esp_selected:
        kwargs["especialidad"] = esp_selected
    if tipo_selected:
        kwargs["tipo"] = tipo_selected
    if iti_selected != FILTRO_TODOS:
        kwargs["iti"] = iti_selected
    if prov_selected != FILTRO_TODAS:
        kwargs["provincia"] = prov_selected
    if municipi_text.strip():
        kwargs["municipi"] = municipi_text.strip()
    if ling_selected != FILTRO_TODOS:
        kwargs["req_lingüístic"] = ling_selected
    if obs_selected != FILTRO_TODAS:
        kwargs["observaciones"] = obs_selected

    return kwargs


# ---------------------------------------------------------------------------
# Main area: Summary + View Toggle + Results
# ---------------------------------------------------------------------------

def render_summary(df_filtered: pd.DataFrame, origen_nombre: str):
    """Render summary metrics bar."""
    st.subheader("Resumen")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total plazas", len(df_filtered))
    c2.metric("Vacantes", len(df_filtered[df_filtered["Tipo"] == "VACANTE"]))
    c3.metric(
        "Sustituciones",
        len(df_filtered[df_filtered["Tipo"] == "SUSTITUCIÓN INDETERMINADA"]),
    )
    c4.metric("ITI SI", len(df_filtered[df_filtered["ITI"] == "SI"]))
    n_sub = (
        df_filtered["Zona_Subarea"].nunique()
        if "Zona_Subarea" in df_filtered.columns
        else 0
    )
    c5.metric("Subáreas", n_sub)


# Columnas prescindibles que se eliminan de la vista plana y las
# exportaciones (CSV/JSON). La lógica interna (cálculo OSRM) las usa antes
# de este punto, por lo que solo se descartan al final del enriquecimiento.
FLAT_DROP_COLS = [
    "Cos",
    "Latitud_Destino",
    "Longitud_Destino",
    "Zona_Subarea",
    "Zona_Subarea_Nombre",
]


def _flatten_blocks_to_df(bloques_data: dict) -> pd.DataFrame:
    """Flatten the grouped blocks structure into a per-plaza DataFrame with
    the computed route metrics, keyed by the original row 'Índex'."""
    records = []
    for bloque in bloques_data.get("resumen_por_subareas", []):
        for p in bloque.get("plazas", []):
            records.append({
                "Índex": p.get("index"),
                "Subárea": p.get("subarea_codigo", ""),
                "Tiempo (min)": p.get("tiempo_trayecto_minutos"),
                "Distancia (km)": p.get("distancia_km"),
            })
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def _prepare_flat_df(
    df_filtered: pd.DataFrame,
    bloques_data: dict | None,
    sort_by: str = "distancia",
) -> pd.DataFrame:
    """Build the display-ready, sorted flat table.

    Enriches each position with the computed travel time (min) and distance
    (km) plus its subarea (when block results exist), then sorts ascending by
    the selected criterion, placing unresolved (None/NaN) rows last.
    """
    clean = to_clean_table(df_filtered)

    if bloques_data is None:
        clean["Tiempo (min)"] = None
        clean["Distancia (km)"] = None
    else:
        metrics = _flatten_blocks_to_df(bloques_data)
        if metrics.empty:
            clean["Tiempo (min)"] = None
            clean["Distancia (km)"] = None
            clean["Subárea"] = ""
        else:
            # Normalise merge keys to str so int/str/float mismatches never
            # produce silent NaN rows.
            clean["_join_key"] = clean["#"].astype(str)
            metrics["_join_key"] = metrics["Índex"].astype(str)

            clean = clean.merge(
                metrics, on="_join_key", how="left"
            ).drop(columns=["Índex"])

            # Fallback: fill any remaining NaN via subarea-level aggregation
            # (takes the minimum time/distance within each subarea).
            if clean["Tiempo (min)"].isna().any() and "Zona_Subarea" in clean.columns:
                subarea_min = (
                    metrics.groupby("Subárea")
                    .agg({"Tiempo (min)": "min", "Distancia (km)": "min"})
                    .reset_index()
                )
                subarea_min["_join_key"] = subarea_min["Subárea"].astype(str)
                mask = clean["Tiempo (min)"].isna()
                merged_fb = clean.loc[mask].merge(
                    subarea_min, on="_join_key", how="left",
                    suffixes=("_orig", "_fb"),
                )
                for col in ("Tiempo (min)", "Distancia (km)"):
                    fb_col = f"{col}_fb"
                    if fb_col in merged_fb.columns:
                        clean.loc[mask, col] = merged_fb[fb_col].values
                if "Subárea_fb" in merged_fb.columns:
                    clean.loc[mask, "Subárea"] = merged_fb["Subárea_fb"].values

            clean.drop(columns=["_join_key"], inplace=True)

    sort_col = "Distancia (km)" if sort_by == "distancia" else "Tiempo (min)"
    if sort_col in clean.columns:
        clean = clean.sort_values(
            by=sort_col,
            ascending=True,
            na_position="last",
        )

    # Descarta las columnas redundantes tras el enriquecimiento/sorteo.
    clean = clean.drop(columns=FLAT_DROP_COLS, errors="ignore")
    return clean


def render_flat_view(
    df_filtered: pd.DataFrame,
    bloques_data: dict | None = None,
    sort_by: str = "distancia",
):
    """Render the flat table view of filtered positions.

    If computed block results are available, each row is enriched with the
    estimated travel time (min) and distance (km), plus its subarea, and the
    table is sorted ascending by the selected sidebar criterion.
    """
    if bloques_data is None:
        st.info(
            "Pulsa **Calcular y Ordenar Plazas** para obtener los tiempos "
            "y distancias de cada plaza."
        )

    clean = _prepare_flat_df(df_filtered, bloques_data, sort_by)

    if bloques_data is not None and "Distancia (km)" in clean.columns:
        sort_label = "distancia" if sort_by == "distancia" else "tiempo"
        st.caption(
            f"Ordenada per **{sort_label}** (ascendent). Plazas sense ruta "
            f"calculada es mostren al final."
        )

    st.dataframe(
        clean,
        use_container_width=True,
        height=500,
        hide_index=True,
    )

    col_csv, col_json = st.columns(2)
    with col_csv:
        csv_data = clean.to_csv(index=False, encoding="utf-8-sig")
        st.download_button(
            label="CSV",
            data=csv_data,
            file_name="adjudicacio_filtrat.csv",
            mime="text/csv",
        )
    with col_json:
        json_records = clean.where(clean.notna(), other=None).to_dict(
            orient="records"
        )
        json_data = json.dumps(
            json_records, ensure_ascii=False, indent=2, default=str
        )
        st.download_button(
            label="JSON",
            data=json_data,
            file_name="adjudicacio_filtrat.json",
            mime="application/json",
        )


def render_grouped_view(bloques_data: dict, sort_by: str = "distancia"):
    """Render the grouped accordion view of blocks by subarea."""
    subareas = bloques_data.get("resumen_por_subareas", [])

    if not subareas:
        st.info("No hay subáreas para mostrar.")
        return

    sort_col = "Distancia (km)" if sort_by == "distancia" else "Tiempo (min)"
    sort_label = "distancia" if sort_by == "distancia" else "tiempo"

    for bloque in subareas:
        codigo = bloque["subarea_codigo"]
        nombre = bloque.get("subarea_nombre", "")
        tiempo = bloque["tiempo_medio_minutos"]
        distancia = bloque["distancia_minima_km"]
        total = bloque["total_plazas"]

        label_parts = [f"Subárea {codigo}"]
        if nombre:
            label_parts.append(f"— {nombre}")
        label_parts.append(
            f"  |  {tiempo:.1f} min (media)" if tiempo is not None
            else "  |  N/A min (media)"
        )
        label_parts.append(
            f"  |  {distancia:.1f} km (mínima)" if distancia is not None
            else "  |  N/A km (mínima)"
        )
        label_parts.append(f"  |  {total} plazas")

        label = " ".join(label_parts)

        with st.expander(label, expanded=False):
            plazas = bloque.get("plazas", [])

            if not plazas:
                st.info("No hay plazas en esta subárea.")
                continue

            st.markdown(
                f"**{len(plazas)} plazas** ordenadas por {sort_label} "
                f"(de menor a mayor)"
            )

            rows = []
            for p in plazas:
                t = p.get("tiempo_trayecto_minutos")
                d = p.get("distancia_km")
                rows.append(
                    {
                        "Centro": p.get("centro", ""),
                        "Municipio": p.get("municipio", ""),
                        "Especialidad": p.get("especialidad", ""),
                        "Tipo": p.get("tipo", ""),
                        "Req. Ling.": p.get("requisito_idioma", ""),
                        "Tiempo (min)": t if t is not None else "N/A",
                        "Distancia (km)": d if d is not None else "N/A",
                    }
                )

            df_plazas = pd.DataFrame(rows)

            def _sort_key(val):
                if val is None or val == "N/A":
                    return float("inf")
                return float(val)

            df_plazas = df_plazas.sort_values(
                by=sort_col,
                key=lambda col: col.map(_sort_key),
                ascending=True,
            )

            st.dataframe(df_plazas, use_container_width=True, hide_index=True)

    col_json, = st.columns(1)
    with col_json:
        json_str = json.dumps(bloques_data, ensure_ascii=False, indent=2)
        st.download_button(
            label="Descargar JSON completo",
            data=json_str,
            file_name="bloques_subareas.json",
            mime="application/json",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Adjudicaciones CV",
        page_icon="\U0001f393",
        layout="wide",
    )

    st.html(SIDEBAR_DROPDOWN_CSS)

    st.title("Asistente de Adjudicaciones CV")
    st.caption(
        "Selecciona tu municipio de origen, carga el PDF de adjudicaciones "
        "y filtra las plazas para encontrar las más cercanas."
    )

    municipios = load_municipios()

    if not municipios:
        st.error(
            "No se pudieron cargar los municipios. "
            "Verifica que el archivo municipios_cv.json existe."
        )
        return

    # --- Sidebar: Origin + PDF + Ordering -----------------------------------
    sidebar = render_sidebar(municipios)
    origen_nombre = sidebar["origen_nombre"]
    pdf_bytes = sidebar["pdf_bytes"]
    sort_by = sidebar["sort_by"]

    # ===================================================================
    # FASE 1 — Lazy loading gate
    #   Si no hay PDF subido, la app se detiene inmediatamente con un
    #   mensaje amigable. Nada más se ejecuta.
    # ===================================================================
    if pdf_bytes is None:
        st.info(
            "Carga un archivo PDF para extraer, filtrar y ordenar "
            "las adjudicaciones."
        )
        st.stop()

    # ===================================================================
    # FASE 2 — Parseo controlado + filtros cacheados
    #   Solo ahora se parsea el PDF (resultado cacheado por @st.cache_data).
    #   Las opciones de filtro se extraen una sola vez y se cachean.
    # ===================================================================
    df_raw = load_adjudicacion_data(pdf_bytes)

    if df_raw.empty:
        st.warning("El PDF seleccionado no contiene datos parseables.")
        st.stop()

    filter_options = extract_filter_options(df_raw)
    filter_kwargs = render_filters(filter_options)

    # --- Apply filters ------------------------------------------------------
    df_filtered = filter_positions(df_raw, **filter_kwargs)

    if df_filtered.empty:
        st.warning("No hay resultados con los filtros seleccionados.")
        st.stop()

    # --- Enrich with zone data ----------------------------------------------
    # Inject coordinates + subareas using the robust fallback-by-base-name
    # logic (resolves pedanías like "ELX - ALTABIX" -> ELX -> 0351).
    if ZONAS_FILE.is_file():
        df_filtered = inject_coordinates(
            df_filtered,
            str(MUNICIPIOS_FILE),
            zonas_json_path=str(ZONAS_FILE),
        )

    # --- Quality gate: validate critical subareas --------------------------
    if "Zona_Subarea" in df_filtered.columns:
        from match_coords import validate_critical_subareas
        validate_critical_subareas(df_filtered)

    # --- Summary ------------------------------------------------------------
    render_summary(df_filtered, origen_nombre)

    st.divider()

    # ===================================================================
    # FASE 4 — Invalidación de resultados obsoletos
    #   Comparamos la huella de los inputs actuales con la almacenada.
    #   Si cambiaron origen, filtros, PDF u ordenación, se limpian los
    #   resultados previos para obligar a un nuevo cálculo.
    # ===================================================================
    current_fingerprint = _make_fingerprint(
        origen_nombre, sort_by, filter_kwargs, pdf_bytes
    )
    if "resultados_fingerprint" in st.session_state:
        if st.session_state["resultados_fingerprint"] != current_fingerprint:
            st.session_state.pop("resultados", None)
            st.session_state.pop("resultados_fingerprint", None)

    # --- View mode toggle (opción visual — NO dispara cálculo) ---------------
    view_mode = st.radio(
        "Vista",
        options=["blocks", "flat"],
        format_func=lambda x: (
            "Agrupada por subáreas" if x == "blocks" else "Lista plana"
        ),
        horizontal=True,
        key="view_mode",
    )

    # ===================================================================
    # FASE 3 + 4 — Botón maestro + persistencia en session_state
    #   El backend (OSRM + agrupación por subárea) SOLO se ejecuta cuando
    #   el usuario hace clic en el botón. Los resultados se almacenan en
    #   st.session_state["resultados"] y se reutilizan al cambiar de vista.
    #
    #   El botón es COMÚN a ambas vistas ( Lista plana y Agrupada ) para
    #   que el usuario no necesite cambiar de pestaña para calcular.
    # ===================================================================
    if "resultados" not in st.session_state:
        if st.button("Calcular y Ordenar Plazas", type="primary"):
            with st.spinner(
                f"Calculando distancias desde **{origen_nombre}** "
                f"a {df_filtered['Municipio'].nunique()} municipios..."
            ):
                try:
                    bloques_data = generar_bloques(
                        df=df_filtered,
                        origen_nombre=origen_nombre,
                        municipios_cv_path=str(MUNICIPIOS_FILE),
                        zonas_json_path=(
                            str(ZONAS_FILE) if ZONAS_FILE.is_file() else None
                        ),
                        areas_subareas_path=(
                            str(AREAS_SUBAREAS_FILE)
                            if AREAS_SUBAREAS_FILE.is_file()
                            else None
                        ),
                        sort_by=sort_by,
                    )
                    st.session_state["resultados"] = bloques_data
                    st.session_state["resultados_fingerprint"] = (
                        current_fingerprint
                    )
                except ValueError as e:
                    st.error(str(e))
                    st.stop()
                except Exception as e:
                    st.error(f"Error calculando rutas: {e}")
                    st.stop()

    # --- Render selected view -----------------------------------------------
    if view_mode == "blocks":
        st.subheader("Cálculo por subáreas")

        if "resultados" in st.session_state:
            render_grouped_view(
                st.session_state["resultados"], sort_by=sort_by
            )
        else:
            st.info(
                "Pulsa **Calcular y Ordenar Plazas** para ver los resultados "
                "agrupados por subárea."
            )
    else:
        render_flat_view(
            df_filtered,
            bloques_data=st.session_state.get("resultados"),
            sort_by=sort_by,
        )


if __name__ == "__main__":
    main()
