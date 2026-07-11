"""
bloques.py

Engine for grouping positions by subarea with dual-level ascending sorting
and route metrics via OSRM.

Phase 3 — Dual-Level Ascending Sorting:
  1. Internal Pass: rows inside each block sorted closest -> farthest.
  2. Block Pass: blocks sorted by their closest municipality to origin.

Phase 4 integration: quality gate assertions from match_coords.py are
called after block generation.
"""

import json
import math
import urllib.request
import urllib.parse
from pathlib import Path

import pandas as pd

from match_coords import (
    inject_coordinates,
    validate_critical_subareas,
    validate_block_sorting,
)


# ---------------------------------------------------------------------------
# OSRM configuration
# ---------------------------------------------------------------------------
OSRM_BASE = "https://router.project-osrm.org"
MAX_COORDS_PER_REQUEST = 100


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_municipios_cv(path: str) -> list[dict]:
    """Load municipios_cv.json."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_municipio(nombre: str, municipios: list[dict]) -> dict | None:
    """Find a municipality by name with exact then partial matching."""
    nombre_lower = nombre.lower().strip()

    for m in municipios:
        if m["nombre"].lower() == nombre_lower:
            return m

    for m in municipios:
        if nombre_lower in m["nombre"].lower():
            return m

    return None


# ---------------------------------------------------------------------------
# OSRM Table API
# ---------------------------------------------------------------------------

def _osrm_table(origin: dict, destinos: list[dict]) -> tuple[list, list]:
    """
    Call OSRM Table API for one origin and a list of destinations.

    Returns:
        (distances_meters, durations_seconds) as parallel lists.
        Element 0 is the origin (0 distance, 0 duration).
    """
    coords = [f"{origin['longitud']},{origin['latitud']}"]
    for m in destinos:
        coords.append(f"{m['longitud']},{m['latitud']}")

    coord_str = ";".join(coords)
    params = urllib.parse.urlencode({
        "sources": "0",
        "annotations": "distance,duration",
    })
    url = f"{OSRM_BASE}/table/v1/driving/{coord_str}?{params}"

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM returned code: {data.get('code')}")

    if "distances" not in data or "durations" not in data:
        raise RuntimeError(
            f"OSRM response missing 'distances' or 'durations': {list(data.keys())}"
        )

    distances_m = data["distances"][0]
    durations_s = data["durations"][0]

    if distances_m is None or durations_s is None:
        raise RuntimeError("OSRM returned null distances/durations row")

    return distances_m, durations_s


# ---------------------------------------------------------------------------
# Batch distance calculation
# ---------------------------------------------------------------------------

def calcular_distancias_batch(
    origin: dict,
    destinos_unicos: list[dict],
    max_batch_size: int = MAX_COORDS_PER_REQUEST - 1,
) -> dict:
    """
    Calculate distances and times from origin to multiple destinations
    using OSRM Table API with batching.

    Returns:
        Dict {(lat_rounded, lon_rounded): {"distancia_km": float, "tiempo_min": float}}
    """
    if not destinos_unicos:
        return {}

    total_batches = math.ceil(len(destinos_unicos) / max_batch_size)
    resultados: dict[tuple[float, float], dict] = {}

    for batch_idx in range(total_batches):
        start = batch_idx * max_batch_size
        end = min(start + max_batch_size, len(destinos_unicos))
        batch = destinos_unicos[start:end]

        print(
            f"  OSRM batch {batch_idx + 1}/{total_batches}: "
            f"calculating {len(batch)} routes..."
        )

        distances_m, durations_s = _osrm_table(origin, batch)

        for i, m in enumerate(batch):
            idx = i + 1  # skip origin at position 0
            if idx >= len(distances_m) or idx >= len(durations_s):
                print(f"  SKIP [{m.get('key', '?')}]: index out of range")
                continue

            dist_m = distances_m[idx]
            dur_s = durations_s[idx]

            if dist_m is None or dur_s is None:
                print(f"  SKIP [{m.get('key', '?')}]: null value")
                continue

            key = (round(m["latitud"], 6), round(m["longitud"], 6))
            resultados[key] = {
                "distancia_km": round(dist_m / 1000, 2),
                "tiempo_min": round(dur_s / 60, 1),
            }

    return resultados


# ---------------------------------------------------------------------------
# Phase 3 — Dual-Level Ascending Sorting
# ---------------------------------------------------------------------------

def agrupar_por_subarea(
    df: pd.DataFrame,
    rutas: dict,
    sort_by: str = "distancia",
) -> list[dict]:
    """
    Group positions by subarea with two strict sorting passes:

    Internal Pass: sort all rows inside each subarea block from closest
    to farthest (by the selected metric).

    Block Pass: calculate the minimum distance/time for each subarea block
    (its closest municipality to the origin) and sort the blocks themselves
    so the closest subarea is at the top.

    Args:
        df: DataFrame with Zona_Subarea, Latitud_Destino, Longitud_Destino.
        rutas: Dict of OSRM results keyed by (lat, lon).
        sort_by: 'distancia' or 'tiempo' — sorting criterion.

    Returns:
        List of block dicts sorted ascending by the chosen metric.
    """
    # --- Group rows by subarea code ---
    grupos: dict[str, dict] = {}
    for _, row in df.iterrows():
        subarea = row.get("Zona_Subarea", "") or "SIN_SUBAREA"

        if subarea not in grupos:
            grupos[subarea] = {
                "subarea_codigo": subarea,
                "subarea_nombre": row.get("Zona_Subarea_Nombre", ""),
                "plazas": [],
            }
        grupos[subarea]["plazas"].append(row)

    # --- Build block dicts with per-plaza metrics ---
    resultados: list[dict] = []

    for subarea_code, grupo in grupos.items():
        tiempos: list[float] = []
        distancias: list[float] = []
        plazas_data: list[dict] = []

        for row in grupo["plazas"]:
            lat = row.get("Latitud_Destino")
            lon = row.get("Longitud_Destino")

            if pd.isna(lat) or pd.isna(lon):
                plazas_data.append({
                    "centro": row.get("Centro_Nombre", ""),
                    "municipio": row.get("Municipio", ""),
                    "especialidad": row.get("Especialidad", ""),
                    "tipo": row.get("Tipo", ""),
                    "tiempo_trayecto_minutos": None,
                    "distancia_km": None,
                })
                continue

            key = (round(float(lat), 6), round(float(lon), 6))
            ruta = rutas.get(key)

            if ruta:
                tiempo = ruta["tiempo_min"]
                distancia = ruta["distancia_km"]
                tiempos.append(tiempo)
                distancias.append(distancia)
            else:
                tiempo = None
                distancia = None

            plazas_data.append({
                "centro": row.get("Centro_Nombre", ""),
                "municipio": row.get("Municipio", ""),
                "especialidad": row.get("Especialidad", ""),
                "tipo": row.get("Tipo", ""),
                "tiempo_trayecto_minutos": tiempo,
                "distancia_km": distancia,
            })

        # --- Internal Pass: sort plazas closest -> farthest ---
        def _plaza_sort_key(p: dict) -> float:
            if sort_by == "tiempo":
                val = p.get("tiempo_trayecto_minutos")
            else:
                val = p.get("distancia_km")
            return val if val is not None else float("inf")

        plazas_data.sort(key=_plaza_sort_key)

        # --- Block metrics (minimums for Block Pass, plus average) ---
        # Use inf for unresolved blocks so they sort to the bottom
        distancia_minima = round(min(distancias), 2) if distancias else float("inf")
        tiempo_minimo = round(min(tiempos), 1) if tiempos else float("inf")
        tiempo_medio = (
            round(sum(tiempos) / len(tiempos), 1) if tiempos else 0.0
        )

        resultados.append({
            "subarea_codigo": subarea_code,
            "subarea_nombre": grupo["subarea_nombre"],
            "distancia_minima_km": distancia_minima,
            "tiempo_minimo_minutos": tiempo_minimo,
            "tiempo_medio_minutos": tiempo_medio,
            "total_plazas": len(plazas_data),
            "plazas": plazas_data,
        })

    # --- Block Pass: sort blocks ascending by minimum of chosen metric ---
    # Blocks with inf (unresolved coords) sink to the bottom automatically.
    # Explicit guard: convert any residual None to inf before sorting.
    def _block_sort_key(block: dict) -> float:
        if sort_by == "tiempo":
            val = block.get("tiempo_minimo_minutos")
        else:
            val = block.get("distancia_minima_km")
        if val is None or val != val:  # NaN check
            return float("inf")
        return val

    resultados.sort(key=_block_sort_key)

    return resultados


# ---------------------------------------------------------------------------
# Main entry point: generar_bloques
# ---------------------------------------------------------------------------

def generar_bloques(
    df: pd.DataFrame,
    origen_nombre: str,
    municipios_cv_path: str,
    zonas_json_path: str | None = None,
    areas_subareas_path: str | None = None,
    sort_by: str = "distancia",
) -> dict:
    """
    Generate the subarea block structure with complete metrics.

    Pipeline:
      1. Load geographic data
      2. Find origin coordinates
      3. Inject coordinates + subarea from zonas.json (Phase 1 + 2)
      4. Calculate OSRM distances in batches
      5. Group by subarea with dual-level sorting (Phase 3)
      6. Run quality gate assertions (Phase 4)
      7. Return formatted JSON

    Args:
        df: Adjudicaciones DataFrame (may be pre-filtered).
        origen_nombre: Name of the origin municipality.
        municipios_cv_path: Path to municipios_cv.json.
        zonas_json_path: Optional path to zonas.json.
        areas_subareas_path: DEPRECATED — kept for backward compat.
        sort_by: 'distancia' or 'tiempo'.

    Returns:
        Dict with key 'resumen_por_subareas' ready for the UI.

    Raises:
        ValueError: If the origin municipality is not found.
    """
    # 1. Load municipalities
    municipios = load_municipios_cv(municipios_cv_path)

    # 2. Find origin
    origen = find_municipio(origen_nombre, municipios)
    if not origen:
        disponibles = sorted(
            [m["nombre"] for m in municipios], key=str.lower
        )
        raise ValueError(
            f"Municipio '{origen_nombre}' no encontrado.\n"
            f"Municipios disponibles ({len(disponibles)}): "
            f"{', '.join(disponibles[:20])}..."
        )

    print(f"Origin: {origen['nombre']} ({origen['provincia']})")
    print(f"  Coordinates: {origen['latitud']}, {origen['longitud']}")

    # 3. Inject coordinates + subarea if missing (Phase 1 + 2)
    result = df.copy()
    needs_injection = (
        "Latitud_Destino" not in result.columns
        or result["Latitud_Destino"].isna().all()
    )
    if needs_injection:
        result = inject_coordinates(
            result,
            municipios_cv_path,
            zonas_json_path,
            areas_subareas_path=areas_subareas_path,
        )

    # 4. Collect unique destinations (dedup by coordinates)
    destinos_unicos: list[dict] = []
    seen_coords: set[tuple[float, float]] = set()

    for _, row in result.iterrows():
        lat = row.get("Latitud_Destino")
        lon = row.get("Longitud_Destino")

        if pd.isna(lat) or pd.isna(lon):
            continue

        key = (round(float(lat), 6), round(float(lon), 6))
        if key not in seen_coords:
            seen_coords.add(key)
            destinos_unicos.append({
                "latitud": float(lat),
                "longitud": float(lon),
                "key": (
                    f"{row.get('Municipio', '?')}/"
                    f"{row.get('Centro_Nombre', '?')}"
                ),
            })

    print(
        f"Unique destinations: {len(destinos_unicos)} "
        f"(from {len(result)} total plazas)"
    )

    # 5. Calculate OSRM distances in batches
    rutas = calcular_distancias_batch(origen, destinos_unicos)
    print(f"OSRM routes calculated: {len(rutas)}")

    # 6. Group by subarea with dual-level sorting (Phase 3)
    bloques = agrupar_por_subarea(result, rutas, sort_by=sort_by)

    # 7. Quality gate assertions (Phase 4)
    # NOTE: validate_critical_subareas is called from app.py after
    # subareas are fully assigned. validate_block_sorting can be called
    # by the caller on the returned dict.

    output = {"resumen_por_subareas": bloques}
    return output


# ---------------------------------------------------------------------------
# Formatting utilities
# ---------------------------------------------------------------------------

def resumen_texto(bloques: dict) -> str:
    """Generate a readable summary of calculated blocks."""
    subareas = bloques.get("resumen_por_subareas", [])
    if not subareas:
        return "No blocks found."

    lines = [
        f"{'='*70}",
        f"  RESUMEN POR SUBÁREAS ({len(subareas)} bloques)",
        f"{'='*70}",
        f"{'Codigo':<8} {'Tiempo Min':>12} {'Dist. Min.':>12} {'Plazas':>8}",
        f"{'-'*70}",
    ]

    total_plazas = 0
    for b in subareas:
        t_min = b.get("tiempo_minimo_minutos", b["tiempo_medio_minutos"])
        lines.append(
            f"{b['subarea_codigo']:<8} "
            f"{t_min:>10.1f} min "
            f"{b['distancia_minima_km']:>10.2f} km "
            f"{b['total_plazas']:>6}"
        )
        total_plazas += b["total_plazas"]

    lines.append(f"{'-'*70}")
    lines.append(
        f"Total: {total_plazas} plazas en {len(subareas)} subáreas"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI for testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python bloques.py <adjudicaciones.json> <origen>")
        print(
            "  adjudicaciones.json: Archivo JSON de adjudicaciones filtradas"
        )
        print("  origen: Nombre del municipio de origen")
        sys.exit(1)

    adj_path = sys.argv[1]
    origen = sys.argv[2]

    with open(adj_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    df = pd.DataFrame(data)

    script_dir = Path(__file__).resolve().parent
    muni_path = str(script_dir / "municipios_cv.json")
    zonas_path = str(script_dir / "zonas.json")

    resultado = generar_bloques(df, origen, muni_path, zonas_path)

    print(resumen_texto(resultado))

    output_path = f"bloques_{origen}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)

    print(f"\nJSON guardado en: {output_path}")
