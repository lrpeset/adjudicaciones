"""
test_parse_zones.py

Comprehensive tests for parse_zones.py.
Covers regex patterns, line classification, PDF parsing, JSON export,
and the get_zone_by_municipio merge helper.

Run:
    .venv/bin/python test_parse_zones.py
"""

import json
import os
import sys
import tempfile

import pandas as pd
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(__file__))

from parse_zones import (
    PROVINCES,
    RE_AREA,
    RE_MUNICIPALITY,
    RE_PROVINCE,
    RE_SKIP,
    RE_SUBAREA,
    _classify_line,
    _extract_trailing_text,
    _try_merge_municipality,
    get_zone_by_municipio,
    parse_zones,
    parse_zones_text,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

_pass = 0
_fail = 0


def assert_eq(label, actual, expected):
    global _pass, _fail
    if actual == expected:
        _pass += 1
        print(f"  [PASS] {label}")
    else:
        _fail += 1
        print(f"  [FAIL] {label}")
        print(f"         expected: {expected!r}")
        print(f"         actual  : {actual!r}")


def assert_true(label, condition):
    assert_eq(label, bool(condition), True)


def _make_pdf(text_lines: list[str]) -> str:
    """
    Create a minimal valid PDF with the given lines as separate text objects.
    Each line becomes a separate Td + Tj operation, producing proper newlines.

    Returns the path to the temporary PDF file.
    """
    # Build PDF content stream: each line gets its own Td/Tj
    stream_parts = ["BT", "/F1 10 Tf", "50 750 Td"]
    for i, line in enumerate(text_lines):
        # Escape PDF special chars
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if i > 0:
            stream_parts.append("0 -15 Td")
        stream_parts.append(f"({escaped}) Tj")
    stream_parts.append("ET")

    stream_content = "\n".join(stream_parts)
    stream_bytes = stream_content.encode("latin-1")

    # Build the PDF as raw bytes with correct /Length
    header = b"%PDF-1.4\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n")
    obj4_head = b"4 0 obj\n<< /Length "
    obj4_mid = b" >>\nstream\n"
    obj4_tail = b"\nendstream\nendobj\n"
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"

    # Assemble and compute xref offsets
    parts = []
    offsets = []
    pos = 0

    for chunk in [header, obj1, obj2, obj3, obj4_head]:
        parts.append(chunk)
        pos += len(chunk)

    # Length value
    length_str = str(len(stream_bytes)).encode()
    parts.append(length_str)
    pos += len(length_str)

    parts.append(obj4_mid)
    pos += len(obj4_mid)

    offsets.append(pos)  # start of stream content
    parts.append(stream_bytes)
    pos += len(stream_bytes)

    parts.append(obj4_tail)
    pos += len(obj4_tail)

    obj5_offset = pos
    parts.append(obj5)
    pos += len(obj5)

    xref_offset = pos

    # xref table
    xref = (
        f"xref\n0 6\n"
        f"0000000000 65535 f \n"
        f"0000000009 00000 n \n"
        f"0000000058 00000 n \n"
        f"0000000115 00000 n \n"
        f"0000000266 00000 n \n"
        f"{obj5_offset:010d} 00000 n \n"
        f"trailer\n<< /Size 6 /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    pdf_bytes = b"".join(parts) + xref

    tmp = tempfile.NamedTemporaryFile(
        mode="wb", suffix=".pdf", delete=False, dir="/tmp",
    )
    tmp.write(pdf_bytes)
    tmp.close()
    return tmp.name


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Regex Pattern Validation
# ═══════════════════════════════════════════════════════════════════════════

def test_phase1():
    print("\n══ PHASE 1: Regex Pattern Validation ══")

    # --- 1a. RE_PROVINCE matches province headers ------------------------
    province_matches = [
        ("03 ALACANT", "03"),
        ("12 CASTELLÓ", "12"),
        ("46 VALÈNCIA", "46"),
        ("03 Alacant", "03"),
        ("12 Castelló", "12"),
        ("46 València", "46"),
        ("  03 ALACANT  ", "03"),
        ("46 VALENCIA", "46"),  # without accent
    ]
    for text, expected_code in province_matches:
        m = RE_PROVINCE.match(text)
        assert_true(f"1a-province match: '{text}'", m)
        if m:
            # Find which group matched
            code = m.group(1) or m.group(2) or m.group(3)
            assert_eq(f"1a-province code: '{text}'", code, expected_code)

    # Should NOT match
    assert_true("1a-province reject 'ALACANT'", not RE_PROVINCE.match("ALACANT"))
    assert_true("1a-province reject '03018'", not RE_PROVINCE.match("03018"))
    assert_true("1a-province reject '031'", not RE_PROVINCE.match("031"))

    # --- 1b. RE_AREA matches area headers --------------------------------
    area_matches = [
        ("031", "031"),
        ("032", "032"),
        ("121", "121"),
        ("461", "461"),
        ("462", "462"),
        ("  031  ", "031"),
    ]
    for text, expected_code in area_matches:
        m = RE_AREA.match(text)
        assert_true(f"1b-area match: '{text}'", m)
        if m:
            assert_eq(f"1b-area code: '{text}'", m.group(1), expected_code)

    # Should NOT match
    assert_true("1b-area reject '030'", not RE_AREA.match("030"))
    assert_true("1b-area reject '0311'", not RE_AREA.match("0311"))
    assert_true("1b-area reject '03018'", not RE_AREA.match("03018"))
    assert_true("1b-area reject '999'", not RE_AREA.match("999"))

    # --- 1c. RE_SUBAREA matches subarea headers --------------------------
    subarea_matches = [
        ("0311", "0311"),
        ("0312", "0312"),
        ("1211", "1211"),
        ("4611", "4611"),
        ("  0311  ", "0311"),
    ]
    for text, expected_code in subarea_matches:
        m = RE_SUBAREA.match(text)
        assert_true(f"1c-subarea match: '{text}'", m)
        if m:
            assert_eq(f"1c-subarea code: '{text}'", m.group(1), expected_code)

    # Should NOT match
    assert_true("1c-subarea reject '031'", not RE_SUBAREA.match("031"))
    assert_true("1c-subarea reject '03018'", not RE_SUBAREA.match("03018"))
    assert_true("1c-subarea reject '03111'", not RE_SUBAREA.match("03111"))

    # --- 1d. RE_MUNICIPALITY matches municipality entries -----------------
    muni_matches = [
        ("03018 ALTEA", "03018", "ALTEA"),
        ("03032 BENIDORM", "03032", "BENIDORM"),
        ("12001 ALBA DE TORMES", "12001", "ALBA DE TORMES"),
        ("46250 VALENCIA", "46250", "VALENCIA"),
        ("  03018 ALTEA  ", "03018", "ALTEA"),
        ("03018 ALTEA/ALICANTE", "03018", "ALTEA/ALICANTE"),
    ]
    for text, expected_code, expected_name in muni_matches:
        m = RE_MUNICIPALITY.match(text)
        assert_true(f"1d-muni match: '{text}'", m)
        if m:
            assert_eq(f"1d-muni code: '{text}'", m.group(1), expected_code)
            assert_eq(f"1d-muni name: '{text}'", m.group(2).strip(), expected_name)

    # Should NOT match
    assert_true("1d-muni reject '031'", not RE_MUNICIPALITY.match("031"))
    assert_true("1d-muni reject '0311'", not RE_MUNICIPALITY.match("0311"))
    assert_true("1d-muni reject 'ALTEA'", not RE_MUNICIPALITY.match("ALTEA"))

    # --- 1e. RE_SKIP matches header/footer lines -------------------------
    skip_lines = [
        "Pàg 1",
        "LLISTAT D'ÀREES",
        "ANNEX I",
        "CODI A UTILITZAR",
        "LOCALITATS",
        "01/01/2024",
        "",
    ]
    for line in skip_lines:
        assert_true(f"1e-skip: '{line[:30]}'", RE_SKIP.search(line))

    # Should NOT skip actual data
    assert_true("1e-skip reject '03018 ALTEA'",
                not RE_SKIP.search("03018 ALTEA"))

    # --- 1f. PROVINCES dict is complete ----------------------------------
    assert_eq("1f-provinces count", len(PROVINCES), 3)
    assert_eq("1f-03", PROVINCES["03"], "Alacant")
    assert_eq("1f-12", PROVINCES["12"], "Castelló")
    assert_eq("1f-46", PROVINCES["46"], "València")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Line Classification
# ═══════════════════════════════════════════════════════════════════════════

def test_phase2():
    print("\n══ PHASE 2: Line Classification ══")

    initial_state = {
        "provincia_code": "",
        "provincia_name": "",
        "area": "",
        "subarea": "",
    }

    # --- 2a. Province line updates state ----------------------------------
    line_type, state = _classify_line("03 ALACANT", initial_state)
    assert_eq("2a-province type", line_type, "province")
    assert_eq("2a-province code", state["provincia_code"], "03")
    assert_eq("2a-province name", state["provincia_name"], "Alacant")

    # --- 2b. Area line updates state (requires province) ------------------
    state_with_prov = {
        "provincia_code": "03",
        "provincia_name": "Alacant",
        "area": "",
        "subarea": "",
    }
    line_type, state = _classify_line("031", state_with_prov)
    assert_eq("2b-area type", line_type, "area")
    assert_eq("2b-area code", state["area"], "031")
    assert_eq("2b-area preserves prov", state["provincia_code"], "03")

    # --- 2c. Subarea line updates state (requires area) -------------------
    state_with_area = {
        "provincia_code": "03",
        "provincia_name": "Alacant",
        "area": "031",
        "subarea": "",
    }
    line_type, state = _classify_line("0311", state_with_area)
    assert_eq("2c-subarea type", line_type, "subarea")
    assert_eq("2c-subarea code", state["subarea"], "0311")
    assert_eq("2c-subarea preserves area", state["area"], "031")

    # --- 2d. Municipality line returns data dict --------------------------
    state_with_sub = {
        "provincia_code": "03",
        "provincia_name": "Alacant",
        "area": "031",
        "subarea": "0311",
    }
    line_type, data = _classify_line("03018 ALTEA", state_with_sub)
    assert_eq("2d-muni type", line_type, "municipality")
    assert_eq("2d-muni code", data["code"], "03018")
    assert_eq("2d-muni name", data["name"], "ALTEA")
    assert_eq("2d-muni area", data["area"], "031")
    assert_eq("2d-muni subarea", data["subarea"], "0311")
    assert_eq("2d-muni prov", data["provincia_name"], "Alacant")

    # --- 2e. Municipality rejected when wrong province --------------------
    state_castello = {
        "provincia_code": "12",
        "provincia_name": "Castelló",
        "area": "121",
        "subarea": "1211",
    }
    line_type, _ = _classify_line("03018 ALTEA", state_castello)
    assert_eq("2e-wrong prov", line_type, "unknown")

    # --- 2f. Skip lines return "skip" ------------------------------------
    for skip_text in ["", "Pàg 1", "LLISTAT"]:
        line_type, _ = _classify_line(skip_text, initial_state)
        assert_eq(f"2f-skip '{skip_text[:20]}'", line_type, "skip")

    # --- 2g. Unknown lines return "unknown" ------------------------------
    line_type, _ = _classify_line("Random text here", initial_state)
    assert_eq("2g-unknown", line_type, "unknown")

    # --- 2h. Province changes reset area and subarea ---------------------
    state_with_all = {
        "provincia_code": "03",
        "provincia_name": "Alacant",
        "area": "031",
        "subarea": "0311",
    }
    line_type, state = _classify_line("12 CASTELLÓ", state_with_all)
    assert_eq("2h-new prov resets", state["area"], "")
    assert_eq("2h-new prov resets sub", state["subarea"], "")
    assert_eq("2h-new prov code", state["provincia_code"], "12")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Multi-line Municipality Handling
# ═══════════════════════════════════════════════════════════════════════════

def test_phase3():
    print("\n══ PHASE 3: Multi-line Municipality Handling ══")

    state = {
        "provincia_code": "03",
        "provincia_name": "Alacant",
        "area": "031",
        "subarea": "0311",
    }

    # --- 3a. Single line match -------------------------------------------
    muni, consumed = _try_merge_municipality(["03018 ALTEA"], 0, state)
    assert_true("3a-single line match", muni is not None)
    assert_eq("3a-consumed", consumed, 1)
    assert_eq("3a-code", muni["codigo_municipio"], "03018")
    assert_eq("3a-name", muni["municipio"], "ALTEA")

    # --- 3b. Split across two lines --------------------------------------
    lines = ["03018", "ALTEA"]
    muni, consumed = _try_merge_municipality(lines, 0, state)
    assert_true("3b-split match", muni is not None)
    assert_eq("3b-consumed", consumed, 2)
    assert_eq("3b-code", muni["codigo_municipio"], "03018")
    assert_eq("3b-name", muni["municipio"], "ALTEA")

    # --- 3c. Split but next line is a section header ---------------------
    lines = ["03018", "031"]
    muni, consumed = _try_merge_municipality(lines, 0, state)
    assert_true("3c-no match next is area", muni is None)
    assert_eq("3c-consumed", consumed, 0)

    # --- 3d. No match on garbage line ------------------------------------
    muni, consumed = _try_merge_municipality(["Random text"], 0, state)
    assert_true("3d-no match", muni is None)
    assert_eq("3d-consumed", consumed, 0)

    # --- 3e. Split but next line is empty --------------------------------
    lines = ["03018", ""]
    muni, consumed = _try_merge_municipality(lines, 0, state)
    assert_true("3e-no match empty next", muni is None)

    # --- 3f. Split but next line is province header ----------------------
    lines = ["03018", "12 CASTELLÓ"]
    muni, consumed = _try_merge_municipality(lines, 0, state)
    assert_true("3f-no match prov header", muni is None)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — PDF Parsing Integration
# ═══════════════════════════════════════════════════════════════════════════

def test_phase4():
    print("\n══ PHASE 4: PDF Parsing Integration ══")

    # Create a test PDF with known content
    test_lines = [
        "LLISTAT D'ÀREES, SUBÀREES, LOCALITATS I CENTRES",
        "03 ALACANT",
        "031 Marina Baixa",
        "0311 Alt Vinalopó",
        "03018 ALTEA",
        "03032 BENIDORM",
        "0312 Vega Baja",
        "03056 ELCHE",
        "032 Comtat",
        "0321 Comtat Interior",
        "03211 COCENTAINA",
        "12 CASTELLÓ",
        "121 Plana Alta",
        "1211 Castelló Nord",
        "12001 ALBORMA",
        "12002 ALMASSORA",
        "46 VALÈNCIA",
        "461 Horta",
        "4611 Camp de Túria",
        "46250 VALENCIA",
        "46260 ALDAIA",
    ]

    pdf_path = _make_pdf(test_lines)

    try:
        municipalities = parse_zones_text(pdf_path)

        # --- 4a. Extracted municipalities count ---------------------------
        assert_true("4a-has municipalities", len(municipalities) > 0)

        # --- 4b. All 3 provinces represented ------------------------------
        provinces_found = set(m["provincia"] for m in municipalities)
        assert_true("4b-has Alacant", "Alacant" in provinces_found)
        assert_true("4b-has Castelló", "Castelló" in provinces_found)
        assert_true("4b-has València", "València" in provinces_found)

        # --- 4c. Specific municipality found ------------------------------
        altea = [m for m in municipalities if m["municipio"] == "ALTEA"]
        assert_true("4c-ALTEA found", len(altea) > 0)
        if altea:
            assert_eq("4c-ALTEA code", altea[0]["codigo_municipio"], "03018")
            assert_eq("4c-ALTEA area", altea[0]["area"], "031")
            assert_eq("4c-ALTEA subarea", altea[0]["subarea"], "0311")
            assert_eq("4c-ALTEA prov", altea[0]["provincia"], "Alacant")

        # --- 4d. Municipality code format is 5 digits --------------------
        for m in municipalities:
            assert_true(f"4d-code 5 digits: {m['codigo_municipio']}",
                        len(m["codigo_municipio"]) == 5)

        # --- 4e. Municipality names are uppercase ------------------------
        for m in municipalities:
            assert_eq(f"4e-uppercase: {m['municipio']}",
                      m["municipio"], m["municipio"].upper())

    finally:
        os.unlink(pdf_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — JSON Export
# ═══════════════════════════════════════════════════════════════════════════

def test_phase5():
    print("\n══ PHASE 5: JSON Export ══")

    test_lines = [
        "03 ALACANT",
        "031 Marina Baixa",
        "0311 Alt Vinalopó",
        "03018 ALTEA",
        "03032 BENIDORM",
        "12 CASTELLÓ",
        "121 Plana Alta",
        "1211 Castelló Nord",
        "12001 ALBORMA",
        "46 VALÈNCIA",
        "461 Horta",
        "4611 Camp de Túria",
        "46250 VALENCIA",
    ]

    pdf_path = _make_pdf(test_lines)
    json_path = "/tmp/test_zonas_output.json"

    try:
        municipalities = parse_zones(pdf_path, json_path)

        # --- 5a. JSON file created ---------------------------------------
        assert_true("5a-json exists", os.path.exists(json_path))

        # --- 5b. JSON is valid -------------------------------------------
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert_true("5b-valid json", isinstance(data, dict))

        # --- 5c. JSON has "municipios" key --------------------------------
        assert_true("5c-has municipios key", "municipios" in data)

        # --- 5d. JSON structure matches spec ------------------------------
        muni_list = data["municipios"]
        assert_true("5d-municipios is list", isinstance(muni_list, list))
        if muni_list:
            first = muni_list[0]
            assert_true("5d-has codigo_municipio", "codigo_municipio" in first)
            assert_true("5d-has municipio", "municipio" in first)
            assert_true("5d-has subarea", "subarea" in first)
            assert_true("5d-has area", "area" in first)
            assert_true("5d-has provincia", "provincia" in first)

        # --- 5e. Return value matches file content -----------------------
        assert_eq("5e-return matches file", len(municipalities), len(muni_list))

        # --- 5f. JSON is human-readable (indented) -----------------------
        with open(json_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert_true("5f-indented", "\n  " in content)

    finally:
        os.unlink(pdf_path)
        if os.path.exists(json_path):
            os.unlink(json_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — get_zone_by_municipio Merge Helper
# ═══════════════════════════════════════════════════════════════════════════

def test_phase6():
    print("\n══ PHASE 6: get_zone_by_municipio Merge ══")

    # Create mock adjudicacion data
    df_adj = pd.DataFrame([
        {
            "Índex": 1,
            "Tipo": "VACANTE",
            "Municipio": "ALTEA",
            "Centro_Código": "03018129",
            "Centro_Nombre": "IES TEST",
            "Lloc": "123456",
            "ITI": "NO",
            "Observaciones": "",
            "Obs_Tags": [],
            "Req_Lingüístic": "",
            "Especialidad": "120 - EDUCACIÓN INFANTIL",
            "Provincia": "Alacant",
            "Cos": "MAESTROS",
        },
        {
            "Índex": 2,
            "Tipo": "VACANTE",
            "Municipio": "BENIDORM",
            "Centro_Código": "03032456",
            "Centro_Nombre": "CEIP TEST",
            "Lloc": "234567",
            "ITI": "NO",
            "Observaciones": "",
            "Obs_Tags": [],
            "Req_Lingüístic": "",
            "Especialidad": "120 - EDUCACIÓN INFANTIL",
            "Provincia": "Alacant",
            "Cos": "MAESTROS",
        },
        {
            "Índex": 3,
            "Tipo": "VACANTE",
            "Municipio": "VALENCIA",
            "Centro_Código": "46250789",
            "Centro_Nombre": "IES CAPITAL",
            "Lloc": "345678",
            "ITI": "NO",
            "Observaciones": "",
            "Obs_Tags": [],
            "Req_Lingüístic": "",
            "Especialidad": "120 - EDUCACIÓN INFANTIL",
            "Provincia": "València",
            "Cos": "MAESTROS",
        },
    ])

    # Create a mock zonas.json
    zonas_data = {
        "municipios": [
            {"codigo_municipio": "03018", "municipio": "ALTEA", "subarea": "0311", "area": "031", "provincia": "Alacant"},
            {"codigo_municipio": "03032", "municipio": "BENIDORM", "subarea": "0311", "area": "031", "provincia": "Alacant"},
            {"codigo_municipio": "46250", "municipio": "VALENCIA", "subarea": "4611", "area": "461", "provincia": "València"},
            {"codigo_municipio": "12001", "municipio": "ALBORMA", "subarea": "1211", "area": "121", "provincia": "Castelló"},
        ]
    }

    zonas_path = "/tmp/test_zonas_merge.json"
    with open(zonas_path, "w", encoding="utf-8") as f:
        json.dump(zonas_data, f, ensure_ascii=False, indent=2)

    try:
        # --- 6a. Merge adds zone columns ---------------------------------
        result = get_zone_by_municipio(df_adj, zonas_path)
        assert_true("6a-has Zona_Area", "Zona_Area" in result.columns)
        assert_true("6a-has Zona_Subarea", "Zona_Subarea" in result.columns)
        assert_true("6a-has Zona_Provincia", "Zona_Provincia" in result.columns)

        # --- 6b. Match by code (first 5 digits of Centro_Código) ---------
        altea_row = result[result["Municipio"] == "ALTEA"].iloc[0]
        assert_eq("6b-altea area", altea_row["Zona_Area"], "031")
        assert_eq("6b-altea subarea", altea_row["Zona_Subarea"], "0311")
        assert_eq("6b-altea prov", altea_row["Zona_Provincia"], "Alacant")

        # --- 6c. BENIDORM matched ----------------------------------------
        beni_row = result[result["Municipio"] == "BENIDORM"].iloc[0]
        assert_eq("6c-benidorm area", beni_row["Zona_Area"], "031")
        assert_eq("6c-benidorm subarea", beni_row["Zona_Subarea"], "0311")

        # --- 6d. VALENCIA matched ----------------------------------------
        val_row = result[result["Municipio"] == "VALENCIA"].iloc[0]
        assert_eq("6d-valencia area", val_row["Zona_Area"], "461")
        assert_eq("6d-valencia subarea", val_row["Zona_Subarea"], "4611")

        # --- 6e. Original columns preserved ------------------------------
        assert_true("6e-original col preserved", "Municipio" in result.columns)
        assert_true("6e-original col preserved2", "Especialidad" in result.columns)

        # --- 6f. Municipality name fallback match ------------------------
        # Test with a row that has no Centro_Código
        df_no_code = pd.DataFrame([{
            "Índex": 99,
            "Municipio": "ALBORMA",
            "Provincia": "Castelló",
        }])
        result_fb = get_zone_by_municipio(df_no_code, zonas_path)
        assert_eq("6f-fallback area", result_fb.iloc[0]["Zona_Area"], "121")
        assert_eq("6f-fallback subarea", result_fb.iloc[0]["Zona_Subarea"], "1211")

        # --- 6g. Non-matching rows get empty strings ---------------------
        df_unknown = pd.DataFrame([{
            "Índex": 100,
            "Municipio": "UNKNOWN_TOWN",
            "Centro_Código": "99999999",
        }])
        result_un = get_zone_by_municipio(df_unknown, zonas_path)
        assert_eq("6g-unknown area", result_un.iloc[0]["Zona_Area"], "")
        assert_eq("6g-unknown subarea", result_un.iloc[0]["Zona_Subarea"], "")

    finally:
        if os.path.exists(zonas_path):
            os.unlink(zonas_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 7 — All Three Provinces Coverage
# ═══════════════════════════════════════════════════════════════════════════

def test_phase7():
    print("\n══ PHASE 7: All Three Provinces Coverage ══")

    test_lines = [
        "03 ALACANT",
        "031 Marina Baixa",
        "0311 Alt Vinalopó",
        "03018 ALTEA",
        "03032 BENIDORM",
        "032 Comtat",
        "0321 Comtat Interior",
        "03211 COCENTAINA",
        "03212 ALCOI",
        "12 CASTELLÓ",
        "121 Plana Alta",
        "1211 Castelló Nord",
        "12001 ALBORMA",
        "12002 ALMASSORA",
        "122 Plana Baixa",
        "1221 Vila-real",
        "12211 VILA-REAL",
        "46 VALÈNCIA",
        "461 Horta",
        "4611 Camp de Túria",
        "46250 VALENCIA",
        "46260 ALDAIA",
        "462 Ribera",
        "4621 Ribera Alta",
        "46211 ALZIRA",
    ]

    pdf_path = _make_pdf(test_lines)
    json_path = "/tmp/test_zonas_coverage.json"

    try:
        municipalities = parse_zones(pdf_path, json_path)

        # --- 7a. All 3 provinces present ---------------------------------
        provs = set(m["provincia"] for m in municipalities)
        assert_true("7a-has Alacant", "Alacant" in provs)
        assert_true("7a-has Castelló", "Castelló" in provs)
        assert_true("7a-has València", "València" in provs)

        # --- 7b. Province counts -----------------------------------------
        alacant = [m for m in municipalities if m["provincia"] == "Alacant"]
        castello = [m for m in municipalities if m["provincia"] == "Castelló"]
        valencia = [m for m in municipalities if m["provincia"] == "València"]

        assert_true("7b-Alacant has entries", len(alacant) >= 2)
        assert_true("7b-Castelló has entries", len(castello) >= 2)
        assert_true("7b-València has entries", len(valencia) >= 2)

        # --- 7c. Areas and subareas populated ----------------------------
        areas = set(m["area"] for m in municipalities)
        subareas = set(m["subarea"] for m in municipalities)
        assert_true("7c-multiple areas", len(areas) >= 3)
        assert_true("7c-multiple subareas", len(subareas) >= 3)

        # --- 7d. Cross-check: every municipality has area and subarea ----
        for m in municipalities:
            assert_true(f"7d-has area: {m['municipio']}", len(m["area"]) > 0)
            assert_true(f"7d-has subarea: {m['municipio']}", len(m["subarea"]) > 0)

        # --- 7e. Area codes start with province code ---------------------
        for m in municipalities:
            prov_code = m["codigo_municipio"][:2]
            assert_true(f"7e-area matches prov: {m['municipio']}",
                        m["area"].startswith(prov_code))

        # --- 7f. Subarea codes start with area code ----------------------
        for m in municipalities:
            assert_true(f"7f-subarea matches area: {m['municipio']}",
                        m["subarea"].startswith(m["area"]))

        # --- 7g. JSON round-trip valid ------------------------------------
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert_eq("7g-json count", len(data["municipios"]), len(municipalities))

    finally:
        os.unlink(pdf_path)
        if os.path.exists(json_path):
            os.unlink(json_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 8 — Edge Cases & Error Handling
# ═══════════════════════════════════════════════════════════════════════════

def test_phase8():
    print("\n══ PHASE 8: Edge Cases & Error Handling ══")

    # --- 8a. Empty PDF ---------------------------------------------------
    pdf_empty = _make_pdf([""])
    try:
        result = parse_zones_text(pdf_empty)
        assert_eq("8a-empty pdf", len(result), 0)
    finally:
        os.unlink(pdf_empty)

    # --- 8b. PDF with only headers, no municipalities --------------------
    pdf_headers = _make_pdf([
        "LLISTAT D'ÀREES",
        "03 ALACANT",
        "031 Marina Baixa",
    ])
    try:
        result = parse_zones_text(pdf_headers)
        assert_eq("8b-headers only", len(result), 0)
    finally:
        os.unlink(pdf_headers)

    # --- 8c. Municipality without active subarea -------------------------
    pdf_no_sub = _make_pdf([
        "03 ALACANT",
        "031 Marina Baixa",
        "03018 ALTEA",
    ])
    try:
        result = parse_zones_text(pdf_no_sub)
        assert_true("8c-muni without subarea", len(result) > 0)
        if result:
            assert_eq("8c-empty subarea", result[0]["subarea"], "")
    finally:
        os.unlink(pdf_no_sub)

    # --- 8d. Multiple areas within same province -------------------------
    pdf_multi = _make_pdf([
        "03 ALACANT",
        "031 Marina Baixa",
        "0311 Alt Vinalopó",
        "03018 ALTEA",
        "032 Comtat",
        "0321 Comtat Interior",
        "03211 COCENTAINA",
    ])
    try:
        result = parse_zones_text(pdf_multi)
        areas = set(m["area"] for m in result)
        assert_true("8d-multiple areas", len(areas) >= 2)
    finally:
        os.unlink(pdf_multi)

    # --- 8e. get_zone_by_municipio with empty JSON -----------------------
    zonas_empty = {"municipios": []}
    zonas_path = "/tmp/test_zonas_empty.json"
    with open(zonas_path, "w") as f:
        json.dump(zonas_empty, f)

    df_simple = pd.DataFrame([{"Municipio": "ALTEA"}])
    try:
        result = get_zone_by_municipio(df_simple, zonas_path)
        assert_eq("8e-empty zonas area", result.iloc[0]["Zona_Area"], "")
    finally:
        os.unlink(zonas_path)

    # --- 8f. get_zone_by_municipio with missing columns ------------------
    zonas_path = "/tmp/test_zonas_missing.json"
    with open(zonas_path, "w") as f:
        json.dump({"municipios": []}, f)

    df_minimal = pd.DataFrame([{"col": "val"}])
    try:
        result = get_zone_by_municipio(df_minimal, zonas_path)
        assert_true("8e-missing cols handled", "Zona_Area" in result.columns)
    finally:
        os.unlink(zonas_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 9 — _extract_trailing_text helper
# ═══════════════════════════════════════════════════════════════════════════

def test_phase9():
    print("\n══ PHASE 9: _extract_trailing_text ══")

    # --- 9a. Extract text after area code ----------------------------------
    assert_eq("9a-area name", _extract_trailing_text("031 Marina Baixa", "031"), "Marina Baixa")
    assert_eq("9a-area single", _extract_trailing_text("032 Comtat", "032"), "Comtat")
    assert_eq("9a-area valencia", _extract_trailing_text("461 Horta", "461"), "Horta")

    # --- 9b. Extract text after subarea code ------------------------------
    assert_eq("9b-subarea name", _extract_trailing_text("0311 Alt Vinalopó", "0311"), "Alt Vinalopó")
    assert_eq("9b-subarea castello", _extract_trailing_text("1211 Castelló Nord", "1211"), "Castelló Nord")
    assert_eq("9b-subarea valencia", _extract_trailing_text("4611 Camp de Túria", "4611"), "Camp de Túria")

    # --- 9c. No trailing text ---------------------------------------------
    assert_eq("9c-no text", _extract_trailing_text("031", "031"), "")
    assert_eq("9c-no text sub", _extract_trailing_text("0311", "0311"), "")

    # --- 9d. Line doesn't start with code ---------------------------------
    assert_eq("9d-wrong prefix", _extract_trailing_text("ALTEA", "03018"), "")
    assert_eq("9d-empty line", _extract_trailing_text("", "031"), "")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 10 — Subarea/Area nombre extraction in parse_zones_text
# ═══════════════════════════════════════════════════════════════════════════

def test_phase10():
    print("\n══ PHASE 10: Subarea/Area nombre extraction ══")

    test_lines = [
        "03 ALACANT",
        "031 Marina Baixa",
        "0311 Alt Vinalopó",
        "03018 ALTEA",
        "03032 BENIDORM",
        "032 Comtat",
        "0321 Comtat Interior",
        "03211 COCENTAINA",
        "12 CASTELLÓ",
        "121 Plana Alta",
        "1211 Castelló Nord",
        "12001 ALBORMA",
        "46 VALÈNCIA",
        "461 Horta",
        "4611 Camp de Túria",
        "46250 VALENCIA",
        "46260 ALDAIA",
    ]

    pdf_path = _make_pdf(test_lines)

    try:
        municipalities = parse_zones_text(pdf_path)

        # --- 10a. subarea_nombre is populated -----------------------------
        altea = [m for m in municipalities if m["municipio"] == "ALTEA"]
        assert_true("10a-altea found", len(altea) > 0)
        if altea:
            assert_eq("10a-altea subarea_nombre", altea[0]["subarea_nombre"], "Alt Vinalopó")
            assert_eq("10a-altea area_nombre", altea[0]["area_nombre"], "Marina Baixa")

        # --- 10b. Different subarea has its own name ----------------------
        cocentaina = [m for m in municipalities if m["municipio"] == "COCENTAINA"]
        assert_true("10b-cocentaina found", len(cocentaina) > 0)
        if cocentaina:
            assert_eq("10b-cocentaina subarea_nombre", cocentaina[0]["subarea_nombre"], "Comtat Interior")
            assert_eq("10b-cocentaina area_nombre", cocentaina[0]["area_nombre"], "Comtat")

        # --- 10c. Different province --------------------------------------
        valencia = [m for m in municipalities if m["municipio"] == "VALENCIA"]
        assert_true("10c-valencia found", len(valencia) > 0)
        if valencia:
            assert_eq("10c-valencia subarea_nombre", valencia[0]["subarea_nombre"], "Camp de Túria")
            assert_eq("10c-valencia area_nombre", valencia[0]["area_nombre"], "Horta")

        # --- 10d. subarea_nombre is string (not None) --------------------
        for m in municipalities:
            assert_true(f"10d-type: {m['municipio']}",
                        isinstance(m["subarea_nombre"], str))
            assert_true(f"10d-area type: {m['municipio']}",
                        isinstance(m["area_nombre"], str))

    finally:
        os.unlink(pdf_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 11 — get_zone_by_municipio includes Zona_Subarea_Nombre
# ═══════════════════════════════════════════════════════════════════════════

def test_phase11():
    print("\n══ PHASE 11: get_zone_by_municipio Zona_Subarea_Nombre ══")

    df_adj = pd.DataFrame([
        {
            "Municipio": "ALTEA",
            "Centro_Código": "03018129",
        },
        {
            "Municipio": "BENIDORM",
            "Centro_Código": "03032456",
        },
        {
            "Municipio": "VALENCIA",
            "Centro_Código": "46250789",
        },
    ])

    # Zonas with subarea_nombre populated
    zonas_data = {
        "municipios": [
            {"codigo_municipio": "03018", "municipio": "ALTEA", "subarea": "0311", "subarea_nombre": "Alt Vinalopó", "area": "031", "provincia": "Alacant"},
            {"codigo_municipio": "03032", "municipio": "BENIDORM", "subarea": "0311", "subarea_nombre": "Alt Vinalopó", "area": "031", "provincia": "Alacant"},
            {"codigo_municipio": "46250", "municipio": "VALENCIA", "subarea": "4611", "subarea_nombre": "Camp de Túria", "area": "461", "provincia": "València"},
        ]
    }

    zonas_path = "/tmp/test_zonas_nombre.json"
    with open(zonas_path, "w", encoding="utf-8") as f:
        json.dump(zonas_data, f, ensure_ascii=False, indent=2)

    try:
        result = get_zone_by_municipio(df_adj, zonas_path)

        # --- 11a. Column exists -------------------------------------------
        assert_true("11a-has Zona_Subarea_Nombre", "Zona_Subarea_Nombre" in result.columns)

        # --- 11b. Values populated correctly ------------------------------
        altea_row = result[result["Municipio"] == "ALTEA"].iloc[0]
        assert_eq("11b-altea nombre", altea_row["Zona_Subarea_Nombre"], "Alt Vinalopó")

        val_row = result[result["Municipio"] == "VALENCIA"].iloc[0]
        assert_eq("11b-valencia nombre", val_row["Zona_Subarea_Nombre"], "Camp de Túria")

        # --- 11c. Old format zonas.json (no subarea_nombre field) ---------
        zonas_old = {
            "municipios": [
                {"codigo_municipio": "03018", "municipio": "ALTEA", "subarea": "0311", "area": "031", "provincia": "Alacant"},
            ]
        }
        with open(zonas_path, "w", encoding="utf-8") as f:
            json.dump(zonas_old, f, ensure_ascii=False, indent=2)

        result_old = get_zone_by_municipio(df_adj, zonas_path)
        assert_true("11c-old format handled", "Zona_Subarea_Nombre" in result_old.columns)
        altea_old = result_old[result_old["Municipio"] == "ALTEA"].iloc[0]
        assert_eq("11c-old format empty", altea_old["Zona_Subarea_Nombre"], "")

    finally:
        if os.path.exists(zonas_path):
            os.unlink(zonas_path)


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    test_phase1()
    test_phase2()
    test_phase3()
    test_phase4()
    test_phase5()
    test_phase6()
    test_phase7()
    test_phase8()
    test_phase9()
    test_phase10()
    test_phase11()

    print(f"\n{'═' * 50}")
    print(f"  RESULTS: {_pass} passed, {_fail} failed")
    print(f"{'═' * 50}")
    sys.exit(1 if _fail else 0)
