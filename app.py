"""
AdjudicaCV — Ordenador de Adjudicaciones Docentes

Sidebar-driven layout:
  Sidebar — Origen, criterio, PDF upload, filtros (Paso 1-4)
  Main    — Disclaimer + resultados con pestañas (Paso 5)

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
# Configuración
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
MUNICIPIOS_FILE = SCRIPT_DIR / "municipios_cv.json"
ZONAS_FILE = SCRIPT_DIR / "zonas.json"
AREAS_SUBAREAS_FILE = SCRIPT_DIR / "areas_subareas.json"

# ---------------------------------------------------------------------------
# Estilos CSS
# ---------------------------------------------------------------------------
WIZARD_CSS = """
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
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data
def load_municipios() -> list[dict]:
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
    if df.empty:
        return df
    if "Obs_Tags" in df.columns:
        df["Obs_Tags"] = df["Obs_Tags"].apply(
            lambda x: ", ".join(x) if isinstance(x, list) else x
        )
    return df


@st.cache_data
def load_adjudicacion_data(pdf_bytes: bytes) -> pd.DataFrame:
    try:
        df = parse_adjudicacion(pdf_bytes)
        _sanitize_for_cache(df)
        return df
    except Exception as e:
        st.error(f"Error al parsear el PDF: {e}")
        return pd.DataFrame()


@st.cache_data
def extract_filter_options(df: pd.DataFrame) -> dict:
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
    pdf_key = hashlib.md5(pdf_bytes).hexdigest() if pdf_bytes else ""
    parts = [
        origen or "",
        sort_by or "",
        json.dumps(filter_kwargs, sort_keys=True, default=str),
        pdf_key,
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Disclaimer
# ---------------------------------------------------------------------------

def _render_disclaimer():
    st.info(
        "\U0001f4cc Las distancias y tiempos se calculan desde el centro del "
        "**MUNICIPIO** de origen hasta el **MUNICIPIO** de destino. "
        "Por ello, centros dentro del mismo municipio compartirán "
        "el mismo tiempo/distancia."
    )


# ---------------------------------------------------------------------------
# Welcome screen
# ---------------------------------------------------------------------------

def _render_welcome():
    st.markdown("### Bienvenido a AdjudicaCV")
    st.markdown(
        """
Para comenzar:

1. **Selecciona tu municipio de origen** en la barra lateral izquierda.
2. **Elige el criterio de ordenación** (distancia en km o tiempo en coche).
3. **Sube el PDF de adjudicaciones** oficial de la Conselleria.

Una vez cargado, verás todas las plazas ordenadas por cercanía con
distancias y tiempos de trayecto calculados.
"""
    )


# ---------------------------------------------------------------------------
# Sidebar — Configuration (Paso 1-4)
# ---------------------------------------------------------------------------

def render_sidebar_config() -> bytes | None:
    """Render all configuration widgets in the sidebar.

    Returns the uploaded PDF bytes (or None).
    """
    pdf_bytes = None

    with st.sidebar:
        st.header("AdjudicaCV")
        st.caption("Ordenador de Adjudicaciones Docentes")
        st.divider()

        # --- Paso 1: Tu Origen ---
        st.subheader("Paso 1: Tu Origen")
        municipios = load_municipios()
        if municipios:
            nombres = sorted({m["nombre"] for m in municipios})
            st.selectbox(
                "Municipio de origen",
                options=nombres,
                index=nombres.index("Aldaia") if "Aldaia" in nombres else 0,
                placeholder="Escribe para buscar...",
                key="origen_nombre",
                help=(
                    "Selecciona la localidad desde donde vas a "
                    "desplazarte a diario."
                ),
            )
        else:
            st.error("No se pudieron cargar los municipios.")
            return None

        st.divider()

        # --- Paso 2: Criterio de Ordenación ---
        st.subheader("Paso 2: Criterio de Ordenación")
        st.radio(
            "¿Qué criterio prefieres para ordenar las plazas?",
            options=["distancia", "tiempo"],
            format_func=lambda x: (
                "Distancia en km" if x == "distancia"
                else "Tiempo de trayecto en coche"
            ),
            horizontal=True,
            key="sort_by",
            help=(
                "Cambia entre km y tiempo para reordenar la lista "
                "en tiempo real."
            ),
        )

        st.divider()

        # --- Paso 3: Subida del PDF ---
        st.subheader("Paso 3: Subida del PDF")
        uploaded_file = st.file_uploader(
            "Sube el PDF de adjudicaciones",
            type=["pdf"],
            key="pdf_uploader",
            help=(
                "Sube el PDF oficial. Puedes subirlo completo o recortado "
                "con tu especialidad."
            ),
        )

        if uploaded_file is not None:
            pdf_bytes = uploaded_file.read()
            st.session_state["pdf_bytes"] = pdf_bytes
            st.success(f"PDF cargado: {uploaded_file.name}")
        else:
            pdf_bytes = st.session_state.get("pdf_bytes")

        # --- Paso 4: Filtros (solo tras procesar el PDF) ---
        if pdf_bytes is not None:
            st.divider()
            _render_sidebar_filters(pdf_bytes)

        st.divider()

        # --- Reiniciar ---
        if st.button("\U0001f504 Reiniciar asistente", use_container_width=True):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    return pdf_bytes


def _render_sidebar_filters(pdf_bytes: bytes) -> None:
    """Render filter widgets inside the sidebar (Paso 4)."""
    df_raw = load_adjudicacion_data(pdf_bytes)
    if df_raw.empty:
        return

    filter_options = extract_filter_options(df_raw)

    st.subheader("Paso 4: Filtros")

    st.multiselect(
        "Especialidad",
        options=filter_options["especialidades"],
        default=[],
        placeholder="Todas",
        key="filter_especialidad",
        help="Filtra por código o nombre de especialidad docente.",
    )

    st.multiselect(
        "Tipo de plaza",
        options=filter_options["tipo_options"],
        default=[],
        placeholder="Todos",
        key="filter_tipo",
        help="VACANTE o SUSTITUCIÓN INDETERMINADA.",
    )

    st.selectbox(
        "Provincia",
        options=filter_options["provincias"],
        index=0,
        key="filter_provincia",
        help="Filtra por provincia: Alacant, Castelló o València.",
    )

    iti_options = [FILTRO_TODOS, "SI", "NO"]
    st.selectbox(
        "Itinerancia (ITI)",
        options=iti_options,
        index=0,
        key="filter_iti",
        help="Plazas con itinerancia obligatoria (SI) u optativa (NO).",
    )


def _get_filter_kwargs() -> dict:
    """Build filter_kwargs dict from sidebar widget session_state values."""
    kwargs = {}

    esp = st.session_state.get("filter_especialidad", [])
    if esp:
        kwargs["especialidad"] = esp

    tipo = st.session_state.get("filter_tipo", [])
    if tipo:
        kwargs["tipo"] = tipo

    iti = st.session_state.get("filter_iti", FILTRO_TODOS)
    if iti and iti != FILTRO_TODOS:
        kwargs["iti"] = iti

    prov = st.session_state.get("filter_provincia", FILTRO_TODAS)
    if prov and prov != FILTRO_TODAS:
        kwargs["provincia"] = prov

    return kwargs


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def render_summary(df_filtered: pd.DataFrame, origen_nombre: str):
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


# ---------------------------------------------------------------------------
# Flat view helpers
# ---------------------------------------------------------------------------

FLAT_DROP_COLS = [
    "Cos",
    "Latitud_Destino",
    "Longitud_Destino",
    "Zona_Subarea",
    "Zona_Subarea_Nombre",
]


def _flatten_blocks_to_df(bloques_data: dict) -> pd.DataFrame:
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
            clean["_join_key"] = clean["#"].astype(str)
            metrics["_join_key"] = metrics["Índex"].astype(str)

            clean = clean.merge(
                metrics, on="_join_key", how="left"
            ).drop(columns=["Índex"])

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

    clean = clean.drop(columns=FLAT_DROP_COLS, errors="ignore")
    return clean


# ---------------------------------------------------------------------------
# View renderers
# ---------------------------------------------------------------------------

def render_flat_view(
    df_filtered: pd.DataFrame,
    bloques_data: dict | None = None,
    sort_by: str = "distancia",
):
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
# Main — Sidebar-driven orchestrator
# ---------------------------------------------------------------------------

def main():
    st.set_page_config(
        page_title="Adjudicaciones CV",
        page_icon="\U0001f393",
        layout="wide",
    )

    st.html(WIZARD_CSS)

    st.title("AdjudicaCV \u2014 Ordenador de Adjudicaciones Docentes")
    st.caption(
        "Calcula tiempos y distancias desde tu municipio para solicitar "
        "tus plazas con criterio."
    )

    municipios = load_municipios()
    if not municipios:
        st.error(
            "No se pudieron cargar los municipios. "
            "Verifica que el archivo municipios_cv.json existe."
        )
        return

    pdf_bytes = render_sidebar_config()

    # --- Disclaimer ---------------------------------------------------------
    _render_disclaimer()

    # --- No PDF loaded → welcome screen -------------------------------------
    if pdf_bytes is None:
        _render_welcome()
        return

    # --- Parse PDF ----------------------------------------------------------
    df_raw = load_adjudicacion_data(pdf_bytes)

    if df_raw.empty:
        st.warning("El PDF seleccionado no contiene datos parseables.")
        st.stop()

    # --- Sidebar filters → filter_kwargs ------------------------------------
    filter_kwargs = _get_filter_kwargs()

    # --- Apply filters ------------------------------------------------------
    df_filtered = filter_positions(df_raw, **filter_kwargs)

    if df_filtered.empty:
        st.warning("No hay resultados con los filtros seleccionados.")
        st.stop()

    # --- Enrich with zone data ----------------------------------------------
    if ZONAS_FILE.is_file():
        df_filtered = inject_coordinates(
            df_filtered,
            str(MUNICIPIOS_FILE),
            zonas_json_path=str(ZONAS_FILE),
        )

    # --- Quality gate: validate critical subareas ---------------------------
    if "Zona_Subarea" in df_filtered.columns:
        from match_coords import validate_critical_subareas
        validate_critical_subareas(df_filtered)

    # --- Summary ------------------------------------------------------------
    origen_nombre = st.session_state.get("origen_nombre", "")
    render_summary(df_filtered, origen_nombre)
    st.divider()

    # --- OSRM calculation button --------------------------------------------
    sort_by = st.session_state.get("sort_by", "distancia")

    current_fingerprint = _make_fingerprint(
        origen_nombre, sort_by, filter_kwargs, pdf_bytes
    )
    if "resultados_fingerprint" in st.session_state:
        if (
            st.session_state["resultados_fingerprint"]
            != current_fingerprint
        ):
            st.session_state.pop("resultados", None)
            st.session_state.pop("resultados_fingerprint", None)

    if "resultados" not in st.session_state:
        if st.button(
            "Calcular y Ordenar Plazas", type="primary"
        ):
            with st.spinner(
                f"Calculando distancias desde **{origen_nombre}** "
                f"a {df_filtered['Municipio'].nunique()} "
                f"municipios..."
            ):
                try:
                    bloques_data = generar_bloques(
                        df=df_filtered,
                        origen_nombre=origen_nombre,
                        municipios_cv_path=str(MUNICIPIOS_FILE),
                        zonas_json_path=(
                            str(ZONAS_FILE)
                            if ZONAS_FILE.is_file()
                            else None
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

    # --- Results with tabs --------------------------------------------------
    tab_grouped, tab_flat = st.tabs(
        ["Vista por Subáreas", "Lista Plana"]
    )

    with tab_grouped:
        if "resultados" in st.session_state:
            render_grouped_view(
                st.session_state["resultados"],
                sort_by=sort_by,
            )
        else:
            st.info(
                "Pulsa **Calcular y Ordenar Plazas** para ver los "
                "resultados agrupados por subárea."
            )

    with tab_flat:
        render_flat_view(
            df_filtered,
            bloques_data=st.session_state.get("resultados"),
            sort_by=sort_by,
        )


if __name__ == "__main__":
    main()
