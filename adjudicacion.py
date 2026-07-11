"""
adjudicacion.py

Parses the "Adjudicación de personal docente" PDF and provides
multi-criteria filtering for teacher assignment positions.

The PDF uses changing header values (CUERPO/COS, ESPECIALIDAD/ESPECIALITAT,
PROVINCIA/PROVINCIA) that apply to subsequent data rows. Each data row
contains: index, Tipo (VACANTE/SUSTITUCIÓN INDETERMINADA), location,
school code, school name, position code (Lloc), ITI (SI/NO),
Observaciones, and optional linguistic requirements.

Usage:
    from adjudicacion import parse_adjudicacion, filter_positions

    df = parse_adjudicacion("lis_vac_adj_ini_26_27.pdf")
    result = filter_positions(df, especialidad="120", tipo="VACANTE")
"""

import io
import re
import unicodedata
from typing import Union

import pandas as pd
from pypdf import PdfReader

# Tipo unificado: ruta de archivo, bytes crudos o stream en memoria
PdfSource = Union[str, bytes, io.BytesIO]


# ---------------------------------------------------------------------------
# Patterns for header fields that change per specialty/province section
# ---------------------------------------------------------------------------
RE_CUERPO = re.compile(
    r"CUERPO/COS:\s*(.+)", re.IGNORECASE
)
RE_ESPECIALIDAD = re.compile(
    r"ESPECIALIDAD/ESPECIALITAT:\s*(.+)", re.IGNORECASE
)

# Province sub-headers: standalone lines matching known province names
PROVINCES = {"Alacant", "Castelló", "València"}
RE_PROVINCE = re.compile(
    r"^(Alacant|Castelló|València)\s*$"
)

# Column header line (skip during parsing)
RE_COL_HEADER = re.compile(
    r"LOCALIDAD\s*/\s*LOCALITAT", re.IGNORECASE
)

# Page header/footer lines to skip
RE_SKIP = re.compile(
    r"^Pàg\s+\d+"
    r"|^ADJUDICACI"
    r"|^Avgda\."
    r"|^Llocs Ofertats"
    r"|^\d{2}/\d{2}/\d{4}\s*$"
    r"|^ADJUDICACIÓN DE PERSONAL DOCENTE",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Patterns for data row extraction
# ---------------------------------------------------------------------------
# Row format: "序号 VACANTE或SUSTITUCIÓN INDETERMINADA 地点 - 8位学校代码 - 学校名称 [6-7位岗位代码] ITI值 [OBSERV.] [REQ.LING.]"
# NOTE: In the PDF text, VACANTE is often directly concatenated with the
# location (e.g., "VACANTEBENIDORM") without a space. The location may contain
# dashes separating municipality, school code, and school name. The lloc code
# is optional and may be absent.
RE_ROW = re.compile(
    r"^(\d+)\s+"                                    # index
    r"(?:VACANTE|SUSTITUCI[OÓ]N\s+INDETERMINADA)"   # tipo (consumed, not captured)
    r"\s*(.+?)"                                     # location + school + optional lloc
    r"(?:\s+(\d{6,7}))?"                            # lloc code (optional, captured)
    r"\s+(SI|NO)\s*"                                # ITI
    r"(.*)$",                                       # rest (observaciones + req. ling.)
    re.IGNORECASE,
)

# School code pattern (handles spaces around dashes)
RE_CODIGO = re.compile(r"-\s*(03\d{6}|12\d{6}|46\d{6})\s*-")

# Linguistic requirement pattern (e.g., ING-B2, FRN-A2, etc.)
RE_LING = re.compile(
    r"\b(ING|FRN|ALE|ITA|POR|CHI|JAP|RUS|VAL|CAT)\s*-\s*([A-Z0-9]+)\.?",
    re.IGNORECASE,
)

# Observation keywords to tag
OBSERV_KEYWORDS = [
    ("Centre singular", "Centre singular"),
    ("Lloc difícil provisió", "Lloc difícil provisió"),
    ("Lloc d'esp. dificultat", "Lloc d'esp. dificultat"),
    ("PFQB", "PFQB"),
    ("TVA", "TVA"),
    ("CENTRE PENITENCIARI", "CENTRE PENITENCIARI"),
    ("Infantil 0 a 3", "Infantil 0 a 3"),
]


# ---------------------------------------------------------------------------
# Phase 1: PDF Parsing
# ---------------------------------------------------------------------------

def _open_pdf(source: PdfSource) -> PdfReader:
    """Open a PDF from a file path, raw bytes, or an in-memory stream."""
    if isinstance(source, (str,)):
        return PdfReader(source)
    if isinstance(source, bytes):
        return PdfReader(io.BytesIO(source))
    if isinstance(source, io.BytesIO):
        return PdfReader(source)
    raise TypeError(f"Tipo de fuente PDF no soportado: {type(source)}")


def _extract_text_pages(pdf_source: PdfSource) -> list[str]:
    """Return one string per page, preserving reading order."""
    reader = _open_pdf(pdf_source)
    return [page.extract_text() or "" for page in reader.pages]


def _parse_row(line: str, especialidad: str, provincia: str, cuerpo: str) -> dict | None:
    """Parse a single data row line into a structured dict."""
    m = RE_ROW.match(line)
    if not m:
        return None

    idx, location_text, lloc, iti, rest = m.groups()

    # Determine tipo from original line text
    line_upper = line.upper()
    if "SUSTITUCI" in line_upper:
        tipo = "SUSTITUCIÓN INDETERMINADA"
    else:
        tipo = "VACANTE"

    # Extract school code from location text
    code_match = RE_CODIGO.search(location_text)
    codigo = code_match.group(1) if code_match else None

    # Extract municipality: everything before the first -NNNNNNNN-
    if code_match:
        raw_muni = location_text[: code_match.start()]
    else:
        raw_muni = location_text

    # Strip leading index remnants and separators
    raw_muni = re.sub(r"^\d+\s*[\|\-]\s*", "", raw_muni)
    raw_muni = re.sub(r"[\|\-]+$", "", raw_muni).strip()
    municipio = raw_muni.strip()

    # Extract school name: text after code, up to lloc
    centro_nombre = ""
    if code_match:
        after_code = location_text[code_match.end():]
        # Remove trailing lloc from the text
        centro_nombre = re.sub(r"\s*\d{6,7}\s*$", "", after_code).strip()

    # Parse rest: observations + linguistic requirements
    rest = rest.strip()

    # Extract linguistic requirement
    ling_match = RE_LING.search(rest)
    req_ling = ling_match.group(0).strip().rstrip(".") if ling_match else ""

    # Extract observations: everything in rest except the linguistic requirement
    observaciones = rest
    if ling_match:
        observaciones = rest[: ling_match.start()].strip()
    # Also handle " - LLOC\nD'ESP. DIFICULTAT" continuation
    observaciones = re.sub(r"\s*-\s*LLOC\s*$", "", observaciones).strip()

    # Tag observation keywords
    obs_tags = []
    for keyword, tag in OBSERV_KEYWORDS:
        if keyword.lower() in observaciones.lower():
            obs_tags.append(tag)

    return {
        "Índex": int(idx),
        "Tipo": tipo,
        "Municipio": municipio,
        "Centro_Código": codigo,
        "Centro_Nombre": centro_nombre,
        "Lloc": lloc or "",
        "ITI": iti.upper(),
        "Observaciones": observaciones,
        "Obs_Tags": obs_tags,
        "Req_Lingüístic": req_ling,
        "Especialidad": especialidad,
        "Provincia": provincia,
        "Cos": cuerpo,
    }


def parse_adjudicacion(pdf_source: PdfSource) -> pd.DataFrame:
    """
    Parse the teacher assignment PDF and return a structured DataFrame.

    Each row represents a single teacher position with all relevant fields
    extracted and normalized.

    Parameters
    ----------
    pdf_source : str | bytes | BytesIO
        File path, raw PDF bytes, or an in-memory BytesIO stream.
    """
    current_cuerpo = ""
    current_especialidad = ""
    current_provincia = ""

    rows = []

    for page_text in _extract_text_pages(pdf_source):
        prev_line = ""
        for raw_line in page_text.splitlines():
            line = raw_line.strip()
            if not line:
                prev_line = ""
                continue

            # Skip page headers/footers and column headers
            if RE_SKIP.search(line) or RE_COL_HEADER.search(line):
                prev_line = line
                continue

            # Check for CUERPO/COS header
            m = RE_CUERPO.search(line)
            if m:
                current_cuerpo = m.group(1).strip()
                prev_line = line
                continue

            # Check for ESPECIALIDAD header
            m = RE_ESPECIALIDAD.search(line)
            if m:
                current_especialidad = m.group(1).strip()
                prev_line = line
                continue

            # Check for province sub-header
            m = RE_PROVINCE.match(line)
            if m:
                current_provincia = m.group(1).strip()
                prev_line = line
                continue

            # Try to parse as a data row
            row = _parse_row(
                line, current_especialidad, current_provincia, current_cuerpo
            )
            if row:
                rows.append(row)
            else:
                # Handle multi-line observations (e.g., "CENTRE PENITENCIARI  - LLOC")
                # that continue on the next line as "D'ESP. DIFICULTAT"
                if rows and ("LLOC" in line.upper() or "DIFICULTAT" in line.lower()):
                    last = rows[-1]
                    extra = line.strip()
                    # Append to observations
                    if last["Observaciones"]:
                        last["Observaciones"] += " " + extra
                    else:
                        last["Observaciones"] = extra
                    # Re-tag
                    last["Obs_Tags"] = []
                    for keyword, tag in OBSERV_KEYWORDS:
                        if keyword.lower() in last["Observaciones"].lower():
                            last["Obs_Tags"].append(tag)

            prev_line = line

    df = pd.DataFrame(rows)
    return df


# ---------------------------------------------------------------------------
# Phase 2: Core Mandatory Filters
# ---------------------------------------------------------------------------

def _extract_base_muni_name(muni_name: str) -> str:
    """
    Extract the core base municipality name from a string that may contain
    sub-localities or pedanías attached via dashes.

    Uses a regex split on ANY type of dash (ASCII hyphen, en-dash, em-dash)
    or slash surrounded by optional whitespace.

    Examples:
        "ELX - ALTABIX"          -> "ELX"
        "ALMORADÍ - HEREDADES"   -> "ALMORADÍ"
        "ALMORADÍ–HEREDADES"     -> "ALMORADÍ"   (en-dash)
        "ALBAL"                  -> "ALBAL"
    """
    if not isinstance(muni_name, str):
        return muni_name
    parts = re.split(r"\s*[\-\u2010-\u2015\/]\s*", muni_name.strip())
    return parts[0].strip() if parts else muni_name.strip()


def filter_by_especialidad(
    df: pd.DataFrame, especialidad: str
) -> pd.DataFrame:
    """
    Filter by specialty code or name (exact match on code prefix).

    Examples:
        "120" matches "120 - EDUCACIÓN INFANTIL"
        "3A1" matches "3A1 - COCINA Y PASTELERÍA"
        "120 - EDUCACIÓN INFANTIL" exact match
    """
    especialidad_upper = especialidad.upper().strip()

    # Try exact match first
    mask = df["Especialidad"].str.upper() == especialidad_upper
    if mask.any():
        return df[mask].copy()

    # Try code prefix match (e.g., "120" matches "120 - EDUCACIÓN INFANTIL")
    mask = df["Especialidad"].str.upper().str.startswith(especialidad_upper + " -")
    if mask.any():
        return df[mask].copy()

    # Try partial match
    mask = df["Especialidad"].str.upper().str.contains(especialidad_upper, na=False)
    return df[mask].copy()


def filter_by_tipo(df: pd.DataFrame, tipo: str) -> pd.DataFrame:
    """
    Filter by assignment type.

    Args:
        tipo: "VACANTE" or "SUSTITUCIÓN INDETERMINADA" (or partial match)
    """
    tipo_upper = tipo.upper().strip()
    if tipo_upper == "VACANTE":
        return df[df["Tipo"] == "VACANTE"].copy()
    elif "SUSTITUCI" in tipo_upper:
        return df[df["Tipo"] == "SUSTITUCIÓN INDETERMINADA"].copy()
    else:
        return df[df["Tipo"].str.upper().str.contains(tipo_upper, na=False)].copy()


# ---------------------------------------------------------------------------
# Phase 3: Advanced & Conditional Filters
# ---------------------------------------------------------------------------

def filter_by_iti(df: pd.DataFrame, iti: str) -> pd.DataFrame:
    """
    Filter by itinerancy (ITI/COMP.).

    Args:
        iti: "SI" or "NO"
    """
    return df[df["ITI"] == iti.upper().strip()].copy()


def filter_by_req_lingüístic(df: pd.DataFrame, req: str) -> pd.DataFrame:
    """
    Filter by linguistic requirement (e.g., "ING-B2", "FRN-A2").

    Uses partial matching so "ING" matches any English requirement.
    """
    req_upper = req.upper().strip()
    mask = df["Req_Lingüístic"].str.upper().str.contains(req_upper, na=False)
    return df[mask].copy()


def filter_by_observaciones(df: pd.DataFrame, keyword: str) -> pd.DataFrame:
    """
    Filter by observation keyword.

    Matches against both the raw Observaciones text and the Obs_Tags field.

    Obs_Tags may be a raw list or a comma-separated string (after cache
    sanitisation). Both forms are handled transparently.

    Useful keywords:
        - "Centre singular"
        - "Lloc difícil provisió"
        - "Lloc d'esp. dificultat"
        - "PFQB"
        - "TVA"
        - "CENTRE PENITENCIARI"
        - "Infantil 0 a 3"
    """
    keyword_upper = keyword.upper().strip()

    # Check Obs_Tags first (faster, pre-tagged)
    def _has_tag(tags):
        if isinstance(tags, list):
            return any(keyword_upper in t.upper() for t in tags)
        if isinstance(tags, str):
            return any(
                keyword_upper in t.strip().upper()
                for t in tags.split(",")
                if t.strip()
            )
        return False

    mask_tags = df["Obs_Tags"].apply(_has_tag)
    if mask_tags.any():
        return df[mask_tags].copy()

    # Fallback to raw text search
    mask_text = df["Observaciones"].str.upper().str.contains(keyword_upper, na=False)
    return df[mask_text].copy()


# ---------------------------------------------------------------------------
# Phase 4: Query Logic & Output Standardization
# ---------------------------------------------------------------------------

def _match_any(value: str, targets: str | list[str]) -> bool:
    """Return True if *value* matches any of the *targets* (case-insensitive)."""
    if isinstance(targets, str):
        targets = [targets]
    value_upper = value.upper() if isinstance(value, str) else ""
    for t in targets:
        t_upper = t.upper().strip()
        if t_upper in value_upper or value_upper.startswith(t_upper + " -"):
            return True
    return False


def filter_positions(
    df: pd.DataFrame,
    especialidad: str | list[str] | None = None,
    tipo: str | list[str] | None = None,
    iti: str | None = None,
    req_lingüístic: str | None = None,
    observaciones: str | None = None,
    municipi: str | None = None,
    provincia: str | None = None,
) -> pd.DataFrame:
    """
    Apply multiple filters in sequence (AND logic).

    All filters are optional. When provided, they are stacked:

        result = filter_positions(
            df,
            especialidad=["120", "3A1"],
            tipo="VACANTE",
            observaciones="Infantil 0 a 3",
        )

    Parameters
    ----------
    df : DataFrame
        Raw parsed data from `parse_adjudicacion()`.
    especialidad : str | list[str], optional
        Specialty code(s) or name(s). A list uses OR within the category.
        Examples: "120", ["120", "3A1 - COCINA Y PASTELERÍA"].
    tipo : str | list[str], optional
        "VACANTE" or "SUSTITUCIÓN INDETERMINADA". A list uses OR.
    iti : str, optional
        "SI" or "NO".
    req_lingüístic : str, optional
        Linguistic requirement (e.g., "ING-B2").
    observaciones : str, optional
        Observation keyword (e.g., "Centre singular", "TVA").
    municipi : str, optional
        Municipality name (partial, case-insensitive).
    provincia : str, optional
        Province name: "Alacant", "Castelló", or "València".

    Returns
    -------
    DataFrame
        Filtered copy of the input DataFrame.
    """
    result = df.copy()

    if especialidad:
        if isinstance(especialidad, list):
            mask = result["Especialidad"].apply(
                lambda v: _match_any(v, especialidad)
            )
            result = result[mask].copy()
        else:
            result = filter_by_especialidad(result, especialidad)
    if tipo:
        if isinstance(tipo, list):
            mask = result["Tipo"].apply(lambda v: _match_any(v, tipo))
            result = result[mask].copy()
        else:
            result = filter_by_tipo(result, tipo)
    if iti:
        result = filter_by_iti(result, iti)
    if req_lingüístic:
        result = filter_by_req_lingüístic(result, req_lingüístic)
    if observaciones:
        result = filter_by_observaciones(result, observaciones)
    if municipi:
        search = municipi.upper().strip()
        base_search = _extract_base_muni_name(search.upper()).strip()
        mask = result["Municipio"].str.upper().str.contains(
            search, na=False
        )
        if not mask.any() and base_search != search:
            mask = result["Municipio"].apply(
                lambda v: _extract_base_muni_name(str(v).upper()).strip() == base_search
            )
        result = result[mask].copy()
    if provincia:
        mask = result["Provincia"].str.upper() == provincia.upper().strip()
        result = result[mask].copy()

    return result


def get_especialidades(df: pd.DataFrame) -> list[str]:
    """Return sorted list of unique specialties in the dataset."""
    return sorted(df["Especialidad"].unique().tolist())


def get_observacion_tags(df: pd.DataFrame) -> list[str]:
    """Return sorted list of unique observation tags in the dataset.

    Handles both raw list values (pre-cache) and comma-separated strings
    (post-cache, after _sanitize_for_cache joins lists with ', ').
    """
    all_tags = set()
    for tags in df["Obs_Tags"]:
        if isinstance(tags, list):
            all_tags.update(tags)
        elif isinstance(tags, str) and tags:
            all_tags.update(t.strip() for t in tags.split(",") if t.strip())
    return sorted(all_tags)


def to_json(df: pd.DataFrame) -> str:
    """Export filtered DataFrame to JSON (records format)."""
    export = df.copy()
    # Convert Obs_Tags list to comma-separated string for JSON
    export["Obs_Tags"] = export["Obs_Tags"].apply(
        lambda x: ",".join(x) if isinstance(x, list) else (x or "")
    )
    return export.to_json(orient="records", force_ascii=False, indent=2)


def to_clean_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a display-ready DataFrame with user-friendly column names
    and Obs_Tags as a comma-separated string.
    """
    export = df.copy()
    export["Obs_Tags"] = export["Obs_Tags"].apply(
        lambda x: ", ".join(x) if isinstance(x, list) else (x or "")
    )
    export = export.rename(columns={
        "Índex": "#",
        "Centro_Código": "Codi Centre",
        "Centro_Nombre": "Centre",
        "Req_Lingüístic": "Req. Ling.",
        "Obs_Tags": "Etiquetes",
    })
    return export
