#!/usr/bin/env python3
"""
Calcula rutas de conduccion desde un municipio de la Comunidad Valenciana
a todos los demas usando la API publica de OSRM (Open Source Routing Machine).

No se necesita API key.

Uso:
    python calcular_rutas.py "Aldaia"
    python calcular_rutas.py "Castello de la Plana" --sort distance
"""

import json
import math
import sys
import os
import argparse
import urllib.request
import urllib.parse

MUNICIPIOS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "municipios_cv.json")

OSRM_BASE = "https://router.project-osrm.org"
MAX_COORDS_PER_REQUEST = 100


def load_municipios(path=MUNICIPIOS_FILE):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def find_municipio(nombre, municipios):
    """Busca un municipio por nombre con coincidencia flexible."""
    nombre_lower = nombre.lower().strip()

    for m in municipios:
        if m["nombre"].lower() == nombre_lower:
            return m

    for m in municipios:
        if nombre_lower in m["nombre"].lower():
            return m

    return None


def _osrm_table(origin, destinos):
    """
    Llama a la tabla OSRM para un origen y una lista de destinos.

    Returns:
        Tupla (distancias_metros, duraciones_segundos) como listas paralelas.

    Raises:
        ConnectionError: Si no se puede conectar a OSRM.
        RuntimeError: Si OSRM devuelve una respuesta invalida.
    """
    coords = [f"{origin['longitud']},{origin['latitud']}"]
    for m in destinos:
        coords.append(f"{m['longitud']},{m['latitud']}")

    coord_str = ";".join(coords)
    params = urllib.parse.urlencode({"sources": "0", "annotations": "distance,duration"})
    url = f"{OSRM_BASE}/table/v1/driving/{coord_str}?{params}"

    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"No se pudo conectar a OSRM: {e}. "
            "Verifica tu conexion a internet."
        ) from e
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"OSRM devolvio una respuesta no valida (JSON): {e}"
        ) from e

    if data.get("code") != "Ok":
        raise RuntimeError(
            f"OSRM returned code: {data.get('code')}. "
            "El servidor de enrutamiento no pudo calcular las rutas."
        )

    distances = data.get("distances")
    durations = data.get("durations")

    if distances is None or durations is None:
        raise RuntimeError(
            f"OSRM response missing 'distances' or 'durations'. "
            f"Keys received: {list(data.keys())}"
        )

    if len(distances) == 0 or len(durations) == 0:
        raise RuntimeError("OSRM returned empty distances/durations arrays")

    return distances[0], durations[0]


def calcular_rutas(nombre_origen, sort_by="time", municipios_path=MUNICIPIOS_FILE):
    """
    Calcula distancias y tiempos de conduccion desde un municipio a todos los demas.

    Args:
        nombre_origen: Nombre del municipio de origen.
        sort_by: 'time' o 'distance' (por defecto 'time').
        municipios_path: Ruta al archivo JSON de municipios.

    Returns:
        Lista de diccionarios ordenados con la informacion de cada ruta.

    Raises:
        ValueError: Si el municipio de origen no se encuentra.
        ConnectionError: Si no se puede conectar a OSRM.
    """
    municipios = load_municipios(municipios_path)
    origen = find_municipio(nombre_origen, municipios)

    if not origen:
        disponibles = [m["nombre"] for m in sorted(municipios, key=lambda x: x["nombre"])]
        raise ValueError(
            f"Municipio '{nombre_origen}' no encontrado.\n"
            f"Municipios disponibles ({len(disponibles)}): {', '.join(disponibles[:20])}..."
        )

    print(f"Origen: {origen['nombre']} ({origen['provincia']})")
    print(f"  Coordenadas: {origen['latitud']}, {origen['longitud']}")

    destinos = [
        m for m in municipios
        if not (m["nombre"] == origen["nombre"] and m["provincia"] == origen["provincia"])
    ]
    print(f"Destinos: {len(destinos)} municipios")

    max_dest_per_batch = MAX_COORDS_PER_REQUEST - 1
    total_batches = math.ceil(len(destinos) / max_dest_per_batch)

    resultados = []
    skipped = 0

    for batch_idx in range(total_batches):
        start = batch_idx * max_dest_per_batch
        end = min(start + max_dest_per_batch, len(destinos))
        batch_destinos = destinos[start:end]

        print(f"\n  Lote {batch_idx + 1}/{total_batches}: calculando {len(batch_destinos)} rutas...")

        try:
            distances_m, durations_s = _osrm_table(origen, batch_destinos)
        except (ConnectionError, RuntimeError) as e:
            print(f"  ERROR en lote {batch_idx + 1}: {e}")
            skipped += len(batch_destinos)
            continue

        for i, m in enumerate(batch_destinos):
            idx = i + 1

            if idx >= len(distances_m) or idx >= len(durations_s):
                print(f"  SKIP [{m['nombre']}]: indice {idx} fuera de rango "
                      f"(distances={len(distances_m)}, durations={len(durations_s)})")
                skipped += 1
                continue

            dist_m = distances_m[idx]
            dur_s = durations_s[idx]

            if dist_m is None or dur_s is None:
                skipped += 1
                continue

            distancia_km = round(dist_m / 1000, 2)
            duracion_min = round(dur_s / 60, 1)

            resultados.append({
                "nombre": m["nombre"],
                "provincia": m["provincia"],
                "latitud": m["latitud"],
                "longitud": m["longitud"],
                "distancia_km": distancia_km,
                "duracion_min": duracion_min,
            })

    resultados.sort(
        key=lambda r: r["duracion_min"] if sort_by == "time" else r["distancia_km"],
    )

    print(f"\n{'='*75}")
    print(f"  Rutas desde {origen['nombre']} ordenadas por {'TIEMPO' if sort_by == 'time' else 'DISTANCIA'}")
    print(f"{'='*75}")
    print(f"{'#':>4}  {'Municipio':<30} {'Provincia':<12} {'Dist. (km)':>10} {'Tiempo (min)':>12}")
    print(f"{'-'*75}")

    for i, r in enumerate(resultados, 1):
        print(f"{i:>4}  {r['nombre']:<30} {r['provincia']:<12} {r['distancia_km']:>10.1f} {r['duracion_min']:>12.1f}")

    print(f"{'-'*75}")
    print(f"Total: {len(resultados)} rutas calculadas")
    if skipped:
        print(f"Omitidos: {skipped} municipios sin ruta disponible")

    return resultados


def main():
    parser = argparse.ArgumentParser(
        description="Calcula rutas de conduccion desde un municipio CV a todos los demas (usando OSRM)."
    )
    parser.add_argument("origen", help="Nombre del municipio de origen (ej: 'Aldaia')")
    parser.add_argument(
        "--sort",
        choices=["time", "distance"],
        default="time",
        help="Ordenar por 'time' (predeterminado) o 'distance'",
    )
    args = parser.parse_args()

    try:
        calcular_rutas(args.origen, sort_by=args.sort)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ConnectionError as e:
        print(f"Error de conexion: {e}", file=sys.stderr)
        sys.exit(1)
    except RuntimeError as e:
        print(f"Error de OSRM: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
