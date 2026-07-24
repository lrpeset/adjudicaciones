"""Genera la baseline determinista del PDF oficial sin realizar llamadas de red."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import adjudicacion
from match_coords import inject_coordinates


PDF_PATH = PROJECT_ROOT / "lis_vac_adj_ini_26_27.pdf"
MUNICIPIOS_PATH = PROJECT_ROOT / "municipios_cv.json"
ZONAS_PATH = PROJECT_ROOT / "zonas.json"
EXPECTED_PATH = (
    PROJECT_ROOT / "tests" / "baselines" / "lis_vac_adj_ini_26_27.json"
)
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "artifacts" / "baseline"
SCHEMA_VERSION = 1
RAW_INDEX_RE = re.compile(r"^\s*(\d{1,5})\b", re.MULTILINE)

IDENTITY_COLUMNS = [
    "Índex",
    "Municipio",
    "Centro_Código",
    "Centro_Nombre",
    "Lloc",
    "Especialidad",
    "Provincia",
    "Cos",
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().eq("")


def _identity_frame(df: pd.DataFrame, mask: pd.Series) -> pd.DataFrame:
    columns = [column for column in IDENTITY_COLUMNS if column in df.columns]
    return df.loc[mask, columns].sort_values("Índex").reset_index(drop=True)


def _write_reports(
    output_dir: Path,
    summary: dict,
    parsed: pd.DataFrame,
    enriched: pd.DataFrame,
    missing_indices: list[int],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    pd.DataFrame({"Índex": missing_indices}).to_csv(
        output_dir / "missing_indices.csv", index=False, encoding="utf-8"
    )

    lloc_missing = _blank(parsed["Lloc"])
    _identity_frame(parsed, lloc_missing).to_csv(
        output_dir / "missing_lloc.csv", index=False, encoding="utf-8"
    )

    code_missing = _blank(parsed["Centro_Código"])
    name_missing = _blank(parsed["Centro_Nombre"])
    incomplete = _identity_frame(parsed, code_missing | name_missing)
    incomplete["Falta_Código"] = incomplete["Centro_Código"].isna() | (
        incomplete["Centro_Código"].astype(str).str.strip().eq("")
    )
    incomplete["Falta_Nombre"] = incomplete["Centro_Nombre"].isna() | (
        incomplete["Centro_Nombre"].astype(str).str.strip().eq("")
    )
    incomplete.to_csv(
        output_dir / "incomplete_centers.csv", index=False, encoding="utf-8"
    )

    subarea_missing = _blank(enriched["Zona_Subarea"])
    _identity_frame(enriched, subarea_missing).to_csv(
        output_dir / "unresolved_subareas.csv", index=False, encoding="utf-8"
    )

    coords_missing = enriched["Latitud_Destino"].isna() | enriched[
        "Longitud_Destino"
    ].isna()
    unresolved_coords = _identity_frame(enriched, coords_missing)
    unresolved_coords["Latitud_Destino"] = enriched.loc[
        coords_missing, "Latitud_Destino"
    ].reset_index(drop=True)
    unresolved_coords["Longitud_Destino"] = enriched.loc[
        coords_missing, "Longitud_Destino"
    ].reset_index(drop=True)
    unresolved_coords.to_csv(
        output_dir / "unresolved_coordinates.csv", index=False, encoding="utf-8"
    )


def generate_baseline(output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict:
    """Ejecuta la caracterización completa y devuelve su resumen estable."""
    pages = adjudicacion._extract_text_pages(str(PDF_PATH))
    raw_indices = {
        int(value)
        for page in pages
        for value in RAW_INDEX_RE.findall(page)
    }
    if not raw_indices:
        raise AssertionError("El PDF no contiene índices detectables")

    first_raw = min(raw_indices)
    last_raw = max(raw_indices)
    expected_raw = set(range(first_raw, last_raw + 1))
    raw_gaps = sorted(expected_raw - raw_indices)

    # Evita extraer dos veces las 742 páginas: se prueba la apertura arriba y
    # se reutiliza exactamente el texto obtenido para ejercitar el parser.
    with patch.object(adjudicacion, "_extract_text_pages", return_value=pages):
        parsed = adjudicacion.parse_adjudicacion(str(PDF_PATH))

    parsed_indices = {int(value) for value in parsed["Índex"].dropna()}
    missing_indices = sorted(expected_raw - parsed_indices)
    duplicate_indices = int(parsed["Índex"].duplicated().sum())

    # inject_coordinates solo lee JSON locales. Se captura su salida diagnóstica
    # para mantener el informe de CI estable.
    with contextlib.redirect_stdout(io.StringIO()):
        enriched = inject_coordinates(
            parsed,
            str(MUNICIPIOS_PATH),
            str(ZONAS_PATH),
        )

    code_missing = _blank(parsed["Centro_Código"])
    name_missing = _blank(parsed["Centro_Nombre"])
    subarea_missing = _blank(enriched["Zona_Subarea"])
    coords_missing = enriched["Latitud_Destino"].isna() | enriched[
        "Longitud_Destino"
    ].isna()

    summary = {
        "schema_version": SCHEMA_VERSION,
        "input": {
            "path": PDF_PATH.name,
            "sha256": _sha256(PDF_PATH),
            "bytes": PDF_PATH.stat().st_size,
            "pages": len(pages),
        },
        "source": {
            "first_index": first_raw,
            "last_index": last_raw,
            "unique_indices": len(raw_indices),
            "gaps": len(raw_gaps),
        },
        "parser": {
            "rows": len(parsed),
            "unique_indices": len(parsed_indices),
            "duplicate_indices": duplicate_indices,
            "missing_indices": len(missing_indices),
            "missing_lloc": int(_blank(parsed["Lloc"]).sum()),
            "missing_center_code": int(code_missing.sum()),
            "missing_center_name": int(name_missing.sum()),
            "incomplete_centers": int((code_missing | name_missing).sum()),
        },
        "enrichment": {
            "unresolved_subareas": int(subarea_missing.sum()),
            "unresolved_coordinates": int(coords_missing.sum()),
        },
    }

    _write_reports(output_dir, summary, parsed, enriched, missing_indices)
    return summary


def assert_expected(summary: dict, expected_path: Path = EXPECTED_PATH) -> None:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if summary != expected:
        actual_text = json.dumps(summary, ensure_ascii=False, indent=2)
        expected_text = json.dumps(expected, ensure_ascii=False, indent=2)
        raise AssertionError(
            "La baseline del PDF ha cambiado.\n"
            f"Esperada:\n{expected_text}\n\nActual:\n{actual_text}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directorio para los informes detallados",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Compara el resultado con la baseline versionada",
    )
    args = parser.parse_args()

    summary = generate_baseline(args.output_dir)
    if args.check:
        assert_expected(summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
