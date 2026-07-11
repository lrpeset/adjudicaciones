"""
parse_pdf.py

[DEPRECATED] Este modulo esta deprecado y sera eliminado en futuras versiones.

La funcionalidad de parseo de PDF de adjudicaciones ha sido reemplazada por:
  - adjudicacion.py: Parser moderno con filtrado completo
  - bloques.py: Motor de agrupacion por subareas con calculo OSRM
  - match_coords.py: Inyeccion de coordenadas geograficas

El merge con CSV de rutas pre-calculadas (merge_with_routes) ha sido
reemplazado por calculo en tiempo real via OSRM Table API (bloques.py).

Mantenido unicamente para retrocompatibilidad con tests existentes.
"""

import warnings as _warnings
_warnings.warn(
    "parse_pdf.py esta deprecated. Usa adjudicacion.py + bloques.py en su lugar.",
    DeprecationWarning,
    stacklevel=2,
)

import re
import sys
import unicodedata
from pathlib import Path

import pandas as pd
from pypdf import PdfReader


# ---------------------------------------------------------------------------
# Patterns for header fields that change periodically
# ---------------------------------------------------------------------------
RE_CUERPO = re.compile(
    r"CUERPO/COS:\s*(.+)", re.IGNORECASE
)
RE_ESPECIALIDAD = re.compile(
    r"ESPECIALIDAD/ESPECIALITAT:\s*(.+)", re.IGNORECASE
)
RE_PROVINCIA = re.compile(
    r"PROVINCIA/PROVINCIA:\s*(.+)", re.IGNORECASE
)

# Stop criterion for closing a block.
# VACANTE as a record-type label appears at line end, before a pipe, or
# before a trailing number — NOT mid-sentence inside a school name.
RE_BLOCK_END = re.compile(
    r"\bVACANTE(?:\s*\||\s+\d|\s*$)"
    r"|SUSTITUCI[OÓ]N\s+INDETERMINADA",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Patterns for field extraction within each block
# ---------------------------------------------------------------------------
# Centro_Codigo: 8-digit code enclosed in dashes, starting with 03, 12, or 46
RE_CODIGO = re.compile(r"-(03\d{6}|12\d{6}|46\d{6})-")

# Lloc (position code): standalone 6 or 7 digits (not part of longer number)
RE_LLOC = re.compile(r"(?<!\d)(\d{6,7})(?!\d)")


def extract_text_pages(pdf_path: str) -> list[str]:
    """Return one string per page, preserving reading order."""
    reader = PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return pages


def iter_lines(pdf_path: str):
    """
    Yield every line from the PDF, one at a time, in document order.

    pypdf's per-page extract_text() can merge or split visual lines
    unpredictably, so we normalise each page by splitting on newlines
    and also on layout-induced mid-line breaks (e.g. long tokens that
    contain both a label and a value on what looks like one wrapped
    paragraph).
    """
    for page_text in extract_text_pages(pdf_path):
        # Split the page into logical lines on newlines first
        for raw_line in page_text.splitlines():
            stripped = raw_line.strip()
            if stripped:
                yield stripped


def parse_pdf(pdf_path: str, max_blocks: int = 5) -> list[dict]:
    """
    Parse the PDF and return a list of block dicts.

    Each block contains:
      - cuerpo, especialidad, provincia  (current header values)
      - text  (concatenated lines belonging to this record)
    """
    current_cuerpo = ""
    current_especialidad = ""
    current_provincia = ""

    blocks: list[dict] = []
    current_lines: list[str] = []

    def _flush_block(last_line: str = ""):
        """Close the current block if it has content."""
        nonlocal current_lines
        if current_lines:
            if last_line:
                tipo = "VACANTE" if "VACANTE" in last_line.upper() else "SUSTITUCION"
            else:
                # Trailing block — no explicit terminator; infer from text
                joined = " ".join(current_lines).upper()
                tipo = "VACANTE" if "VACANTE" in joined else "SUSTITUCION"
            blocks.append({
                "cuerpo": current_cuerpo,
                "especialidad": current_especialidad,
                "provincia": current_provincia,
                "tipo": tipo,
                "text": " ".join(current_lines),
            })
            current_lines = []

    for line in iter_lines(pdf_path):
        # --- Check stop criterion (before header update to preserve state) ---
        if RE_BLOCK_END.search(line):
            current_lines.append(line)
            _flush_block(last_line=line)
            if max_blocks > 0 and len(blocks) >= max_blocks:
                break
            continue

        # --- Check for header field updates --------------------------------
        m = RE_CUERPO.search(line)
        if m:
            current_cuerpo = m.group(1).strip()

        m = RE_ESPECIALIDAD.search(line)
        if m:
            current_especialidad = m.group(1).strip()

        m = RE_PROVINCIA.search(line)
        if m:
            current_provincia = m.group(1).strip()

        # --- Accumulate line into current block ----------------------------
        current_lines.append(line)

    # Flush any trailing block that was never terminated
    _flush_block()

    return blocks[:max_blocks]


# ---------------------------------------------------------------------------
# Field extraction & normalization
# ---------------------------------------------------------------------------

def normalize_nombre(nombre: str) -> str:
    """
    Convert to uppercase and strip accents/diacritics.

    'València' -> 'VALENCIA', 'Alacant/Alicante' -> 'ALACANT/ALICANTE'
    """
    nombre = nombre.upper().strip()
    # Decompose Unicode then drop combining marks (accents)
    nfkd = unicodedata.normalize("NFKD", nombre)
    return "".join(ch for ch in nfkd if unicodedata.category(ch) != "Mn")


def extract_fields(block: dict) -> dict:
    """
    Extract structured fields from a raw text block.

    Returns a dict with: Municipio, Centro_Codigo, Centro_Nombre, Lloc,
    tipo (VACANTE/SUSTITUCION), plus the inherited header values.
    """
    text = block["text"]

    # --- Centro_Codigo ------------------------------------------------------
    m_code = RE_CODIGO.search(text)
    codigo = m_code.group(1) if m_code else None

    # --- Municipio: everything before the first '-NNNNNNNN-' ----------------
    municipio = ""
    if m_code:
        raw_muni = text[: m_code.start()]
    else:
        raw_muni = text

    # Strip leading index numbers like '53 |' and vertical bars / dashes
    raw_muni = re.sub(r"^\d+\s*[\|\-]\s*", "", raw_muni)
    raw_muni = re.sub(r"[\|\-]+", " ", raw_muni)
    municipio = raw_muni.strip().strip("-").strip()

    # --- Centro_Nombre: text right after the code, up to next pipe/number ---
    centro_nombre = ""
    if m_code:
        after_code = text[m_code.end():]
        # Take tokens until we hit a pipe or end-of-string
        m_name = re.match(
            r"\s*(.+?)(?:\s*\||\s*$)", after_code
        )
        if m_name:
            centro_nombre = m_name.group(1).strip()

    # --- Lloc: last 6-7 digit number in the block --------------------------
    lloc_matches = RE_LLOC.findall(text)
    lloc = lloc_matches[-1] if lloc_matches else None

    # Strip trailing Lloc from Centro_Nombre if present
    if lloc and centro_nombre.endswith(f" {lloc}"):
        centro_nombre = centro_nombre[: -len(lloc) - 1].strip()

    return {
        "Municipio": municipio,
        "Municipio_Norm": normalize_nombre(municipio),
        "Centro_Codigo": codigo,
        "Centro_Nombre": centro_nombre,
        "Lloc": lloc,
        "Cuerpo": block["cuerpo"],
        "Especialidad": block["especialidad"],
        "Provincia": block["provincia"],
        "Tipo": block["tipo"],
    }


def blocks_to_dataframe(blocks: list[dict]) -> pd.DataFrame:
    """Convert a list of raw blocks into a cleaned pandas DataFrame."""
    rows = [extract_fields(b) for b in blocks]
    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# Route merge
# ---------------------------------------------------------------------------

def merge_with_routes(
    positions_df: pd.DataFrame,
    routes_csv: str,
    origen: str,
) -> pd.DataFrame:
    """
    Left-join teacher positions with driving routes from a home town.

    Parameters
    ----------
    positions_df : DataFrame
        Must contain a 'Municipio_Norm' column (already normalized).
    routes_csv : str
        Path to a CSV with columns: Origen, Destino, Km, Tiempo.
    origen : str
        The origin town to filter routes by (will be normalized before
        matching so accents don't matter).

    Returns
    -------
    DataFrame
        The positions DataFrame with Km, Tiempo, and Ruta_Encontrada
        columns added from the matched routes.
    """
    try:
        routes = pd.read_csv(routes_csv)
    except Exception as e:
        print(f"ERROR: cannot read routes CSV: {e}", file=sys.stderr)
        positions_df = positions_df.copy()
        positions_df["Km"] = None
        positions_df["Tiempo"] = None
        positions_df["Ruta_Encontrada"] = False
        return positions_df

    required = {"Origen", "Destino", "Km", "Tiempo"}
    missing = required - set(routes.columns)
    if missing:
        print(
            f"ERROR: routes CSV missing columns {missing}. "
            f"Found: {list(routes.columns)}",
            file=sys.stderr,
        )
        positions_df = positions_df.copy()
        positions_df["Km"] = None
        positions_df["Tiempo"] = None
        positions_df["Ruta_Encontrada"] = False
        return positions_df

    # Coerce Km/Tiempo to numeric; non-parseable values become NaN
    routes["Km"] = pd.to_numeric(routes["Km"], errors="coerce")
    routes["Tiempo"] = pd.to_numeric(routes["Tiempo"], errors="coerce")

    # Normalize the origin column and filter
    routes["Origen_Norm"] = routes["Origen"].apply(normalize_nombre)
    origen_norm = normalize_nombre(origen)
    routes_home = routes[routes["Origen_Norm"] == origen_norm].copy()

    if routes_home.empty:
        print(
            f"WARNING: no routes found for origen='{origen}' "
            f"(normalized='{origen_norm}'). "
            f"Available origins: {routes['Origen'].unique().tolist()}",
            file=sys.stderr,
        )
        # Return positions with empty route columns
        positions_df = positions_df.copy()
        positions_df["Km"] = None
        positions_df["Tiempo"] = None
        positions_df["Ruta_Encontrada"] = False
        return positions_df

    # Normalize the destination column for joining
    routes_home["Destino_Norm"] = routes_home["Destino"].apply(normalize_nombre)

    # Select only what we need from routes and drop duplicate destinations
    # (keep the shortest route if multiple exist for the same destination)
    routes_join = (
        routes_home
        .sort_values("Km")
        .drop_duplicates(subset="Destino_Norm", keep="first")
        [["Destino_Norm", "Km", "Tiempo"]]
    )

    # Left join: keep ALL positions, match routes where possible
    merged = positions_df.merge(
        routes_join,
        left_on="Municipio_Norm",
        right_on="Destino_Norm",
        how="left",
    )

    # Flag whether a route was found
    merged["Ruta_Encontrada"] = merged["Destino_Norm"].notna()

    # Clean up helper column
    merged.drop(columns=["Destino_Norm"], inplace=True, errors="ignore")

    return merged


# ---------------------------------------------------------------------------
# Full pipeline & fallback
# ---------------------------------------------------------------------------

OUTPUT_FILE = "vacantes_ordenadas.csv"

TEMP_COLS = ["Municipio_Norm", "Ruta_Encontrada"]


def build_vacantes(
    pdf_path: str,
    routes_csv: str,
    origen: str,
    output: str = OUTPUT_FILE,
) -> pd.DataFrame:
    """
    Full pipeline: parse PDF → extract → merge → filter → sort → export.

    Returns the final filtered and sorted DataFrame.
    """
    # 1. Parse & extract
    blocks = parse_pdf(pdf_path, max_blocks=0)
    if not blocks:
        print("No se encontraron bloques en el documento.", file=sys.stderr)
        return pd.DataFrame()

    df = blocks_to_dataframe(blocks)
    print(f">>> Bloques extraidos: {len(df)}")

    # 2. Merge with routes
    df = merge_with_routes(df, routes_csv, origen)
    matched = df["Ruta_Encontrada"].sum()
    print(f">>> Rutas encontradas: {matched}/{len(df)}")

    # 3. Filter: keep only VACANTE
    df = df[df["Tipo"] == "VACANTE"].copy()
    print(f">>> Vacantes (tras filtro): {len(df)}")

    # 4. Sort by Tiempo then Km (ascending); NaNs go last
    df = df.sort_values(
        by=["Tiempo", "Km"],
        ascending=True,
        na_position="last",
    ).reset_index(drop=True)

    # 5. Drop temporary columns
    df.drop(columns=[c for c in TEMP_COLS if c in df.columns], inplace=True)

    # 6. Export
    df.to_csv(output, index=False, encoding="utf-8-sig")
    print(f">>> Exportado a {output} ({len(df)} filas, utf-8-sig)")

    return df


def fallback_proximidad(routes_csv: str, origen: str) -> pd.DataFrame:
    """
    Without a PDF, read the routes CSV and return all destinations
    sorted by proximity (Km) from the origin.
    """
    try:
        routes = pd.read_csv(routes_csv)
    except Exception as e:
        print(f"ERROR: cannot read routes CSV: {e}", file=sys.stderr)
        return pd.DataFrame()

    required = {"Origen", "Destino", "Km"}
    missing = required - set(routes.columns)
    if missing:
        print(
            f"ERROR: routes CSV missing columns {missing}. "
            f"Found: {list(routes.columns)}",
            file=sys.stderr,
        )
        return pd.DataFrame()

    routes["Km"] = pd.to_numeric(routes["Km"], errors="coerce")

    routes["Origen_Norm"] = routes["Origen"].apply(normalize_nombre)
    routes["Destino_Norm"] = routes["Destino"].apply(normalize_nombre)
    origen_norm = normalize_nombre(origen)

    home = routes[routes["Origen_Norm"] == origen_norm].copy()
    if home.empty:
        print(
            f"WARNING: no routes found for '{origen}'. "
            f"Available: {routes['Origen'].unique().tolist()}",
            file=sys.stderr,
        )
        return pd.DataFrame()

    # Keep shortest route per destination
    prox = (
        home.sort_values("Km")
        .drop_duplicates(subset="Destino_Norm", keep="first")
        .sort_values("Km")
        .reset_index(drop=True)
    )
    prox.drop(
        columns=["Origen_Norm", "Destino_Norm", "Origen"],
        inplace=True, errors="ignore",
    )
    prox.rename(columns={"Destino": "Municipio"}, inplace=True)

    output = "municipios_por_proximidad.csv"
    prox.to_csv(output, index=False, encoding="utf-8-sig")
    print(f">>> Fallback: {len(prox)} municipios por proximidad → {output}")
    return prox


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse teacher assignment PDF and merge with driving routes."
    )
    parser.add_argument(
        "pdf", nargs="?", default=None,
        help="Path to the teacher assignment PDF (omit for routes-only mode)",
    )
    parser.add_argument(
        "--routes", required=True,
        help="Path to routes CSV (columns: Origen, Destino, Km, Tiempo)",
    )
    parser.add_argument(
        "--origen", required=True,
        help="Origin town (e.g. 'València')",
    )
    parser.add_argument(
        "--output", default=OUTPUT_FILE,
        help=f"Output CSV path (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    if args.pdf:
        # --- Full pipeline: PDF + routes ------------------------------------
        if not Path(args.pdf).is_file():
            print(f"Error: archivo no encontrado: {args.pdf}", file=sys.stderr)
            sys.exit(1)
        if not Path(args.routes).is_file():
            print(f"Error: CSV no encontrado: {args.routes}", file=sys.stderr)
            sys.exit(1)

        df = build_vacantes(args.pdf, args.routes, args.origen, args.output)
        if not df.empty:
            print("\n>>> Resultado final:")
            print(df.to_string(index=False))

    else:
        # --- Fallback: routes only, sorted by proximity ---------------------
        if not Path(args.routes).is_file():
            print(f"Error: CSV no encontrado: {args.routes}", file=sys.stderr)
            sys.exit(1)

        df = fallback_proximidad(args.routes, args.origen)
        if not df.empty:
            print("\n>>> Municipios por proximidad:")
            print(df.to_string(index=False))


if __name__ == "__main__":
    main()
