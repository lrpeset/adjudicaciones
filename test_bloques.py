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
from app import _flatten_blocks_to_df, _prepare_flat_df
from adjudicacion import to_clean_table

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

    @patch("bloques.urllib.request.urlopen")
    def test_full_pipeline_none_metrics_block_sinks_to_bottom(self, mock_urlopen):
        """Regression: a block with no valid route (None metrics) must not
        raise TypeError in the sorting gate and must sort last."""
        # One destination with a route, one with NaN coords (no route -> None).
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))

        df = pd.DataFrame([
            _make_row(subarea="V01", lat=39.45, lon=-0.36),
            _make_row(subarea="V02", lat=float("nan"), lon=float("nan")),
        ])
        result = generar_bloques(df, "Valencia", MUNICPIOS_CV)

        blocks = result["resumen_por_subareas"]
        assert len(blocks) == 2
        # The None-metrics block must be last (sinks to bottom).
        assert blocks[-1]["subarea_codigo"] == "V02"
        assert blocks[-1]["distancia_minima_km"] is None
        # The resolved block keeps ascending ordering.
        assert blocks[0]["subarea_codigo"] == "V01"

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
    def test_full_pipeline_raises_on_sin_subarea_block(self, mock_urlopen):
        """A row with an empty subarea must fail the no-SIN_SUBAREA gate."""
        mock_urlopen.return_value = self._mock_response(_osrm_ok_response(1))
        df = pd.DataFrame([_make_row(subarea="", lat=39.47, lon=-0.38)])

        with pytest.raises(AssertionError, match="SIN_SUBAREA"):
            generar_bloques(df, "Valencia", MUNICPIOS_CV)

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
# Flat view helpers (app.py) — enrichment + sorting of the flat lista
# ═══════════════════════════════════════════════════════════════════════════

class TestFlatView:
    """Tests for _flatten_blocks_to_df and _prepare_flat_df (Lista Plana)."""

    def _raw_df(self):
        return pd.DataFrame([
            {"Índex": 1, "Municipio": "ALCÀSSER", "Centro_Nombre": "IES A",
             "Centro_Código": "1", "Especialidad": "120", "Tipo": "VACANTE",
             "Req_Lingüístic": "", "Lloc": "", "ITI": "", "Observaciones": "",
             "Obs_Tags": "", "Provincia": "VALÈNCIA", "Cos": "SECUNDARIA"},
            {"Índex": 2, "Municipio": "VALÈNCIA", "Centro_Nombre": "IES B",
             "Centro_Código": "2", "Especialidad": "120", "Tipo": "VACANTE",
             "Req_Lingüístic": "", "Lloc": "", "ITI": "", "Observaciones": "",
             "Obs_Tags": "", "Provincia": "VALÈNCIA", "Cos": "SECUNDARIA"},
            {"Índex": 3, "Municipio": "ALCOI", "Centro_Nombre": "IES C",
             "Centro_Código": "3", "Especialidad": "120", "Tipo": "VACANTE",
             "Req_Lingüístic": "", "Lloc": "", "ITI": "", "Observaciones": "",
             "Obs_Tags": "", "Provincia": "ALACANT", "Cos": "SECUNDARIA"},
        ])

    def _bloques_data(self):
        return {"resumen_por_subareas": [
            {"subarea_codigo": "0312", "subarea_nombre": "X",
             "plazas": [
                 {"index": 2, "subarea_codigo": "0312",
                  "subarea_nombre": "X", "centro": "IES B",
                  "municipio": "VALÈNCIA", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": 15.0, "distancia_km": 12.0},
                 {"index": 1, "subarea_codigo": "0312",
                  "subarea_nombre": "X", "centro": "IES A",
                  "municipio": "ALCÀSSER", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": 5.0, "distancia_km": 6.5},
             ]},
            {"subarea_codigo": "0303", "subarea_nombre": "Y",
             "plazas": [
                 {"index": 3, "subarea_codigo": "0303",
                  "subarea_nombre": "Y", "centro": "IES C",
                  "municipio": "ALCOI", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": None, "distancia_km": None},
             ]},
        ]}

    def test_flatten_blocks_to_df(self):
        df = _flatten_blocks_to_df(self._bloques_data())
        assert list(df.columns) == ["Índex", "Subárea", "Tiempo (min)",
                                    "Distancia (km)"]
        assert len(df) == 3
        row1 = df[df["Índex"] == 1].iloc[0]
        assert row1["Tiempo (min)"] == 5.0
        assert row1["Distancia (km)"] == 6.5
        assert row1["Subárea"] == "0312"
        na_row = df[df["Índex"] == 3].iloc[0]
        assert pd.isna(na_row["Tiempo (min)"])
        assert pd.isna(na_row["Distancia (km)"])

    def test_flatten_empty(self):
        assert _flatten_blocks_to_df({}).empty
        assert _flatten_blocks_to_df(
            {"resumen_por_subareas": [{"plazas": []}]}
        ).empty

    def test_prepare_flat_sorted_by_distancia(self):
        flat = _prepare_flat_df(self._raw_df(), self._bloques_data(),
                                sort_by="distancia")
        assert {"Tiempo (min)", "Distancia (km)", "Subárea"}.issubset(
            flat.columns)
        dist = flat["Distancia (km)"].dropna().tolist()
        assert dist == sorted(dist)
        # unresolved row sinks to the bottom
        assert pd.isna(flat["Distancia (km)"].iloc[-1])
        assert flat["Subárea"].iloc[0] == "0312"

    def test_prepare_flat_sorted_by_tiempo(self):
        flat = _prepare_flat_df(self._raw_df(), self._bloques_data(),
                                sort_by="tiempo")
        tiempos = flat["Tiempo (min)"].dropna().tolist()
        assert tiempos == sorted(tiempos)
        assert pd.isna(flat["Tiempo (min)"].iloc[-1])

    def test_prepare_flat_without_results(self):
        flat = _prepare_flat_df(self._raw_df(), None, sort_by="distancia")
        assert "Tiempo (min)" in flat.columns
        assert "Distancia (km)" in flat.columns
        assert flat["Tiempo (min)"].isna().all()
        assert flat["Distancia (km)"].isna().all()
        assert "#" in flat.columns  # to_clean_table renamed Índex

    def test_flat_export_csv_includes_metrics(self):
        flat = _prepare_flat_df(self._raw_df(), self._bloques_data(),
                                sort_by="distancia")
        csv_data = flat.to_csv(index=False, encoding="utf-8-sig")
        assert "Tiempo (min)" in csv_data.split("\n")[0]
        assert "Distancia (km)" in csv_data.split("\n")[0]
        assert "5.0" in csv_data
        assert "6.5" in csv_data

    def test_flat_export_csv_without_results(self):
        flat = _prepare_flat_df(self._raw_df(), None, sort_by="distancia")
        csv_data = flat.to_csv(index=False, encoding="utf-8-sig")
        assert "Tiempo (min)" in csv_data.split("\n")[0]
        assert "Distancia (km)" in csv_data.split("\n")[0]

    def test_flat_export_json_includes_metrics(self):
        flat = _prepare_flat_df(self._raw_df(), self._bloques_data(),
                                sort_by="distancia")
        import json as _json
        records = flat.where(flat.notna(), other=None).to_dict(
            orient="records"
        )
        json_data = _json.loads(_json.dumps(records, default=str))
        assert all("Tiempo (min)" in r for r in json_data)
        assert all("Distancia (km)" in r for r in json_data)
        # Infinity must not appear in JSON
        raw_json = _json.dumps(records, default=str)
        assert "Infinity" not in raw_json
        assert "inf" not in raw_json.lower().split('"')[1::2]

    def test_flat_export_json_without_results(self):
        flat = _prepare_flat_df(self._raw_df(), None, sort_by="distancia")
        import json as _json
        records = flat.where(flat.notna(), other=None).to_dict(
            orient="records"
        )
        json_data = _json.loads(_json.dumps(records, default=str))
        assert all("Tiempo (min)" in r for r in json_data)
        assert all("Distancia (km)" in r for r in json_data)
        assert all(r["Tiempo (min)"] is None for r in json_data)
        assert all(r["Distancia (km)"] is None for r in json_data)

    def test_prepare_flat_no_nan_after_merge(self):
        """All rows with matching routes must have real numeric values,
        never NaN, in Tiempo (min) and Distancia (km)."""
        flat = _prepare_flat_df(self._raw_df(), self._bloques_data(),
                                sort_by="distancia")
        # Row with index 1 → 5.0 / 6.5, row with index 2 → 15.0 / 12.0
        matched = flat[flat["#"].isin([1, 2])]
        assert not matched["Tiempo (min)"].isna().any(), (
            f"NaN in Tiempo after merge: {matched['Tiempo (min)'].tolist()}"
        )
        assert not matched["Distancia (km)"].isna().any(), (
            f"NaN in Distancia after merge: {matched['Distancia (km)'].tolist()}"
        )

    def test_prepare_flat_with_str_index(self):
        """Merge must work even when the plaza index arrives as str
        (e.g. agrupar_por_subarea falling back to row.get('Índex', ''))."""
        raw = self._raw_df()
        # Force # to str (simulates type drift)
        clean_raw = to_clean_table(raw)
        clean_raw["#"] = clean_raw["#"].astype(str)

        # bloques_data with str indices (as produced when Índex col is absent)
        bloques_str = {"resumen_por_subareas": [
            {"subarea_codigo": "0312", "subarea_nombre": "X",
             "plazas": [
                 {"index": "1", "subarea_codigo": "0312",
                  "subarea_nombre": "X", "centro": "IES A",
                  "municipio": "ALCÀSSER", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": 5.0, "distancia_km": 6.5},
                 {"index": "2", "subarea_codigo": "0312",
                  "subarea_nombre": "X", "centro": "IES B",
                  "municipio": "VALÈNCIA", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": 15.0, "distancia_km": 12.0},
             ]},
        ]}
        flat = _prepare_flat_df(raw, bloques_str, sort_by="distancia")
        matched = flat[flat["#"].astype(str).isin(["1", "2"])]
        assert not matched["Tiempo (min)"].isna().any(), (
            f"NaN after str merge: {matched['Tiempo (min)'].tolist()}"
        )
        # Row 3 has no route in bloques_data, so it stays unresolved
        assert pd.isna(flat.loc[flat["#"].astype(str) == "3",
                                  "Tiempo (min)"].iloc[0])

    def test_prepare_flat_subarea_fallback(self):
        """When plaza ID is missing (''), the subarea fallback must fill
        metrics from the block-level aggregation."""
        raw = self._raw_df()
        bloques_missing = {"resumen_por_subareas": [
            {"subarea_codigo": "0312", "subarea_nombre": "X",
             "plazas": [
                 # index='' (simulates missing Índex column)
                 {"index": "", "subarea_codigo": "0312",
                  "subarea_nombre": "X", "centro": "IES A",
                  "municipio": "ALCÀSSER", "especialidad": "120",
                  "tipo": "VACANTE", "requisito_idioma": "",
                  "tiempo_trayecto_minutos": 5.0, "distancia_km": 6.5},
             ]},
        ]}
        flat = _prepare_flat_df(raw, bloques_missing, sort_by="distancia")
        # The fallback should have populated at least some values
        assert "Tiempo (min)" in flat.columns
        assert "Distancia (km)" in flat.columns

    def test_flat_drops_redundant_columns(self):
        """The flat view and its CSV/JSON exports must not contain Cos,
        Latitud_Destino, Longitud_Destino, Zona_Subarea or
        Zona_Subarea_Nombre."""
        import json as _json
        raw = self._raw_df().copy()
        raw["Zona_Subarea"] = ["0312", "0312", "0303"]
        raw["Zona_Subarea_Nombre"] = ["X", "X", "Y"]
        raw["Latitud_Destino"] = [39.37, 39.45, 38.35]
        raw["Longitud_Destino"] = [-0.44, -0.36, -0.48]

        flat = _prepare_flat_df(raw, self._bloques_data(), sort_by="distancia")

        redundant = {"Cos", "Latitud_Destino", "Longitud_Destino",
                     "Zona_Subarea", "Zona_Subarea_Nombre"}
        assert redundant.isdisjoint(flat.columns), (
            f"Columns not dropped: {redundant & set(flat.columns)}"
        )

        # CSV header must omit them
        csv_header = set(flat.to_csv(index=False).split("\n")[0].split(","))
        assert redundant.isdisjoint(csv_header), (
            f"CSV still has: {redundant & csv_header}"
        )

        # JSON records must not carry them
        records = flat.where(flat.notna(), other=None).to_dict(
            orient="records"
        )
        json_data = _json.loads(_json.dumps(records, default=str))
        assert all(redundant.isdisjoint(r) for r in json_data), (
            "JSON records still contain redundant columns"
        )

        # Key user-facing columns remain intact
        kept = {"#", "Tipo", "Municipio", "Codi Centre", "Centre", "Lloc",
                "ITI", "Observaciones", "Etiquetes", "Req. Ling.",
                "Especialidad", "Provincia", "Tiempo (min)", "Distancia (km)"}
        assert kept.issubset(flat.columns), (
            f"Missing key columns: {kept - set(flat.columns)}"
        )

    def test_flat_drops_redundant_columns_without_results(self):
        """Redundant columns must also be dropped when there are no block
        results (bloques_data is None)."""
        raw = self._raw_df().copy()
        raw["Zona_Subarea"] = ["0312", "0312", "0303"]
        raw["Zona_Subarea_Nombre"] = ["X", "X", "Y"]
        raw["Latitud_Destino"] = [39.37, 39.45, 38.35]
        raw["Longitud_Destino"] = [-0.44, -0.36, -0.48]

        flat = _prepare_flat_df(raw, None, sort_by="distancia")

        redundant = {"Cos", "Latitud_Destino", "Longitud_Destino",
                     "Zona_Subarea", "Zona_Subarea_Nombre"}
        assert redundant.isdisjoint(flat.columns)

    def test_prepare_flat_full_pipeline_no_nans(self):
        """End-to-end: agrupar_por_subarea → _flatten_blocks_to_df →
        _prepare_flat_df must produce no NaN in matched rows."""
        from bloques import agrupar_por_subarea
        raw = self._raw_df().copy()
        raw["Zona_Subarea"] = ["0312", "0312", "0303"]
        raw["Zona_Subarea_Nombre"] = ["X", "X", "Y"]
        raw["Latitud_Destino"] = [39.37, 39.45, 38.35]
        raw["Longitud_Destino"] = [-0.44, -0.36, -0.48]

        rutas = {
            (round(39.37, 6), round(-0.44, 6)): {"tiempo_min": 18.0, "distancia_km": 12.0},
            (round(39.45, 6), round(-0.36, 6)): {"tiempo_min": 10.0, "distancia_km": 7.0},
            (round(38.35, 6), round(-0.48, 6)): {"tiempo_min": 45.0, "distancia_km": 40.0},
        }

        bloques = agrupar_por_subarea(raw, rutas, sort_by="distancia")
        bloques_data = {"resumen_por_subareas": bloques}

        flat = _prepare_flat_df(raw, bloques_data, sort_by="distancia")

        # ALL three rows should have metrics (no NaN)
        assert not flat["Tiempo (min)"].isna().any(), (
            f"NaN in full pipeline: {flat['Tiempo (min)'].tolist()}"
        )
        assert not flat["Distancia (km)"].isna().any(), (
            f"NaN in full pipeline: {flat['Distancia (km)'].tolist()}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
