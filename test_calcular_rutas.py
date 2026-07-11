"""
test_calcular_rutas.py

Tests for calcular_rutas.py: standalone CLI route calculator with OSRM.

Covers:
  - load_municipios: file loading
  - find_municipio: exact/partial/no match
  - _osrm_table: mocked HTTP (success, connection error, bad JSON, bad status, missing keys)
  - calcular_rutas: full pipeline (mocked), ValueError on unknown origin, sorting

Run:
    .venv/bin/python -m pytest test_calcular_rutas.py -v
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from calcular_rutas import (
    calcular_rutas,
    find_municipio,
    load_municipios,
    _osrm_table,
)

MUNICIPIOS_FILE = os.path.join(os.path.dirname(__file__), "municipios_cv.json")


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


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — load_municipios
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadMunicipios:
    def test_loads_json(self):
        data = load_municipios(MUNICIPIOS_FILE)
        assert isinstance(data, list)
        assert len(data) > 500

    def test_entries_have_required_keys(self):
        data = load_municipios(MUNICIPIOS_FILE)
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
        data = load_municipios(MUNICIPIOS_FILE)
        result = find_municipio("Valencia", data)
        assert result is not None
        assert result["nombre"] == "Valencia"

    def test_case_insensitive(self):
        data = load_municipios(MUNICIPIOS_FILE)
        result = find_municipio("VALENCIA", data)
        assert result is not None

    def test_partial_match(self):
        data = load_municipios(MUNICIPIOS_FILE)
        result = find_municipio("castello", data)
        assert result is not None

    def test_no_match(self):
        data = load_municipios(MUNICIPIOS_FILE)
        result = find_municipio("NingunLugarInventado", data)
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — _osrm_table (mocked HTTP)
# ═══════════════════════════════════════════════════════════════════════════

class TestOsrmTable:
    def _origin(self):
        return {"latitud": 39.47, "longitud": -0.38}

    def _destinos(self):
        return [{"latitud": 39.45, "longitud": -0.36}]

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(1))
        dist, dur = _osrm_table(self._origin(), self._destinos())
        assert len(dist) == 2
        assert dist[0] == 0.0

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("network down")
        with pytest.raises(ConnectionError, match="conectar a OSRM"):
            _osrm_table(self._origin(), self._destinos())

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_bad_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json at all"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp
        with pytest.raises(RuntimeError, match="respuesta no valida"):
            _osrm_table(self._origin(), self._destinos())

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_bad_status_code(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"code": "InvalidUrl"})
        with pytest.raises(RuntimeError, match="OSRM returned code"):
            _osrm_table(self._origin(), self._destinos())

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_missing_keys(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({"code": "Ok"})
        with pytest.raises(RuntimeError, match="missing"):
            _osrm_table(self._origin(), self._destinos())

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_empty_arrays(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response({
            "code": "Ok", "distances": [], "durations": []
        })
        with pytest.raises(RuntimeError, match="empty"):
            _osrm_table(self._origin(), self._destinos())


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — calcular_rutas (mocked OSRM)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalcularRutas:
    @patch("calcular_rutas.urllib.request.urlopen")
    def test_full_pipeline(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(2))
        result = calcular_rutas("Valencia", municipios_path=MUNICIPIOS_FILE)
        assert isinstance(result, list)
        assert len(result) > 0
        for r in result:
            assert "nombre" in r
            assert "distancia_km" in r
            assert "duracion_min" in r
            assert r["distancia_km"] >= 0
            assert r["duracion_min"] >= 0

    def test_unknown_origin(self):
        with pytest.raises(ValueError, match="no encontrado"):
            calcular_rutas("FaketownXYZ", municipios_path=MUNICIPIOS_FILE)

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_sorted_by_time(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(3))
        result = calcular_rutas("Valencia", sort_by="time", municipios_path=MUNICIPIOS_FILE)
        times = [r["duracion_min"] for r in result]
        assert times == sorted(times)

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_sorted_by_distance(self, mock_urlopen):
        mock_urlopen.return_value = _mock_response(_osrm_ok_response(3))
        result = calcular_rutas("Valencia", sort_by="distance", municipios_path=MUNICIPIOS_FILE)
        dists = [r["distancia_km"] for r in result]
        assert dists == sorted(dists)

    @patch("calcular_rutas.urllib.request.urlopen")
    def test_skipped_on_connection_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("down")
        result = calcular_rutas("Valencia", municipios_path=MUNICIPIOS_FILE)
        # All skipped → empty result
        assert result == []


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
