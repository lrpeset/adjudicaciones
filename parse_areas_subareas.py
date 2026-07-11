#!/usr/bin/env python3
"""
parse_areas_subareas.py

Parsea el PDF 2013_6484_adj_mestres.pdf (Annex I de la Generalitat Valenciana)
y genera areas_subareas.json indexado por código de centro (8 dígitos).

Jerarquía del documento:
  Área (2 dígitos provincia) > Subárea (4 dígitos) > Localidad (9 dígitos) > Centro (8 dígitos)
"""

import json
import re
import sys
from pathlib import Path

from pypdf import PdfReader

PDF_PATH = Path(__file__).parent / "2013_6484_adj_mestres.pdf"
OUTPUT_PATH = Path(__file__).parent / "areas_subareas.json"

# Patrones de extracción
RE_SUBAREA = re.compile(r"SUBÀREA\s*/\s*SUBÁREA\s*:\s*(\d{4})", re.IGNORECASE)
RE_LOCALITAT_HEADER = re.compile(
    r"(.+?)LOCALITAT\s*/\s*LOCALIDAD", re.IGNORECASE
)
RE_LOCALIDAD_CODIGO = re.compile(r"(?<!\d)(\d{9})(?!\d)")
RE_CENTRO = re.compile(r"(.+?)\s+(03\d{6}|12\d{6}|46\d{6})\s*(.*)")
RE_PROGRAMA = re.compile(r"\b(PEV(?:/PIP)?|PIP(?:/PIL)?|PIL|ZC|CAES)\b")

# Líneas de ruido a filtrar (encabezados de página repetitivos)
HEADER_NOISE = re.compile(
    r"ANNEX\s*I|LLISTAT\s+DE|LISTADO\s+DE|CODI\s+A\s+UTILITZAR|"
    r"ÀREA\s*/\s*ÁREA|DIARIOOFICIAL|DOCV|\d+\s*$|^\d+$",
    re.IGNORECASE,
)


def extraer_paginas(pdf_path: Path) -> list[str]:
    """Extrae texto plano página a página del PDF."""
    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def es_linea_ruido(linea: str) -> bool:
    """Determina si una línea es ruido (encabezado, número de página, etc.)."""
    if not linea.strip():
        return True
    if HEADER_NOISE.search(linea):
        return True
    # Líneas que solo contienen código de área/provincia (ej: "Alacant 03")
    if re.match(r"^(Alacant|Castelló|València|Alicante|Castellón|Valencia)\s+\d{2}$", linea, re.IGNORECASE):
        return True
    return False


def limpiar_nombre(nombre: str) -> str:
    """Limpia espacios dobles y bordes de un nombre."""
    return re.sub(r"\s+", " ", nombre).strip()


def parsear_pdf(pdf_path: Path) -> dict:
    """
    Lee el PDF y devuelve un diccionario indexado por código de centro.

    Usa una máquina de estados con búfer que procesa cada bloque al
    encontrar un código de centro (8 dígitos).
    """
    paginas = extraer_paginas(pdf_path)
    resultado = {}

    subarea_actual = ""
    localidad_codigo = ""
    localidad_nombre = ""
    buffer = []  # Búfer de líneas entre centros

    codigo_centro_anterior = None  # Para deduplicar saltos de página

    for pagina in paginas:
        for linea in pagina.splitlines():
            linea = linea.strip()
            if not linea:
                continue

            # Detectar subárea (se actualiza cuando aparece)
            m_sub = RE_SUBAREA.search(linea)
            if m_sub:
                subarea_actual = m_sub.group(1)
                continue

            # Detectar cabecera de localidad: "NOMBRELOCALITAT / LOCALIDAD"
            m_loc = RE_LOCALITAT_HEADER.search(linea)
            if m_loc:
                localidad_nombre = limpiar_nombre(m_loc.group(1))
                buffer = []  # Reset del búfer al nueva localidad
                continue

            # Detectar línea "CENTRE / CENTRO" (cabecera de tabla)
            if re.match(r"^\s*CENTRE\s*/\s*CENTRO\s*$", linea, re.IGNORECASE):
                continue

            # Detectar código de localidad (9 dígitos, standalone)
            m_loc_code = RE_LOCALIDAD_CODIGO.match(linea)
            if m_loc_code:
                localidad_codigo = m_loc_code.group(1)
                continue

            # Detectar código de centro (8 dígitos) → flush del búfer
            m_centro = RE_CENTRO.match(linea)
            if m_centro:
                nombre_raw = m_centro.group(1).strip()
                codigo = m_centro.group(2)
                programa_raw = m_centro.group(3).strip()

                # Deduplicar: si el código es igual al anterior y la
                # localidad coincide, es un artefacto de salto de página
                if codigo == codigo_centro_anterior:
                    continue
                codigo_centro_anterior = codigo

                # Extraer nombre del centro (limpiar nombre del búfer + línea actual)
                nombre_centro = limpiar_nombre(nombre_raw)

                # Extraer programa lingüístico
                m_prog = RE_PROGRAMA.search(programa_raw)
                programa = m_prog.group(1) if m_prog else "No especificado"

                # Derivar area_code de subarea_code (2 primeros dígitos)
                area_code = subarea_actual[:2] if subarea_actual else ""

                resultado[codigo] = {
                    "centro_nombre": nombre_centro,
                    "programa": programa,
                    "localidad_codigo": localidad_codigo,
                    "localidad_nombre": localidad_nombre,
                    "subarea_codigo": subarea_actual,
                    "area_codigo": area_code,
                }
                buffer = []
                continue

            # Cualquier otra línea → acumular en búfer (para debug futuro)
            buffer.append(linea)

    return resultado


def main():
    if not PDF_PATH.is_file():
        print(f"Error: PDF no encontrado: {PDF_PATH}", file=sys.stderr)
        sys.exit(1)

    print(f"Leyendo {PDF_PATH.name}...")
    datos = parsear_pdf(PDF_PATH)

    if not datos:
        print("Error: no se extrajeron centros del PDF.", file=sys.stderr)
        sys.exit(1)

    # Guardar JSON
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

    # Estadísticas
    areas = set(v["area_codigo"] for v in datos.values())
    subareas = set(v["subarea_codigo"] for v in datos.values())
    localidades = set(v["localidad_codigo"] for v in datos.values())
    con_programa = sum(1 for v in datos.values() if v["programa"] != "No especificado")

    print(f"Centros extraídos: {len(datos)}")
    print(f"Áreas: {len(areas)} | Subáreas: {len(subareas)} | Localidades: {len(localidades)}")
    print(f"Con programa lingüístico: {con_programa}/{len(datos)}")
    print(f"Guardado en: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
