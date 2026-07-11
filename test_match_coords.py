"""
test_match_coords.py

Tests for match_coords.py: coordinate injection into adjudicacion DataFrames.

Covers:
  - Name normalization
  - Municipality code extraction
  - Coordinate injection by name and code
  - Edge cases: empty DataFrames, missing columns, unknown municipalities

Run:
    .venv/bin/python -m pytest test_match_coords.py -v
"""

import json
import os
import sys
import tempfile

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(__file__))

from match_coords import (
    _extract_municipality_code,
    _extract_base_muni_name,
    _normalize_name,
    build_center_to_muni_code,
    build_majority_subarea_map,
    build_zone_code_lookup,
    inject_coordinates,
    load_municipios_cv,
    validate_critical_subareas,
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

MUNICPIOS_CV = os.path.join(os.path.dirname(__file__), "municipios_cv.json")


def _make_adj_row(idx=1, municipio="Aldaia", codigo="46012345", nombre="CEIP Test"):
    return {
        "Centro_Codigo": codigo,
        "Municipio": municipio,
        "Centro_Nombre": nombre,
        "Tipo": "VACANTE",
        "Especialidad": "120",
    }


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Name Normalization
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeName:
    def test_lowercase(self):
        assert _normalize_name("VALÈNCIA") == "valencia"

    def test_accents_stripped(self):
        assert _normalize_name("Castelló de la Plana") == "castello de la plana"

    def test_empty_string(self):
        assert _normalize_name("") == ""

    def test_none_returns_empty(self):
        assert _normalize_name(None) == ""

    def test_slash_preserved_as_chars(self):
        result = _normalize_name("Alacant/Alicante")
        assert result == "alacantalicante"

    def test_whitespace_normalized(self):
        result = _normalize_name("  Aldaia  ")
        assert result == "aldaia"

    def test_non_string_returns_empty(self):
        assert _normalize_name(123) == ""


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1b — Base Name Extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractBaseMuniName:
    def test_elx_altabix(self):
        assert _extract_base_muni_name("ELX - ALTABIX") == "ELX"

    def test_almoradi_heredades(self):
        assert _extract_base_muni_name("ALMORADÍ - HEREDADES") == "ALMORADÍ"

    def test_no_dash_unchanged(self):
        assert _extract_base_muni_name("ALBAL") == "ALBAL"

    def test_no_dash_with_spaces(self):
        assert _extract_base_muni_name("Castelló de la Plana") == "Castelló de la Plana"

    def test_none_passthrough(self):
        assert _extract_base_muni_name(None) is None

    def test_empty_string(self):
        assert _extract_base_muni_name("") == ""

    def test_elx_vallverda(self):
        assert _extract_base_muni_name("ELX - VALLVERDA") == "ELX"

    def test_multiple_dashes_takes_first(self):
        assert _extract_base_muni_name("A - B - C") == "A"

    def test_single_suffix(self):
        assert _extract_base_muni_name("ALDAIA - SOMEPLACE") == "ALDAIA"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Municipality Code Extraction
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractMunicipalityCode:
    """DEPRECATED function now always returns None to prevent wrong mappings."""

    def test_returns_none_for_46_prefix(self):
        assert _extract_municipality_code("46001234") is None

    def test_returns_none_for_03_prefix(self):
        assert _extract_municipality_code("03018001") is None

    def test_returns_none_for_12_prefix(self):
        assert _extract_municipality_code("12345678") is None

    def test_too_short(self):
        assert _extract_municipality_code("AB") is None

    def test_invalid_prefix(self):
        assert _extract_municipality_code("99001234") is None

    def test_empty_string(self):
        assert _extract_municipality_code("") is None

    def test_none(self):
        assert _extract_municipality_code(None) is None

    def test_strips_whitespace(self):
        assert _extract_municipality_code("  46001234  ") is None


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Load Municipios CV
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadMunicipiosCv:
    def test_loads_successfully(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        assert "by_name" in data
        assert "raw" in data
        assert len(data["raw"]) > 0

    def test_all_entries_have_required_keys(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        for m in data["raw"]:
            assert "nombre" in m
            assert "provincia" in m
            assert "latitud" in m
            assert "longitud" in m

    def test_by_name_indexed_by_normalized(self):
        data = load_municipios_cv(MUNICPIOS_CV)
        for m in data["raw"]:
            norm = _normalize_name(m["nombre"])
            assert norm in data["by_name"]


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — Zone Code Lookup
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildZoneCodeLookup:
    def test_valid_zonas_json(self):
        zonas_data = {
            "municipios": [
                {"codigo_municipio": "46250", "municipio": "VALENCIA"},
                {"codigo_municipio": "03018", "municipio": "ALTEA"},
            ]
        }
        path = "/tmp/test_zonas_lookup.json"
        with open(path, "w") as f:
            json.dump(zonas_data, f)
        try:
            lookup = build_zone_code_lookup(path)
            assert "46250" in lookup
            assert lookup["46250"] == "valencia"
            assert "03018" in lookup
            assert lookup["03018"] == "altea"
        finally:
            os.unlink(path)

    def test_missing_file_returns_empty(self):
        lookup = build_zone_code_lookup("/tmp/nonexistent_zonas_abc.json")
        assert lookup == {}


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — Coordinate Injection
# ═══════════════════════════════════════════════════════════════════════════

class TestInjectCoordinates:
    def test_injects_by_name(self):
        df = pd.DataFrame([_make_adj_row(municipio="Aldaia")])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert "Latitud_Destino" in result.columns
        assert "Longitud_Destino" in result.columns
        assert not result["Latitud_Destino"].isna().any()
        assert not result["Longitud_Destino"].isna().any()

    def test_injects_case_insensitive(self):
        df = pd.DataFrame([_make_adj_row(municipio="ALDAIA")])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert not result["Latitud_Destino"].isna().any()

    def test_unknown_municipality_gets_nan(self):
        df = pd.DataFrame([_make_adj_row(municipio="Faketown")])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert result["Latitud_Destino"].isna().all()
        assert result["Longitud_Destino"].isna().all()

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["Centro_Codigo", "Municipio"])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert result.empty
        assert "Latitud_Destino" in result.columns

    def test_no_cento_codigo_column(self):
        df = pd.DataFrame([{"Municipio": "Aldaia"}])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert not result["Latitud_Destino"].isna().any()

    def test_injects_by_code_with_areas_subareas(self):
        """Test code-based injection using areas_subareas.json for real muni code."""
        areas_data = {
            "46250123": {
                "centro_nombre": "CEIP Test",
                "programa": "PIL",
                "localidad_codigo": "462500001",
                "localidad_nombre": "VALENCIA",
                "subarea_codigo": "4661",
                "area_codigo": "46",
            }
        }
        zonas_data = {
            "municipios": [
                {"codigo_municipio": "46250", "municipio": "VALENCIA",
                 "subarea": "4661", "area": "466", "provincia": "Valencia"},
            ]
        }
        areas_path = "/tmp/test_areas_subareas_inject.json"
        zonas_path = "/tmp/test_zonas_inject.json"
        with open(areas_path, "w") as f:
            json.dump(areas_data, f)
        with open(zonas_path, "w") as f:
            json.dump(zonas_data, f)
        try:
            df = pd.DataFrame([_make_adj_row(codigo="46250123", municipio="X")])
            result = inject_coordinates(df, MUNICPIOS_CV, zonas_path,
                                        areas_subareas_path=areas_path)
            assert not result["Latitud_Destino"].isna().any()
        finally:
            os.unlink(areas_path)
            os.unlink(zonas_path)

    def test_multiple_rows(self):
        df = pd.DataFrame([
            _make_adj_row(idx=1, municipio="Aldaia", codigo="46012345"),
            _make_adj_row(idx=2, municipio="Altea", codigo="03018001"),
            _make_adj_row(idx=3, municipio="Faketown", codigo="99999001"),
        ])
        result = inject_coordinates(df, MUNICPIOS_CV)
        assert len(result) == 3
        assert not result.iloc[0]["Latitud_Destino"] != result.iloc[0]["Latitud_Destino"]  # not NaN
        assert result.iloc[2]["Latitud_Destino"] != result.iloc[2]["Latitud_Destino"]  # is NaN


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — Center to Municipality Code Lookup
# ═══════════════════════════════════════════════════════════════════════════

AREAS_SUBAREAS = os.path.join(os.path.dirname(__file__), "areas_subareas.json")


class TestBuildCenterToMuniCode:
    def test_builds_from_real_file(self):
        lookup = build_center_to_muni_code(AREAS_SUBAREAS)
        assert len(lookup) > 0

    def test_aldaia_center_maps_to_46021(self):
        lookup = build_center_to_muni_code(AREAS_SUBAREAS)
        assert lookup.get("46015071") == "46021"

    def test_cofrentes_center_maps_to_46097(self):
        lookup = build_center_to_muni_code(AREAS_SUBAREAS)
        assert lookup.get("46015371") == "46097"

    def test_missing_file_returns_empty(self):
        lookup = build_center_to_muni_code("/tmp/nonexistent_areas.json")
        assert lookup == {}

    def test_from_synthetic_file(self):
        data = {
            "46015071": {"localidad_codigo": "460210001"},
            "46015371": {"localidad_codigo": "460970001"},
        }
        path = "/tmp/test_center_to_muni.json"
        with open(path, "w") as f:
            json.dump(data, f)
        try:
            lookup = build_center_to_muni_code(path)
            assert lookup["46015071"] == "46021"
            assert lookup["46015371"] == "46097"
        finally:
            os.unlink(path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7 — Majority Subarea Map
# ═══════════════════════════════════════════════════════════════════════════

class TestBuildMajoritySubareaMap:
    def test_majority_wins(self):
        data = {
            "c1": {"localidad_codigo": "460210001", "subarea_codigo": "4642"},
            "c2": {"localidad_codigo": "460210002", "subarea_codigo": "4642"},
            "c3": {"localidad_codigo": "460210003", "subarea_codigo": "4643"},
        }
        path = "/tmp/test_majority.json"
        with open(path, "w") as f:
            json.dump(data, f)
        try:
            result = build_majority_subarea_map(path)
            assert result["46021"] == "4642"
        finally:
            os.unlink(path)

    def test_single_center(self):
        data = {
            "c1": {"localidad_codigo": "460970001", "subarea_codigo": "4635"},
        }
        path = "/tmp/test_majority_single.json"
        with open(path, "w") as f:
            json.dump(data, f)
        try:
            result = build_majority_subarea_map(path)
            assert result["46097"] == "4635"
        finally:
            os.unlink(path)

    def test_real_file_aldaia(self):
        result = build_majority_subarea_map(AREAS_SUBAREAS)
        assert result.get("46021") == "4642"

    def test_real_file_cofrentes(self):
        result = build_majority_subarea_map(AREAS_SUBAREAS)
        assert result.get("46097") == "4635"


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 8 — Quality Gate Validation
# ═══════════════════════════════════════════════════════════════════════════

class TestValidateCriticalSubareas:
    def test_passes_for_correct_subareas(self):
        df = pd.DataFrame([
            {"Municipio": "Aldaia", "Zona_Subarea": "4642", "Centro_Nombre": "Test"},
            {"Municipio": "Cofrentes", "Zona_Subarea": "4635", "Centro_Nombre": "Test2"},
        ])
        validate_critical_subareas(df)

    def test_fails_for_wrong_aldaia(self):
        df = pd.DataFrame([
            {"Municipio": "Aldaia", "Zona_Subarea": "4644", "Centro_Nombre": "Wrong"},
        ])
        with pytest.raises(AssertionError, match="4642"):
            validate_critical_subareas(df)

    def test_fails_for_wrong_cofrentes(self):
        df = pd.DataFrame([
            {"Municipio": "Cofrentes", "Zona_Subarea": "4644", "Centro_Nombre": "Wrong"},
        ])
        with pytest.raises(AssertionError, match="4635"):
            validate_critical_subareas(df)

    def test_skips_missing_municipalities(self):
        df = pd.DataFrame([
            {"Municipio": "Otro", "Zona_Subarea": "9999"},
        ])
        validate_critical_subareas(df)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 9 — Real Data Integration Test
# ═══════════════════════════════════════════════════════════════════════════

class TestRealDataIntegration:
    def test_aldaia_gets_correct_coordinates(self):
        df = pd.DataFrame([_make_adj_row(municipio="Aldaia", codigo="46015071")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        areas_path = AREAS_SUBAREAS
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path,
                                    areas_subareas_path=areas_path)
        aldaia = load_municipios_cv(MUNICPIOS_CV)["by_name"]["aldaia"]
        assert result.iloc[0]["Latitud_Destino"] == aldaia["latitud"]
        assert result.iloc[0]["Longitud_Destino"] == aldaia["longitud"]

    def test_cofrentes_gets_correct_coordinates(self):
        df = pd.DataFrame([_make_adj_row(municipio="Cofrentes", codigo="46015371")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        areas_path = AREAS_SUBAREAS
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path,
                                    areas_subareas_path=areas_path)
        cofrentes = load_municipios_cv(MUNICPIOS_CV)["by_name"]["cofrentes"]
        assert result.iloc[0]["Latitud_Destino"] == cofrentes["latitud"]
        assert result.iloc[0]["Longitud_Destino"] == cofrentes["longitud"]

    def test_elx_altabix_resolves_via_base_name(self):
        """ELX - ALTABIX must resolve to ELX's subarea (0351) and coordinates."""
        df = pd.DataFrame([_make_adj_row(municipio="ELX - ALTABIX", codigo="03065100")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path)
        assert result.iloc[0]["Zona_Subarea"] == "0351"
        assert not result.iloc[0]["Latitud_Destino"] != result.iloc[0]["Latitud_Destino"]

    def test_almoradi_heredades_resolves_via_base_name(self):
        """ALMORADÍ - HEREDADES must resolve to ALMORADÍ' subarea (0361)."""
        df = pd.DataFrame([_make_adj_row(municipio="ALMORADÍ - HEREDADES", codigo="03015100")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path)
        assert result.iloc[0]["Zona_Subarea"] == "0361"
        assert not result.iloc[0]["Latitud_Destino"] != result.iloc[0]["Latitud_Destino"]

    def test_elx_vallverda_resolves_via_base_name(self):
        """ELX - VALLVERDA must resolve to ELX's subarea (0351)."""
        df = pd.DataFrame([_make_adj_row(municipio="ELX - VALLVERDA", codigo="03065200")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path)
        assert result.iloc[0]["Zona_Subarea"] == "0351"

    def test_plain_elx_still_works(self):
        """Plain ELX (no suffix) must still resolve normally."""
        df = pd.DataFrame([_make_adj_row(municipio="ELX", codigo="03065100")])
        zonas_path = os.path.join(os.path.dirname(__file__), "zonas.json")
        result = inject_coordinates(df, MUNICPIOS_CV, zonas_path)
        assert result.iloc[0]["Zona_Subarea"] == "0351"


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
