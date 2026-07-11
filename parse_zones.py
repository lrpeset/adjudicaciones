"""
parse_zones.py

Parses the official Conselleria d'Educació PDF "Llistat d'àrees, subàrees,
localitats i centres - Codi a utilitzar" (Annex I) to extract the mapping of
Areas, Subareas, and Municipalities for the Comunitat Valenciana.

The PDF is structured hierarchically:
  Province (03 Alacant, 12 Castelló, 46 València)
    Area (e.g., 031)
      Subarea (e.g., 0311)
        Municipality (e.g., 03018 ALTEA)

Usage:
    from parse_zones import parse_zones, get_zone_by_municipio

    # Parse PDF and export to JSON
    parse_zones("areas_subareas_localidades.pdf", "zonas.json")

    # Merge zones with adjudicacion data
    df_merged = get_zone_by_municipio(df_adjudicaciones, "zonas.json")
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd
from pypdf import PdfReader


# ---------------------------------------------------------------------------
# Province definitions
# ---------------------------------------------------------------------------
PROVINCES = {
    "03": "Alacant",
    "12": "Castelló",
    "46": "València",
}

# Province header patterns (standalone lines or with trailing text)
RE_PROVINCE = re.compile(
    r"^\s*(03)\s+ALACANT\b"
    r"|^\s*(12)\s+CASTELL[OÓ]\b"
    r"|^\s*(46)\s+VAL[ÈE]NCIA\b",
    re.IGNORECASE,
)

# Area header: 3-digit code (province prefix + 1 digit)
RE_AREA = re.compile(r"^\s*(03[1-9]|12[1-9]|46[1-9])\b")

# Subarea header: 4-digit code (province prefix + 2 digits)
RE_SUBAREA = re.compile(r"^\s*(03[1-9]\d|12[1-9]\d|46[1-9]\d)\b")

# Municipality entry: 5-digit code followed by name
# Municipality codes: province (2) + comarca (1) + sequential (2) = 5 digits
RE_MUNICIPALITY = re.compile(
    r"^\s*(03\d{3}|12\d{3}|46\d{3})\s+(.+?)\s*$"
)

# Lines to skip (headers, footers, page numbers, etc.)
RE_SKIP = re.compile(
    r"^\s*$"
    r"|^Pàg\s+\d+"
    r"|^LLISTAT"
    r"|^ANNEX"
    r"|^ANEXO"
    r"|^CODI\b"
    r"|^C[OÓ]DIGO\b"
    r"|^ÀREES?\b"
    r"|^ÁREAS?\b"
    r"|^SUBÀREES?\b"
    r"|^SUBÁREAS?\b"
    r"|^LOCALITATS?\b"
    r"|^LOCALIDADES?\b"
    r"|^CENTRES?\b"
    r"|^CENTROS?\b"
    r"|^PROVINCIA\b"
    r"|^COMUNITAT\b"
    r"|^COMUNIDAD\b"
    r"|^GENERALITAT\b"
    r"|^\d{2}/\d{2}/\d{4}\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# PDF text extraction
# ---------------------------------------------------------------------------

def _extract_text_pages(pdf_path: str) -> list[str]:
    """Return one string per page, preserving reading order."""
    reader = PdfReader(pdf_path)
    return [page.extract_text() or "" for page in reader.pages]


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

def _extract_trailing_text(line: str, code: str) -> str:
    """Extract descriptive text after a code prefix (e.g., '031 Marina Baixa' → 'Marina Baixa')."""
    stripped = line.strip()
    if stripped.startswith(code) and len(stripped) > len(code):
        rest = stripped[len(code):]
        if rest and rest[0] in (" ", "\t"):
            return rest.strip()
    return ""


def _classify_line(line: str, current_state: dict) -> tuple[str, dict]:
    """
    Classify a single line and update parsing state.

    Returns:
        (line_type, updated_state) where line_type is one of:
        "skip", "province", "area", "subarea", "municipality", "unknown"
    """
    stripped = line.strip()

    # Skip empty or header/footer lines
    if RE_SKIP.search(stripped):
        return "skip", current_state

    # Check for province header
    m = RE_PROVINCE.match(stripped)
    if m:
        code = m.group(1) or m.group(2) or m.group(3)
        state = current_state.copy()
        state["provincia_code"] = code
        state["provincia_name"] = PROVINCES[code]
        state["area"] = ""
        state["area_nombre"] = ""
        state["subarea"] = ""
        state["subarea_nombre"] = ""
        return "province", state

    # Check for subarea header (before area, since subarea is more specific)
    m = RE_SUBAREA.match(stripped)
    if m:
        code = m.group(1)
        # Validate: subarea must start with current province
        if code[:2] == current_state.get("provincia_code", ""):
            state = current_state.copy()
            state["subarea"] = code
            state["subarea_nombre"] = _extract_trailing_text(stripped, code)
            return "subarea", state

    # Check for area header
    m = RE_AREA.match(stripped)
    if m:
        code = m.group(1)
        # Validate: area must start with current province
        if code[:2] == current_state.get("provincia_code", ""):
            state = current_state.copy()
            state["area"] = code
            state["area_nombre"] = _extract_trailing_text(stripped, code)
            state["subarea"] = ""
            state["subarea_nombre"] = ""
            return "area", state

    # Check for municipality entry
    m = RE_MUNICIPALITY.match(stripped)
    if m:
        code = m.group(1)
        name = m.group(2).strip()
        # Validate: municipality must start with current province
        if code[:2] == current_state.get("provincia_code", ""):
            return "municipality", {
                "code": code,
                "name": name,
                "area": current_state.get("area", ""),
                "area_nombre": current_state.get("area_nombre", ""),
                "subarea": current_state.get("subarea", ""),
                "subarea_nombre": current_state.get("subarea_nombre", ""),
                "provincia_code": current_state.get("provincia_code", ""),
                "provincia_name": current_state.get("provincia_name", ""),
            }

    return "unknown", current_state


# ---------------------------------------------------------------------------
# Multi-line municipality handling
# ---------------------------------------------------------------------------

def _try_merge_municipality(lines: list[str], idx: int, state: dict) -> tuple[dict | None, int]:
    """
    Try to parse a municipality entry, handling multi-line cases.

    Some PDFs may split municipality entries across lines, e.g.:
        03018
        ALTEA

    Returns:
        (municipality_dict_or_None, lines_consumed)
    """
    line = lines[idx].strip()

    # Direct match: code + name on same line
    m = RE_MUNICIPALITY.match(line)
    if m:
        code = m.group(1)
        name = m.group(2).strip()
        if code[:2] == state.get("provincia_code", ""):
            return {
                "codigo_municipio": code,
                "municipio": name.upper(),
                "subarea": state.get("subarea", ""),
                "subarea_nombre": state.get("subarea_nombre", ""),
                "area": state.get("area", ""),
                "area_nombre": state.get("area_nombre", ""),
                "provincia": state.get("provincia_name", ""),
            }, 1

    # Split match: code on one line, name on next
    m_code = re.match(r"^\s*(03\d{3}|12\d{3}|46\d{3})\s*$", line)
    if m_code and idx + 1 < len(lines):
        code = m_code.group(1)
        next_line = lines[idx + 1].strip()
        if (next_line
                and not RE_PROVINCE.match(next_line)
                and not RE_AREA.match(next_line)
                and not RE_SUBAREA.match(next_line)
                and not RE_SKIP.search(next_line)
                and code[:2] == state.get("provincia_code", "")):
            return {
                "codigo_municipio": code,
                "municipio": next_line.upper(),
                "subarea": state.get("subarea", ""),
                "subarea_nombre": state.get("subarea_nombre", ""),
                "area": state.get("area", ""),
                "area_nombre": state.get("area_nombre", ""),
                "provincia": state.get("provincia_name", ""),
            }, 2

    return None, 0


# ---------------------------------------------------------------------------
# Main parsing function
# ---------------------------------------------------------------------------

def parse_zones_text(pdf_path: str) -> list[dict]:
    """
    Parse the zones PDF and return a list of municipality mappings.

    Each entry contains:
        - codigo_municipio: 5-digit code (e.g., "03018")
        - municipio: uppercase name (e.g., "ALTEA")
        - subarea: 4-digit code (e.g., "0311")
        - area: 3-digit code (e.g., "031")
        - provincia: name (e.g., "Alacant")
    """
    pages = _extract_text_pages(pdf_path)
    all_text = "\n".join(pages)
    lines = all_text.splitlines()

    state = {
        "provincia_code": "",
        "provincia_name": "",
        "area": "",
        "area_nombre": "",
        "subarea": "",
        "subarea_nombre": "",
    }

    municipalities = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Classify the line
        line_type, new_state = _classify_line(line, state)

        if line_type == "province":
            state = new_state
        elif line_type == "area":
            state = new_state
        elif line_type == "subarea":
            state = new_state
        elif line_type == "municipality":
            # new_state is actually the municipality dict from _classify_line
            municipalities.append({
                "codigo_municipio": new_state["code"],
                "municipio": new_state["name"].upper(),
                "subarea": new_state["subarea"],
                "subarea_nombre": new_state["subarea_nombre"],
                "area": new_state["area"],
                "area_nombre": new_state["area_nombre"],
                "provincia": new_state["provincia_name"],
            })
        elif line_type == "skip":
            pass
        else:
            # Try multi-line municipality match
            muni, consumed = _try_merge_municipality(lines, i, state)
            if muni:
                municipalities.append(muni)
                i += consumed - 1  # -1 because loop increments

        i += 1

    return municipalities


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def parse_zones(pdf_path: str, output_path: str = "zonas.json") -> list[dict]:
    """
    Parse the zones PDF and export to a standardized JSON file.

    Args:
        pdf_path: Path to the official zones PDF.
        output_path: Path for the output JSON file (default: "zonas.json").

    Returns:
        List of municipality mapping dicts.
    """
    municipalities = parse_zones_text(pdf_path)

    output = {"municipios": municipalities}

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    return municipalities


# ---------------------------------------------------------------------------
# Helper: merge zones with adjudicacion data
# ---------------------------------------------------------------------------

def get_zone_by_municipio(
    df_adjudicaciones: pd.DataFrame,
    zonas_json_path: str,
) -> pd.DataFrame:
    """
    Merge adjudicacion DataFrame with zones JSON mapping.

    Links each row to its Area, Subarea, and Province based on the
    municipality name or code.

    Args:
        df_adjudicaciones: DataFrame from adjudicacion.parse_adjudicacion().
        zonas_json_path: Path to the zonas.json file.

    Returns:
        DataFrame with added columns: 'Zona_Area', 'Zona_Subarea', 'Zona_Subarea_Nombre', 'Zona_Provincia'.
    """
    with open(zonas_json_path, "r", encoding="utf-8") as f:
        zonas = json.load(f)

    df_zonas = pd.DataFrame(zonas["municipios"])

    if df_zonas.empty:
        for col in ["Zona_Area", "Zona_Subarea", "Zona_Subarea_Nombre", "Zona_Provincia"]:
            df_adjudicaciones[col] = ""
        return df_adjudicaciones

    # Normalize municipality names for matching
    df_zonas["_muni_norm"] = (
        df_zonas["municipio"]
        .str.upper()
        .str.strip()
    )

    # Create lookup by code (first 5 chars of Centro_Código = municipality code)
    code_to_zone = {}
    for _, row in df_zonas.iterrows():
        code_to_zone[row["codigo_municipio"]] = {
            "Zona_Area": row["area"],
            "Zona_Subarea": row["subarea"],
            "Zona_Subarea_Nombre": row.get("subarea_nombre", ""),
            "Zona_Provincia": row["provincia"],
        }

    # Create lookup by name (normalized)
    name_to_zone = {}
    for _, row in df_zonas.iterrows():
        name_to_zone[row["_muni_norm"]] = {
            "Zona_Area": row["area"],
            "Zona_Subarea": row["subarea"],
            "Zona_Subarea_Nombre": row.get("subarea_nombre", ""),
            "Zona_Provincia": row["provincia"],
        }

    result = df_adjudicaciones.copy()

    # Initialize zone columns
    result["Zona_Area"] = ""
    result["Zona_Subarea"] = ""
    result["Zona_Subarea_Nombre"] = ""
    result["Zona_Provincia"] = ""

    # Match by municipality name (primary strategy)
    # NOTE: Code-based matching by Centro_Código[:5] was removed because the
    # 8-digit school code does NOT contain the municipality code in its first
    # 5 digits. The correct municipality code lives in localidad_codigo from
    # areas_subareas.json — use inject_coordinates() from match_coords.py for
    # code-based coordinate injection.
    for idx, row in result.iterrows():
        muni_name = str(row.get("Municipio", "")).upper().strip()
        if muni_name in name_to_zone:
            zone = name_to_zone[muni_name]
            result.at[idx, "Zona_Area"] = zone["Zona_Area"]
            result.at[idx, "Zona_Subarea"] = zone["Zona_Subarea"]
            result.at[idx, "Zona_Subarea_Nombre"] = zone["Zona_Subarea_Nombre"]
            result.at[idx, "Zona_Provincia"] = zone["Zona_Provincia"]

    # Drop helper column
    result.drop(columns=["_muni_norm"], errors="ignore", inplace=True)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python parse_zones.py <pdf_path> [output_json]")
        print("  pdf_path:    Path to the official zones PDF")
        print("  output_json: Output JSON path (default: zonas.json)")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else "zonas.json"

    if not Path(pdf_path).exists():
        print(f"Error: PDF file not found: {pdf_path}")
        sys.exit(1)

    municipalities = parse_zones(pdf_path, output_path)

    # Summary
    provinces = {}
    for m in municipalities:
        prov = m["provincia"]
        provinces[prov] = provinces.get(prov, 0) + 1

    print(f"Parsed {len(municipalities)} municipalities from {pdf_path}")
    print(f"Output written to {output_path}")
    for prov, count in sorted(provinces.items()):
        print(f"  {prov}: {count} municipalities")


if __name__ == "__main__":
    main()
