#!/usr/bin/env python3
"""
Genera el archivo municipios_cv.json con todos los municipios de la
Comunitat Valenciana.

Fuente: GeoJSON oficial del Institut Cartogràfic Valencià (ICV) a través
del portal de Datos Abiertos de la Generalitat Valenciana.
"""

import json
import math
import re
import urllib.request

GEOJSON_URL = (
    "https://dadesobertes.gva.es/dataset/7928cfb8-88f7-4055-98e2-a40f9c8316a8"
    "/resource/2823465c-7c24-4e23-b3ef-ac541c3109ac/download/"
    "ca_municipios_20260505.geojson"
)
OUTPUT_FILE = "municipios_cv.json"

# Mapa de códigos de provincia a nombres
PROVINCIAS = {"03": "Alicante", "12": "Castellón", "46": "Valencia"}


def _utm_to_latlon(easting: float, northing: float, zone: int = 30) -> tuple[float, float]:
    """
    Convierte coordenadas UTM (ETRS89 / UTM zone 30N, EPSG:25830)
    a WGS84 latitud/longitud.
    La diferencia ETRS89-WGS84 es sub-métrica, se trata como WGS84.
    """
    k0 = 0.9996
    a = 6378137.0
    f = 1.0 / 298.257223563
    e = math.sqrt(2 * f - f * f)
    e1 = (1.0 - math.sqrt(1.0 - e * e)) / (1.0 + math.sqrt(1.0 - e * e))

    x = easting - 500000
    y = northing

    m = y / k0
    mu = m / (a * (1.0 - e * e / 4.0 - 3.0 * e**4 / 64.0 - 5.0 * e**6 / 256.0))

    phi1 = mu + (
        (3.0 * e1 / 2.0 - 27.0 * e1**3 / 32.0) * math.sin(2.0 * mu)
        + (21.0 * e1**2 / 16.0 - 55.0 * e1**4 / 32.0) * math.sin(4.0 * mu)
        + (151.0 * e1**3 / 96.0) * math.sin(6.0 * mu)
        + (1097.0 * e1**4 / 512.0) * math.sin(8.0 * mu)
    )

    c1 = 1.0 - e * e * math.sin(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    r1 = a * (1.0 - e * e) / (c1**1.5)
    n1 = a / math.sqrt(c1)
    d = x / (n1 * k0)

    phi = phi1 - (n1 * math.tan(phi1) / r1) * (
        d * d / 2.0
        - (5.0 + 3.0 * t1 + 10.0 * c1 - 4.0 * c1 * c1 - 9.0 * e * e) * d**4 / 24.0
        + (61.0 + 90.0 * t1 + 298.0 * c1 + 45.0 * t1 * t1 - 252.0 * e * e - 3.0 * c1 * c1)
        * d**6
        / 720.0
    )
    lam = (
        d
        - (1.0 + 2.0 * t1 + c1) * d**3 / 6.0
        + (5.0 - 2.0 * c1 + 28.0 * t1 - 3.0 * c1 * c1 + 8.0 * e * e + 24.0 * t1 * t1)
        * d**5
        / 120.0
    ) / math.cos(phi1)

    lat = math.degrees(phi)
    lon = math.degrees(lam) + (zone * 6 - 183)
    return lat, lon


def _polygon_centroid(geom: dict) -> tuple[float, float]:
    """Calcula el centroide de un polígono o multipolígono (promedio de vértices)."""
    if geom["type"] == "Polygon":
        coords = geom["coordinates"][0]
    elif geom["type"] == "MultiPolygon":
        coords = max(geom["coordinates"], key=lambda p: len(p[0]))[0]
    else:
        msg = f"Tipo de geometría no soportado: {geom['type']}"
        raise ValueError(msg)
    n = len(coords)
    cx = sum(c[0] for c in coords) / n
    cy = sum(c[1] for c in coords) / n
    return cx, cy


def _format_nombre(name: str) -> str:
    """Convierte un nombre en mayúsculas a formato título con reglas lingüísticas."""
    lowercase_words = {"de", "del", "la", "las", "los", "el", "les", "y", "i"}
    words = name.lower().split()
    result = []
    for i, w in enumerate(words):
        if i == 0:
            result.append(w.capitalize())
        else:
            stripped = w.strip("()")
            if "/" not in w and stripped in lowercase_words:
                result.append(w)
            else:
                result.append(w.capitalize())
    s = " ".join(result)
    # Capitalizar tras cada slash
    s = re.sub(r"/([a-z])", lambda m: "/" + m.group(1).upper(), s)
    # Capitalizar tras paréntesis
    s = re.sub(r"\(([a-z])", lambda m: "(" + m.group(1).upper(), s)
    # Capitalizar tras coma (artículos desplazados: "LA", "EL", "L'")
    s = re.sub(r", ([a-z])", lambda m: ", " + m.group(1).upper(), s)
    # Lowercase particle after apostrophe (D' → d')
    s = re.sub(r"(?<=\b[DL])(['´])[A-Z]", lambda m: m.group(1) + m.group(0)[-1].lower(), s)
    # Capitalize letter after apostrophe (d'ASNAR → d'Asnar)
    s = re.sub(r"(?<=['´])[A-ZÀÈÉÍÒÓÚ]", lambda m: m.group(0).upper(), s)
    return s


def main():
    print(f"Descargando datos desde {GEOJSON_URL} ...")
    with urllib.request.urlopen(GEOJSON_URL) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    features = data.get("features", [])
    print(f"Municipios encontrados en la fuente: {len(features)}")

    municipios = []
    seen = set()

    for f in features:
        props = f["properties"]
        nombre_raw = props.get("NOMBRE", "").strip()
        if not nombre_raw:
            continue
        nombre = _format_nombre(nombre_raw)

        codprov = props.get("CODPROV", "")
        provincia = PROVINCIAS.get(codprov, "")
        if not provincia:
            continue

        geom = f.get("geometry")
        if not geom:
            continue

        try:
            cx, cy = _polygon_centroid(geom)
        except (ValueError, KeyError):
            continue

        lat, lon = _utm_to_latlon(cx, cy)
        latitud = round(lat, 6)
        longitud = round(lon, 6)

        key = (nombre, provincia)
        if key in seen:
            continue
        seen.add(key)

        municipios.append({
            "nombre": nombre,
            "provincia": provincia,
            "latitud": latitud,
            "longitud": longitud,
        })

    municipios.sort(key=lambda m: (m["provincia"], m["nombre"]))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(municipios, f, ensure_ascii=False, indent=2)

    print(f"Archivo {OUTPUT_FILE} generado con {len(municipios)} municipios.")

    por_prov = {}
    for m in municipios:
        por_prov.setdefault(m["provincia"], 0)
        por_prov[m["provincia"]] += 1
    for prov, cnt in sorted(por_prov.items()):
        print(f"  {prov}: {cnt}")


if __name__ == "__main__":
    main()
