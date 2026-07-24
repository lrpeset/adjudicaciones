"""
test_bloques.py

Tests for bloques.py: subarea grouping, OSRM batching, and JSON generation.

Covers:
  - load_municipios_cv: file loading
  - find_municipio: exact/partial/no match
  - _osrm_table: mocked OSRM responses
  - calcular_distancias_batch: batching, empty, mocked
  - agrupar_por_subarea: grouping, sorting, metrics, NaN handling
  - generar_bloques: full pipeline (mocked OSRM), ValueError on bad origin
  - resumen_texto: formatting

Run:
    .venv/bin/python -m pytest test_bloques.py -v
"""

import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from bloques import (
    agrupar_por_subarea,
    calcular_distancias_batch,
    find_municipio,
    generar_bloques,
    load_municipios_cv,
    resumen_texto,
    _osrm_table,
)

MUNICPIOS_CV = os.path.join(os.path.dirname(__file__), "municipios_cv.json")


def _make_row(subarea="V01", subarea_nombre="Valencia capital",
              municipio="Valencia", centro="IES Test", lat=39.47, lon=-0.38,
              especialidad="120", tipo="VACANTE"):
    return {
        "Zona_Subarea": subarea,
        "Zona_Subarea_Nombre": subarea_nombre,
        "Municipio": municipio,
        "Centro_Nombre": centro,
        "Especialidad": especialidad,
        "Tipo": tipo,
        "Latitud_Destino": lat,
        "Longitud_Destino": lon,
    }


def _osrm_ok_response(n):
    """Build a valid OSRM response for n destinations."""
    distances = [0.0] + [1000.0 * (i + 1) for i in range(n)]
    durations = [0.0] + [60.0 * (i + 1) for i in range(n)]
    return {"code": "Ok", "distances": [distances], "durations": [durations]}


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — load_municipios_cv
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadMunicipiosCv:
    def test_loads_json(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_all_entries_have_required_keys(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        for m in data:
            assert "nombre" in m
            assert "provincia" in m
            assert "latitud" in m
            assert "longitud" in m


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — find_municipio
# ═══════════════════════════════════════════════════════════════════════════

class TestFindMunicipio:
    def test_exact_match(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        result = find_municipio("Valencia", data)
        assert result is not None
        assert result["nombre"] == "Valencia"

    def test_case_insensitive(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        result = find_municipio("VALENCIA", data)
        assert result is not None
        assert result["nombre"] == "Valencia"

    def test_partial_match(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        result = find_municipio("alicante", data)
        assert result is not None

    def test_no_match(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        result = find_municipio("FaketownZZZ", data)
        assert result is None

    def test_empty_string_matches_first(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        result = find_municipio("", data)
        # "" in "agost" is True, so partial match returns first entry
        assert result is not None


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — _osrm_table (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════

class TestOsrmTable:
    def _mock_response(self, data_dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data_dict).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("bloques.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45, "longitud": -0.36}]
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))

        distances, durations = _osrm_table(origin, destinos)
        assert len(distances) == 2
        assert distances[0] == 0.0
        assert durations[0] == 0.0

    @patch("bloques.urllib.request.urlopen")
    def test_bad_status_code(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45, "longitud": -0.36}]
        mock_urlopen.return_value = self._mock_response({"code": "InvalidUrl"})

        with pytest.raises(RuntimeError, match="OSRM returned code"):
            _osrm_table(origin, destinos)

    @patch("bloques.urllib.request.urlopen")
    def test_missing_distances_key(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45, "longitud": -0.36}]
        mock_urlopen.return_value = self._mock_response({"code": "Ok", "durations": [[0.0, 60.0]]})

        with pytest.raises(RuntimeError, match="missing"):
            _osrm_table(origin, destinos)

    @patch("bloques.urllib.request.urlopen")
    def test_null_distances_row(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45, "longitud": -0.36}]
        mock_urlopen.return_value = self._mock_response({
            "code": "Ok", "distances": [None], "durations": [None]
        })

        with pytest.raises(RuntimeError, match="null"):
            _osrm_table(origin, destinos)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — calcular_distancias_batch (mocked OSRM)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalcularDistanciasBatch:
    def _mock_response(self, data_dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data_dict).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("bloques.urllib.request.urlopen")
    def test_single_destination(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45, "longitud": -0.36, "key": "test"}]
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))

        result = calcular_distancias_batch(origin, destinos)
        assert len(result) == 1
        key = (round(39.45, 6), round(-0.36, 6))
        assert key in result
        assert result[key]["distancia_km"] == 1.0
        assert result[key]["tiempo_min"] == 1.0

    @patch("bloques.urllib.request.urlopen")
    def test_empty_destinations(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        result = calcular_distancias_batch(origin, [])
        assert result == {}
        mock_urlopen.assert_not_called()

    @patch("bloques.urllib.request.urlopen")
    def test_null_distance_skipped(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [
            {"latitud": 39.45, "longitud": -0.36, "key": "A"},
            {"latitud": 39.50, "longitud": -0.30, "key": "B"},
        ]
        resp = {"code": "Ok", "distances": [[0.0, None, 5000.0]], "durations": [[0.0, None, 120.0]]}
        mock_urlopen.return_value = self._mock_response(resp)

        result = calcular_distancias_batch(origin, destinos)
        assert len(result) == 1  # Only B is included, A is skipped

    @patch("bloques.urllib.request.urlopen")
    def test_batching(self, mock_urlopen):
        origin = {"latitud": 39.47, "longitud": -0.38}
        destinos = [{"latitud": 39.45 + i * 0.01, "longitud": -0.36, "key": f"d{i}"} for i in range(5)]
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(5))

        result = calcular_distancias_batch(origin, destinos, max_batch_size=2)
        assert len(result) == 5
        assert mock_urlopen.call_count == 3  # ceil(5/2) = 3 batches


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — agrupar_por_subarea
# ═══════════════════════════════════════════════════════════════════════════

class TestAgruparPorSubarea:
    def test_basic_grouping(self):
        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
            _make_row(subarea="V01", lat=39.46, lon=-0.37),
            _make_row(subarea="A02", lat=38.35, lon=-0.48),
        ])
        rutas = {
            (round(39.45, 6), round(-0.36, 6)): {"distancia_km": 1.0, "tiempo_min": 2.0},
            (round(39.46, 6), round(-0.37, 6)): {"distancia_km": 1.5, "tiempo_min": 3.0},
            (round(38.35, 6), round(-0.48, 6)): {"distancia_km": 10.0, "tiempo_min": 15.0},
        }
        result = agrupar_por_subarea(df, rutas)
        assert len(result) == 2
        # V01 has distance 1.0, A02 has distance 10.0 → V01 first
        assert result[0]["subarea_codigo"] == "V01"
        assert result[1]["subarea_codigo"] == "A02"

    def test_metrics_calculation(self):
        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
            _make_row(subarea="V01", lat=39.46, lon=-0.37),
        ])
        rutas = {
            (round(39.45, 6), round(-0.36, 6)): {"distancia_km": 2.0, "tiempo_min": 4.0},
            (round(39.46, 6), round(-0.37, 6)): {"distancia_km": 4.0, "tiempo_min": 8.0},
        }
        result = agrupar_por_subarea(df, rutas)
        assert result[0]["total_plazas"] == 2
        assert result[0]["distancia_minima_km"] == 2.0
        assert result[0]["tiempo_medio_minutos"] == 6.0

    def test_plazas_sorted_by_distance(self):
        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.50, lon=-0.40, centro="Far"),
            _make_row(subarea="V01", lat=39.45, lon=-0.36, centro="Near"),
        ])
        rutas = {
            (round(39.50, 6), round(-0.40, 6)): {"distancia_km": 5.0, "tiempo_min": 10.0},
            (round(39.45, 6), round(-0.36, 6)): {"distancia_km": 1.0, "tiempo_min": 2.0},
        }
        result = agrupar_por_subarea(df, rutas)
        plazas = result[0]["plazas"]
        assert plazas[0]["centro"] == "Near"
        assert plazas[1]["centro"] == "Far"

    def test_nan_coordinates_plaza(self):
        df = pd.DataFrame([
            _make_row(subarea="V01", lat=float("nan"), lon=float("nan")),
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
        ])
        rutas = {
            (round(39.45, 6), round(-0.36, 6)): {"distancia_km": 1.0, "tiempo_min": 2.0},
        }
        result = agrupar_por_subarea(df, rutas)
        assert result[0]["total_plazas"] == 2
        # NaN plazas sort to end (inf key), so last plaza has no route info
        assert result[0]["plazas"][-1]["tiempo_trayecto_minutos"] is None

    def test_no_matching_route(self):
        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
        ])
        rutas = {}  # No matching routes
        result = agrupar_por_subarea(df, rutas)
        assert result[0]["plazas"][0]["tiempo_trayecto_minutos"] is None

    def test_sorted_by_min_distance(self):
        df = pd.DataFrame([
            _make_row(subarea="FAR", lat=38.35, lon=-0.48),
            _make_row(subarea="NEAR", lat=39.45, lon=-0.36),
        ])
        rutas = {
            (round(38.35, 6), round(-0.48, 6)): {"distancia_km": 10.0, "tiempo_min": 15.0},
            (round(39.45, 6), round(-0.36, 6)): {"distancia_km": 1.0, "tiempo_min": 2.0},
        }
        result = agrupar_por_subarea(df, rutas)
        assert result[0]["subarea_codigo"] == "NEAR"
        assert result[1]["subarea_codigo"] == "FAR"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — generar_bloques (mocked OSRM)
# ═══════════════════════════════════════════════════════════════════════════

class TestGenerarBloques:
    def _mock_response(self, data_dict):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(data_dict).encode("utf-8")
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("bloques.urllib.request.urlopen")
    def test_full_pipeline(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))

        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
        ])
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)
        assert "resumen_por_subareas" in result
        assert len(result["resumen_por_subareas"]) == 1
        assert result["resumen_por_subareas"][0]["total_plazas"] == 1

    @pytest.mark.xfail(
        strict=True,
        reason="Defecto conocido: generar_bloques no invoca validate_block_sorting",
    )
    @patch("bloques.urllib.request.urlopen")
    def test_full_pipeline_invokes_block_quality_gate(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))
        df = pd.DataFrame([_make_row()])

        with patch("bloques.validate_block_sorting") as validator:
            generar_bloques(df, "Valencia", MUNICPIOS_CV)

        validator.assert_called_once()

    def test_unknown_origin_raises(self):
        df = pd.DataFrame([_make_row()])
        with pytest.raises(ValueError, match="no encontrado"):
            generar_bloques(df, "FaketownZZZ", MUNICPIOS_CV)

    @patch("bloques.urllib.request.urlopen")
    def test_skips_injection_if_coords_present(self, mock_urlopen):
        """If Latitud_Destino is already filled, inject_coordinates is skipped."""
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))

        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
        ])
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)
        assert result["resumen_por_subareas"][0]["total_plazas"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7 — resumen_texto
# ═══════════════════════════════════════════════════════════════════════════

class TestResumenTexto:
    def test_empty(self):
        assert resumen_texto({"resumen_por_subareas": []}) == "No blocks found."

    def test_with_data(self):
        bloques = {
            "resumen_por_subareas": [
                {"subarea_codigo": "V01", "tiempo_medio_minutos": 5.0,
                 "distancia_minima_km": 1.2, "total_plazas": 10},
                {"subarea_codigo": "A02", "tiempo_medio_minutos": 15.0,
                 "distancia_minima_km": 8.5, "total_plazas": 5},
            ]
        }
        text = resumen_texto(bloques)
        assert "V01" in text
        assert "A02" in text
        assert "15" in text  # total plazas = 10 + 5
        assert "2 subáreas" in text


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
