"""
test_integration.py

End-to-end integration tests that exercise the full pipeline:
  adjudicacion filter → match_coords → bloques (mocked OSRM) → JSON validation

Run:
    .venv/bin/python -m pytest test_integration.py -v
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from match_coords import inject_coordinates
from bloques import generar_bloques

MUNICPIOS_CV = os.path.join(os.path.dirname(__file__), "municipios_cv.json")


def _osrm_ok_response(n):
    distances = [0.0] + [1000.0 * (i + 1) for i in range(n)]
    durations = [0.0] + [60.0 * (i + 1) for i in range(n)]
    return {"code": "Ok", "distances": [distances], "durations": [durations]}


def _mock_response(data_dict):
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data_dict).encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _make_synthetic_adjudicaciones():
    """Create a synthetic DataFrame mimicking adjudicacion.py output."""
    return pd.DataFrame([
        {
            "Centro_Codigo": "462501234",
            "Centro_Nombre": "IES Jaume I",
            "Municipio": "Valencia",
            "Especialidad": "120",
            "Tipo": "VACANTE",
            "Zona_Subarea": "V01",
            "Zona_Subarea_Nombre": "Valencia capital",
        },
        {
            "Centro_Codigo": "462505678",
            "Centro_Nombre": "IES Tirant lo Blanch",
            "Municipio": "Valencia",
            "Especialidad": "120",
            "Tipo": "VACANTE",
            "Zona_Subarea": "V01",
            "Zona_Subarea_Nombre": "Valencia capital",
        },
        {
            "Centro_Codigo": "03018129",
            "Centro_Nombre": "IES La Marina",
            "Municipio": "Altea",
            "Especialidad": "120",
            "Tipo": "VACANTE",
            "Zona_Subarea": "A01",
            "Zona_Subarea_Nombre": "Marina Baixa",
        },
        {
            "Centro_Codigo": "12032456",
            "Centro_Nombre": "IES Castello",
            "Municipio": "Castello de la Plana",
            "Especialidad": "200",
            "Tipo": "VACANTE",
            "Zona_Subarea": "C01",
            "Zona_Subarea_Nombre": "Plana Alta",
        },
        {
            "Centro_Codigo": "46012345",
            "Centro_Nombre": "CEIP Aldaia",
            "Municipio": "Aldaia",
            "Especialidad": "120",
            "Tipo": "VACANTE",
            "Zona_Subarea": "V02",
            "Zona_Subarea_Nombre": "Horta Oest",
        },
    ])


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Coordinate injection + filter integration
# ═══════════════════════════════════════════════════════════════════════════

class TestCoordinateInjection:
    def test_injects_all_known_municipalities(self):
        df = _make_synthetic_adjudicaciones()
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert not result["Latitud_Destino"].isna().any()
        assert not result["Longitud_Destino"].isna().any()

    def test_filter_then_inject(self):
        df = _make_synthetic_adjudicaciones()
        filtered = df[df["Especialidad"] == "120"].copy()
        assert len(filtered) == 4  # 4 rows with espec 120
        result = inject_coordinates(filtered, MUNICPIOS_CV)
        assert len(result) == 4
        assert not result["Latitud_Destino"].isna().any()


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Full pipeline: filter → inject → group → JSON
# ═══════════════════════════════════════════════════════════════════════════

class TestFullPipeline:
    @patch("bloques.urllib.request.urlopen")
    def test_filter_and_group(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(4))

        df = _make_synthetic_adjudicaciones()
        filtered = df[df["Especialidad"] == "120"].copy()

        result = generar_bloques(filtered, "Valencia", MUNICPIOS_CV)

        assert "resumen_por_subareas" in result
        subareas = result["resumen_por_subareas"]
        assert len(subareas) > 0

        total_plazas = sum(s["total_plazas"] for s in subareas)
        assert total_plazas == 4

    @patch("bloques.urllib.request.urlopen")
    def test_json_schema_valid(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(4))

        df = _make_synthetic_adjudicaciones()
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        subareas = result["resumen_por_subareas"]
        for sa in subareas:
            assert "subarea_codigo" in sa
            assert "tiempo_medio_minutos" in sa
            assert "distancia_minima_km" in sa
            assert "total_plazas" in sa
            assert "plazas" in sa
            assert isinstance(sa["plazas"], list)

            for pl in sa["plazas"]:
                assert "centro" in pl
                assert "municipio" in pl
                assert "especialidad" in pl
                assert "tipo" in pl
                assert "tiempo_trayecto_minutos" in pl
                assert "distancia_km" in pl

    @patch("bloques.urllib.request.urlopen")
    def test_subareas_sorted_by_distance(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(4))

        df = _make_synthetic_adjudicaciones()
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        subareas = result["resumen_por_subareas"]
        distances = [sa["distancia_minima_km"] for sa in subareas]
        assert distances == sorted(distances), "Subareas must be sorted by distance ascending"

    @patch("bloques.urllib.request.urlopen")
    def test_plazas_sorted_within_subarea(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(4))

        df = _make_synthetic_adjudicaciones()
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        for sa in result["resumen_por_subareas"]:
            plazas = sa["plazas"]
            dists = [p["distancia_km"] for p in plazas if p["distancia_km"] is not None]
            assert dists == sorted(dists), \
                f"Plazas in {sa['subarea_codigo']} must be sorted by distance"

    @patch("bloques.urllib.request.urlopen")
    def test_output_serializable_to_json(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(4))

        df = _make_synthetic_adjudicaciones()
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        # Must be JSON-serializable
        json_str = json.dumps(result, ensure_ascii=False, indent=2)
        assert len(json_str) > 0
        parsed = json.loads(json_str)
        assert parsed == result


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Edge cases: all unknown, single row, empty after filter
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCases:
    @patch("bloques.urllib.request.urlopen")
    def test_single_row(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(1))

        df = _make_synthetic_adjudicaciones().iloc[[0]]
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        subareas = result["resumen_por_subareas"]
        assert len(subareas) == 1
        assert subareas[0]["total_plazas"] == 1

    @patch("bloques.urllib.request.urlopen")
    def test_empty_filter_returns_no_subareas(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(0))

        df = _make_synthetic_adjudicaciones()
        filtered = df[df["Especialidad"] == "999"].copy()  # No match
        result = generar_bloques(filtered, "Valencia", MUNICPIOS_CV)

        assert result["resumen_por_subareas"] == []


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
