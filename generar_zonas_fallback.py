#!/usr/bin/env python3
"""
generar_zonas_fallback.py

Genera zonas.json a partir de areas_subareas.json y municipios_cv.json,
sin necesidad del PDF oficial de la Conselleria.

Lógica:
  - Código de municipio = 5 primeros dígitos de localidad_codigo
  - Área (3 dígitos) = 3 primeros dígitos de subarea_codigo
  - Subárea (4 dígitos) = subarea_codigo completo
  - Nombre y provincia se mapean desde municipios_cv.json

Uso:
    python generar_zonas_fallback.py
"""

import json
import unicodedata
import re
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

PROVINCIAS = {
    "03": "Alicante",
    "12": "Castellón",
    "46": "Valencia",
}


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        return ""
    s = name.lower().strip()
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main():
    with open(SCRIPT_DIR / "areas_subareas.json", encoding="utf-8") as f:
        areas_subareas = json.load(f)

    with open(SCRIPT_DIR / "municipios_cv.json", encoding="utf-8") as f:
        municipios_cv = json.load(f)

    muni_by_norm = {}
    for m in municipios_cv:
        muni_by_norm[_normalize_name(m["nombre"])] = m

    municipios_map: dict[str, dict] = {}

    for _centro_id, info in areas_subareas.items():
        loc_codigo = info.get("localidad_codigo", "")
        loc_nombre = info.get("localidad_nombre", "")
        subarea = info.get("subarea_codigo", "")

        if len(loc_codigo) < 5 or len(subarea) < 4:
            continue

        codigo_5d = loc_codigo[:5]
        area_3d = subarea[:3]

        if codigo_5d not in municipios_map:
            clean_name = loc_nombre.split(" - ")[0].strip()

            muni_entry = {
                "codigo_municipio": codigo_5d,
                "municipio": clean_name.upper(),
                "subarea": subarea,
                "subarea_nombre": "",
                "area": area_3d,
                "area_nombre": "",
                "provincia": PROVINCIAS.get(codigo_5d[:2], ""),
            }

            norm = _normalize_name(clean_name)
            if norm in muni_by_norm:
                m = muni_by_norm[norm]
                muni_entry["municipio"] = m["nombre"].upper()
                muni_entry["provincia"] = m["provincia"]

            municipios_map[codigo_5d] = muni_entry

    output = {"municipios": sorted(
        municipios_map.values(),
        key=lambda x: x["codigo_municipio"],
    )}

    output_path = SCRIPT_DIR / "zonas.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"Generated {len(municipios_map)} municipalities → {output_path}")

    provinces: dict[str, int] = {}
    for m in output["municipios"]:
        prov = m["provincia"]
        provinces[prov] = provinces.get(prov, 0) + 1

    for prov, count in sorted(provinces.items()):
        print(f"  {prov}: {count}")


if __name__ == "__main__":
    main()
