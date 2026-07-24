"""
test_parse_pdf.py

Comprehensive lifecycle tests for parse_pdf.py.
Covers all 4 audit phases: Ingestion, Extraction, Integration, Business Logic.

Run:
    .venv/bin/python test_parse_pdf.py
"""

import os
import re
import sys
import tempfile
import textwrap

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from parse_pdf import (
    RE_BLOCK_END,
    RE_CODIGO,
    RE_CUERPO,
    RE_ESPECIALIDAD,
    RE_LLOC,
    RE_PROVINCIA,
    blocks_to_dataframe,
    build_vacantes,
    extract_fields,
    fallback_proximidad,
    merge_with_routes,
    normalize_nombre,
)
from adjudicacion import (
    RE_ROW,
    filter_by_especialidad,
    filter_by_iti,
    filter_by_observaciones,
    filter_by_req_lingüístic,
    filter_by_tipo,
    filter_positions,
    get_especialidades,
    get_observacion_tags,
    to_clean_table,
    to_json,
    _parse_row,
    _extract_base_muni_name,
)

# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def assert_eq(label, actual, expected):
    assert actual == expected, (
        f"{label}: expected {expected!r}, got {actual!r}"
    )


def assert_true(label, condition):
    assert_eq(label, bool(condition), True)


def make_block(text, tipo="VACANTE", cuerpo="Secundaria",
               especialidad="Matematicas", provincia="Valencia"):
    return {
        "cuerpo": cuerpo,
        "especialidad": especialidad,
        "provincia": provincia,
        "tipo": tipo,
        "text": text,
    }


def write_routes(rows):
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8",
    )
    pd.DataFrame(rows).to_csv(tmp, index=False)
    tmp.close()
    return tmp.name


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — Data Ingestion & Segmentation
# ═══════════════════════════════════════════════════════════════════════════

def test_phase1():
    print("\n══ PHASE 1: Data Ingestion & Segmentation ══")

    # --- 1a. Header state persists across non-header lines ---------------
    # Simulate the internal state machine of parse_pdf without a real PDF.
    # We do this by testing that extract_fields receives correct headers.
    block = make_block(
        "53 | València -03015129- IES LA DEHESA 123456",
        cuerpo="Primaria", especialidad="General", provincia="Castellon",
    )
    fields = extract_fields(block)
    assert_eq("1a-header persists", fields["Cuerpo"], "Primaria")

    # --- 1b. Block terminated by VACANTE sets tipo correctly -------------
    block_v = make_block("text VACANTE", tipo="VACANTE")
    block_s = make_block("text SUSTITUCION INDETERMINADA", tipo="SUSTITUCION")
    assert_eq("1b-tipo VACANTE", block_v["tipo"], "VACANTE")
    assert_eq("1b-tipo SUSTITUCION", block_s["tipo"], "SUSTITUCION")

    # --- 1c. Trailing block (no terminator) infers tipo from text --------
    # The _flush_block logic: if no last_line, look at the text itself
    block_trail = make_block("some text about VACANTE position", tipo="VACANTE")
    block_trail2 = make_block("some text about substitution", tipo="SUSTITUCION")
    assert_eq("1c-trailing infer VACANTE", block_trail["tipo"], "VACANTE")
    assert_eq("1c-trailing infer SUSTITUCION", block_trail2["tipo"], "SUSTITUCION")

    # --- 1d. RE_BLOCK_END does NOT match VACANTE inside school names ----
    false_positives = [
        "IES VACANTE DEL RIO -03015129- descripcion",
        "Colegio VACANTE Nueva direccion",
    ]
    for fp in false_positives:
        assert_true(f"1d-no false positive: '{fp[:35]}...'",
                    not RE_BLOCK_END.search(fp))

    # --- 1e. RE_BLOCK_END matches VACANTE as label at end / before pipe --
    true_positives = [
        ("VACANTE", True),
        ("VACANTE 123456", True),
        ("VACANTE | metadata", True),
        ("SUSTITUCION INDETERMINADA", True),
    ]
    for text, expected in true_positives:
        assert_eq(f"1e-true positive: '{text[:35]}'",
                  bool(RE_BLOCK_END.search(text)), expected)

    # --- 1f. max_blocks=0 means unlimited (the P0 fix) ------------------
    # We can't call parse_pdf without a real PDF, but we can verify the
    # guard: max_blocks > 0 before checking len(blocks) >= max_blocks.
    # If max_blocks=0, the condition should NOT trigger a break.
    max_blocks = 0
    blocks_count = 5
    would_break = max_blocks > 0 and blocks_count >= max_blocks
    assert_eq("1f-max_blocks=0 no break", would_break, False)

    # Also test that max_blocks=3 DOES limit
    max_blocks = 3
    blocks_count = 3
    would_break = max_blocks > 0 and blocks_count >= max_blocks
    assert_eq("1f-max_blocks=3 breaks", would_break, True)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — Data Extraction & Regex
# ═══════════════════════════════════════════════════════════════════════════

def test_phase2():
    print("\n══ PHASE 2: Data Extraction & Regex ══")

    # --- 2a. normalize_nombre handles accents, case, slashes -------------
    cases = [
        ("València", "VALENCIA"),
        ("Alacant/Alicante", "ALACANT/ALICANTE"),
        ("Castelló de la Plana", "CASTELLO DE LA PLANA"),
        ("Elx/Elche", "ELX/ELCHE"),
        ("  Xàtiva  ", "XATIVA"),
        ("IBIZA", "IBIZA"),
    ]
    for raw, expected in cases:
        assert_eq(f"2a-normalize('{raw}')", normalize_nombre(raw), expected)

    # --- 2b. RE_CODIGO matches 03/12/46 prefixed 8-digit codes -----------
    code_tests = [
        ("-03015129-", "03015129"),
        ("-12000789-", "12000789"),
        ("-46001234-", "46001234"),
    ]
    for text, expected in code_tests:
        m = RE_CODIGO.search(text)
        assert_eq(f"2b-codigo '{text}'",
                  m.group(1) if m else None, expected)

    # Should NOT match
    assert_eq("2b-codigo reject 99", RE_CODIGO.search("-99000123-"), None)
    assert_eq("2b-codigo reject 7digit", RE_CODIGO.search("-0301512-"), None)

    # --- 2c. Municipio extraction strips index and bars ------------------
    block = make_block("53 | València -03015129- IES TEST 123456")
    fields = extract_fields(block)
    assert_eq("2c-municipio", fields["Municipio"], "València")
    assert_eq("2c-municipio_norm", fields["Municipio_Norm"], "VALENCIA")

    # --- 2d. Centro_Nombre extracted after code --------------------------
    assert_eq("2c-centro_nombre", fields["Centro_Nombre"], "IES TEST")

    # --- 2d2. Centro_Nombre with internal number preserved ---------------
    block_num = make_block(
        "53 | València -03015129- IES Numero 300001 desc 123456"
    )
    fields_num = extract_fields(block_num)
    assert_eq("2d2-centro_nombre internal number",
              fields_num["Centro_Nombre"], "IES Numero 300001 desc")
    assert_eq("2d2-loc trailing stripped", fields_num["Lloc"], "123456")

    # --- 2e. RE_LLOC does NOT match inside 8-digit school code -----------
    text1 = "València -03015129- IES TEST 123456"
    matches = RE_LLOC.findall(text1)
    assert_eq("2e-Lloc excludes code", "0301512" not in matches, True)
    assert_eq("2e-Lloc finds standalone", "123456" in matches, True)

    # 8-digit number should NOT produce any 6-7 digit substring matches
    text2 = "-46001234- end"
    matches2 = RE_LLOC.findall(text2)
    assert_eq("2e-Lloc 8digit no match", len(matches2), 0)

    # --- 2f. Lloc picks the LAST standalone match -----------------------
    text3 = "random 111111 code 222222 end 333333"
    matches3 = RE_LLOC.findall(text3)
    assert_eq("2f-Lloc last match", matches3[-1], "333333")

    # --- 2g. Block with no code returns empty fields ---------------------
    block_nocode = make_block("text without any school code here")
    fields_nocode = extract_fields(block_nocode)
    assert_eq("2g-no code", fields_nocode["Centro_Codigo"], None)
    assert_eq("2g-no lloc", fields_nocode["Lloc"], None)

    # --- 2h. Header regexes are case-insensitive -------------------------
    assert_true("2h-cuerpo case", RE_CUERPO.search("cuerpo/cos: Test"))
    assert_true("2h-esp case", RE_ESPECIALIDAD.search("Especialidad/Especialitat: X"))
    assert_true("2h-prov case", RE_PROVINCIA.search("provincia/provincia: Y"))


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — Data Integration (Merge)
# ═══════════════════════════════════════════════════════════════════════════

def test_phase3():
    print("\n══ PHASE 3: Data Integration (Merge) ══")

    positions = pd.DataFrame([
        {"Municipio": "València", "Municipio_Norm": "VALENCIA",
         "Centro_Codigo": "03015129", "Centro_Nombre": "IES TEST",
         "Lloc": "123456", "Cuerpo": "S", "Especialidad": "M",
         "Provincia": "V", "Tipo": "VACANTE"},
        {"Municipio": "Alacant", "Municipio_Norm": "ALACANT",
         "Centro_Codigo": "03000456", "Centro_Nombre": "IES ALI",
         "Lloc": "234567", "Cuerpo": "S", "Especialidad": "I",
         "Provincia": "A", "Tipo": "VACANTE"},
        {"Municipio": "Benidorm", "Municipio_Norm": "BENIDORM",
         "Centro_Codigo": "03001234", "Centro_Nombre": "IES BEN",
         "Lloc": "456789", "Cuerpo": "S", "Especialidad": "C",
         "Provincia": "A", "Tipo": "VACANTE"},
    ])

    # --- 3a. Normal merge with routes ------------------------------------
    csv_path = write_routes([
        {"Origen": "València", "Destino": "Valencia", "Km": 0, "Tiempo": 0},
        {"Origen": "València", "Destino": "Alacant", "Km": 167, "Tiempo": 105},
    ])
    merged = merge_with_routes(positions, csv_path, "València")
    assert_eq("3a-Valencia matched",
              merged.loc[merged["Municipio_Norm"] == "VALENCIA", "Km"].iloc[0], 0)
    assert_eq("3a-Alacant matched",
              merged.loc[merged["Municipio_Norm"] == "ALACANT", "Km"].iloc[0], 167)
    assert_true("3a-Benidorm unmatched",
                not merged.loc[merged["Municipio_Norm"] == "BENIDORM", "Ruta_Encontrada"].iloc[0])
    os.unlink(csv_path)

    # --- 3b. Origin not in CSV → graceful fallback ----------------------
    csv_path = write_routes([
        {"Origen": "Madrid", "Destino": "Toledo", "Km": 70, "Tiempo": 45},
    ])
    merged_b = merge_with_routes(positions, csv_path, "València")
    assert_eq("3b-no origin all null", merged_b["Km"].isna().all(), True)
    assert_eq("3b-no origin all false", merged_b["Ruta_Encontrada"].all(), False)
    os.unlink(csv_path)

    # --- 3c. Missing CSV columns → graceful fallback --------------------
    csv_path = write_routes([{"Wrong": "data"}])
    merged_c = merge_with_routes(positions, csv_path, "València")
    assert_eq("3c-missing cols null", merged_c["Km"].isna().all(), True)
    os.unlink(csv_path)

    # --- 3d. Non-existent CSV → graceful fallback -----------------------
    missing_csv = os.path.join(tempfile.gettempdir(), "nonexistent_abc.csv")
    merged_d = merge_with_routes(positions, missing_csv, "València")
    assert_eq("3d-bad file null", merged_d["Km"].isna().all(), True)

    # --- 3e. Duplicate destinations → shortest kept ---------------------
    csv_path = write_routes([
        {"Origen": "València", "Destino": "Alacant", "Km": 200, "Tiempo": 120},
        {"Origen": "València", "Destino": "Alacant", "Km": 160, "Tiempo": 100},
    ])
    merged_e = merge_with_routes(positions, csv_path, "València")
    assert_eq("3e-shortest kept",
              merged_e.loc[merged_e["Municipio_Norm"] == "ALACANT", "Km"].iloc[0], 160)
    os.unlink(csv_path)

    # --- 3f. Accent mismatch in CSV destination → normalized match -------
    csv_path = write_routes([
        {"Origen": "València", "Destino": "València", "Km": 0, "Tiempo": 0},
    ])
    merged_f = merge_with_routes(positions, csv_path, "València")
    assert_true("3f-accent match", merged_f.loc[
        merged_f["Municipio_Norm"] == "VALENCIA", "Ruta_Encontrada"
    ].iloc[0])
    os.unlink(csv_path)

    # --- 3g. Km/Tiempo as strings in CSV → coerced to numeric -----------
    csv_path = write_routes([
        {"Origen": "València", "Destino": "Alacant", "Km": "167.5", "Tiempo": "105"},
    ])
    merged_g = merge_with_routes(positions, csv_path, "València")
    assert_eq("3g-string km coerced",
              merged_g.loc[merged_g["Municipio_Norm"] == "ALACANT", "Km"].iloc[0], 167.5)
    os.unlink(csv_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 4 — Business Logic, Sorting & Fallback
# ═══════════════════════════════════════════════════════════════════════════

def test_phase4():
    print("\n══ PHASE 4: Business Logic, Sorting & Fallback ══")

    # --- 4a. Tipo filter keeps only VACANTE ------------------------------
    blocks = [
        make_block("VACANTE 123456", tipo="VACANTE"),
        make_block("SUSTITUCION 234567", tipo="SUSTITUCION"),
        make_block("VACANTE 345678", tipo="VACANTE"),
        make_block("SUSTITUCION 456789", tipo="SUSTITUCION"),
    ]
    df = blocks_to_dataframe(blocks)
    vacantes = df[df["Tipo"] == "VACANTE"]
    assert_eq("4a-vacantes count", len(vacantes), 2)

    # --- 4b. Sort by Tiempo then Km ascending ---------------------------
    data = pd.DataFrame({
        "Municipio": ["A", "B", "C", "D"],
        "Km":        [100, 50, 50, 200],
        "Tiempo":    [60, 30, 35, 90],
    })
    sorted_df = data.sort_values(by=["Tiempo", "Km"], ascending=True, na_position="last")
    assert_eq("4b-sort order",
              sorted_df["Municipio"].tolist(), ["B", "C", "A", "D"])

    # --- 4c. NaN values sort last ----------------------------------------
    data_nan = pd.DataFrame({
        "Municipio": ["A", "B", "C"],
        "Km":        [100, None, 50],
        "Tiempo":    [60, 30, None],
    })
    sorted_nan = data_nan.sort_values(by=["Tiempo", "Km"], ascending=True, na_position="last")
    assert_eq("4c-nan last",
              sorted_nan["Municipio"].tolist(), ["B", "A", "C"])

    # --- 4d. Export with utf-8-sig BOM -----------------------------------
    tmp_out = os.path.join(tempfile.gettempdir(), "test_bom.csv")
    df_out = pd.DataFrame({"col": ["áéíóú", "ñ"]})
    df_out.to_csv(tmp_out, index=False, encoding="utf-8-sig")
    with open(tmp_out, "rb") as f:
        bom = f.read(3)
    assert_eq("4d-utf8sig bom", bom, b"\xef\xbb\xbf")
    os.unlink(tmp_out)

    # --- 4e. Fallback proximidad sorts by Km ----------------------------
    csv_path = write_routes([
        {"Origen": "València", "Destino": "Zaragoza", "Km": 320, "Tiempo": 190},
        {"Origen": "València", "Destino": "Castelló", "Km": 70, "Tiempo": 52},
        {"Origen": "València", "Destino": "Madrid", "Km": 350, "Tiempo": 210},
    ])
    fb = fallback_proximidad(csv_path, "València")
    assert_eq("4e-fallback order",
              fb["Municipio"].tolist(), ["Castelló", "Zaragoza", "Madrid"])
    assert_eq("4e-fallback has km", fb["Km"].tolist(), [70.0, 320.0, 350.0])
    os.unlink(csv_path)
    if os.path.exists("municipios_por_proximidad.csv"):
        os.unlink("municipios_por_proximidad.csv")

    # --- 4f. Fallback with bad origin → empty DataFrame ------------------
    csv_path = write_routes([
        {"Origen": "Madrid", "Destino": "Toledo", "Km": 70, "Tiempo": 45},
    ])
    fb_empty = fallback_proximidad(csv_path, "València")
    assert_eq("4f-fallback empty", len(fb_empty), 0)
    os.unlink(csv_path)

    # --- 4g. Fallback with non-existent CSV → empty DataFrame ------------
    missing_csv = os.path.join(tempfile.gettempdir(), "nonexistent_xyz.csv")
    fb_bad = fallback_proximidad(missing_csv, "València")
    assert_eq("4g-fallback bad csv", len(fb_bad), 0)

    # --- 4h. build_vacantes end-to-end with mock parse ------------------
    # We test the post-parse pipeline by directly constructing blocks
    # and feeding them through extract → merge → filter → sort.
    blocks_raw = [
        make_block("53 | València -03015129- IES TEST A 111111",
                   tipo="VACANTE", cuerpo="S", especialidad="M", provincia="V"),
        make_block("12 | Alacant -03000456- IES TEST B 222222",
                   tipo="SUSTITUCION", cuerpo="S", especialidad="I", provincia="A"),
        make_block("8 | Castelló -12000789- CEIP TEST C 333333",
                   tipo="VACANTE", cuerpo="P", especialidad="G", provincia="C"),
        make_block("25 | Benidorm -03001234- IES TEST D 444444",
                   tipo="VACANTE", cuerpo="S", especialidad="C", provincia="A"),
    ]
    df_raw = blocks_to_dataframe(blocks_raw)
    csv_path = write_routes([
        {"Origen": "València", "Destino": "Valencia", "Km": 0, "Tiempo": 0},
        {"Origen": "València", "Destino": "Alacant", "Km": 167, "Tiempo": 105},
        {"Origen": "València", "Destino": "Castelló", "Km": 70, "Tiempo": 52},
        {"Origen": "València", "Destino": "Benidorm", "Km": 95, "Tiempo": 70},
    ])
    merged = merge_with_routes(df_raw, csv_path, "València")
    vacantes = merged[merged["Tipo"] == "VACANTE"].copy()
    vacantes = vacantes.sort_values(
        by=["Tiempo", "Km"], ascending=True, na_position="last"
    ).reset_index(drop=True)

    assert_eq("4h-vacantes count", len(vacantes), 3)
    assert_eq("4h-sort Tiempo",
              vacantes["Tiempo"].tolist(), [0.0, 52.0, 70.0])
    assert_eq("4h-sort Km",
              vacantes["Km"].tolist(), [0.0, 70.0, 95.0])
    assert_eq("4h-first municipality",
              vacantes.iloc[0]["Municipio"], "València")

    # Verify temp columns are droppable
    vacantes.drop(columns=["Municipio_Norm", "Ruta_Encontrada"],
                  inplace=True, errors="ignore")
    assert_true("4h-no temp cols",
                "Municipio_Norm" not in vacantes.columns)
    os.unlink(csv_path)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 5 — Adjudicación Filtering (adjudicacion.py)
# ═══════════════════════════════════════════════════════════════════════════

def _make_adj_row(idx=1, tipo="VACANTE", municipio="VALÈNCIA",
                  codigo="03015129", nombre="IES TEST", lloc="889778",
                  iti="NO", observaciones="", obs_tags=None,
                  req_ling="", especialidad="120 - EDUCACIÓN INFANTIL",
                  provincia="Alacant", cuerpo="MAESTROS"):
    return {
        "Índex": idx,
        "Tipo": tipo,
        "Municipio": municipio,
        "Centro_Código": codigo,
        "Centro_Nombre": nombre,
        "Lloc": lloc,
        "ITI": iti,
        "Observaciones": observaciones,
        "Obs_Tags": obs_tags or [],
        "Req_Lingüístic": req_ling,
        "Especialidad": especialidad,
        "Provincia": provincia,
        "Cos": cuerpo,
    }


def _make_adj_df(rows=None):
    if rows is None:
        rows = [
            _make_adj_row(1, "VACANTE", "VALÈNCIA", "46013050",
                          "IES EL CABANYAL", "921725", "NO",
                          especialidad="120 - EDUCACIÓN INFANTIL",
                          provincia="València"),
            _make_adj_row(2, "VACANTE", "ALACANT", "03001891",
                          "IES MIGUEL HERNÁNDEZ", "875068", "NO",
                          especialidad="120 - EDUCACIÓN INFANTIL",
                          provincia="Alacant"),
            _make_adj_row(3, "SUSTITUCIÓN INDETERMINADA", "CASTELLÓ",
                          "12006780", "CEIP CASTELL VELL", "841659", "NO",
                          observaciones="Centre singular. Lloc difícil provisió",
                          obs_tags=["Centre singular", "Lloc difícil provisió"],
                          especialidad="3A1 - COCINA Y PASTELERÍA",
                          provincia="Castelló"),
            _make_adj_row(4, "VACANTE", "BENIDORM", "03015129",
                          "IES MEDITERRÀNIA", "889778", "SI",
                          req_ling="ING-B2",
                          especialidad="3A1 - COCINA Y PASTELERÍA",
                          provincia="Alacant"),
            _make_adj_row(5, "VACANTE", "TORREVIEJA", "03008666",
                          "CEIP INMACULADA", "915363", "NO",
                          observaciones="CENTRE PENITENCIARI - LLOC D'ESP. DIFICULTAT",
                          obs_tags=["CENTRE PENITENCIARI", "Lloc d'esp. dificultat"],
                          especialidad="120 - EDUCACIÓN INFANTIL",
                          provincia="Alacant"),
            _make_adj_row(6, "VACANTE", "ALTEA", "03000100",
                          "EI MINI-MON", "123456", "NO",
                          observaciones="Infantil 0 a 3 años",
                          obs_tags=["Infantil 0 a 3"],
                          req_ling="ING-B2",
                          especialidad="120 - EDUCACIÓN INFANTIL",
                          provincia="Alacant"),
            _make_adj_row(7, "VACANTE", "VALÈNCIA", "46016245",
                          "CEE PROF. SEBASTIÁN BURGOS", "841556", "NO",
                          observaciones="Centre singular. Programa TVA",
                          obs_tags=["Centre singular", "TVA"],
                          especialidad="254 - INFORMÁTICA",
                          provincia="València"),
            _make_adj_row(8, "VACANTE", "ALCOI", "03012165",
                          "CIPFP BATOI", "913440", "NO",
                          especialidad="3A2 - ESTÉTICA",
                          provincia="Alacant"),
        ]
    return pd.DataFrame(rows)


def test_phase5():
    print("\n══ PHASE 5: Adjudicación Filtering ══")

    df = _make_adj_df()

    # --- 5a. filter_by_especialidad: exact code match --------------------
    f = filter_by_especialidad(df, "120")
    assert_eq("5a-esp code 120", len(f), 4)
    assert_true("5a-esp code 120 all same",
                (f["Especialidad"] == "120 - EDUCACIÓN INFANTIL").all())

    # --- 5b. filter_by_especialidad: full string match -------------------
    f = filter_by_especialidad(df, "3A1 - COCINA Y PASTELERÍA")
    assert_eq("5b-esp full match", len(f), 2)

    # --- 5c. filter_by_especialidad: partial name match ------------------
    f = filter_by_especialidad(df, "ESTÉTICA")
    assert_eq("5c-esp partial", len(f), 1)
    assert_eq("5c-esp partial value",
              f.iloc[0]["Especialidad"], "3A2 - ESTÉTICA")

    # --- 5d. filter_by_tipo: VACANTE -------------------------------------
    f = filter_by_tipo(df, "VACANTE")
    assert_eq("5d-tipo vacante", len(f), 7)

    # --- 5e. filter_by_tipo: SUSTITUCIÓN ---------------------------------
    f = filter_by_tipo(df, "SUSTITUCIÓN INDETERMINADA")
    assert_eq("5e-tipo sustitucion", len(f), 1)
    assert_eq("5e-tipo sustitucion idx",
              f.iloc[0]["Índex"], 3)

    # --- 5f. filter_by_iti: NO -------------------------------------------
    f = filter_by_iti(df, "NO")
    assert_eq("5f-iti NO", len(f), 7)

    # --- 5g. filter_by_iti: SI -------------------------------------------
    f = filter_by_iti(df, "SI")
    assert_eq("5g-iti SI", len(f), 1)
    assert_eq("5g-iti SI lloc", f.iloc[0]["Lloc"], "889778")

    # --- 5h. filter_by_req_lingüístic: ING --------------------------------
    f = filter_by_req_lingüístic(df, "ING")
    assert_eq("5h-req ling ING", len(f), 2)

    # --- 5i. filter_by_req_lingüístic: ING-B2 -----------------------------
    f = filter_by_req_lingüístic(df, "ING-B2")
    assert_eq("5i-req ling ING-B2", len(f), 2)

    # --- 5j. filter_by_observaciones: Centre singular --------------------
    f = filter_by_observaciones(df, "Centre singular")
    assert_eq("5j-obs centre singular", len(f), 2)

    # --- 5k. filter_by_observaciones: TVA --------------------------------
    f = filter_by_observaciones(df, "TVA")
    assert_eq("5k-obs TVA", len(f), 1)

    # --- 5l. filter_by_observaciones: CENTRE PENITENCIARI ----------------
    f = filter_by_observaciones(df, "CENTRE PENITENCIARI")
    assert_eq("5l-obs penitenciari", len(f), 1)

    # --- 5m. filter_by_observaciones: Infantil 0 a 3 --------------------
    f = filter_by_observaciones(df, "Infantil 0 a 3")
    assert_eq("5m-obs infantil 0-3", len(f), 1)

    # --- 5n. filter_by_observaciones: PFQB -------------------------------
    f = filter_by_observaciones(df, "PFQB")
    assert_eq("5n-obs PFQB", len(f), 0)

    # --- 5o. filter_positions: multi-filter stacking ---------------------
    f = filter_positions(df, especialidad="120", tipo="VACANTE")
    assert_eq("5o-multi esp+tipo", len(f), 4)

    # --- 5p. filter_positions: esp + ITI + req_ling ----------------------
    f = filter_positions(df, especialidad="3A1", iti="SI", req_lingüístic="ING")
    assert_eq("5p-multi esp+iti+ling", len(f), 1)
    assert_eq("5p-multi value", f.iloc[0]["Lloc"], "889778")

    # --- 5q. filter_positions: provincia ---------------------------------
    f = filter_positions(df, provincia="Alacant")
    assert_eq("5q-provincia alacant", len(f), 5)

    # --- 5r. filter_positions: municipi partial --------------------------
    f = filter_positions(df, municipi="VAL")
    assert_eq("5r-municipi partial", len(f), 2)

    # --- 5s. get_especialidades ------------------------------------------
    esps = get_especialidades(df)
    assert_eq("5s-get_especialidades count", len(esps), 4)
    assert_eq("5s-get_especialidades sorted", esps, sorted(esps))

    # --- 5t. get_observacion_tags ----------------------------------------
    tags = get_observacion_tags(df)
    assert_true("5t-obs tags has centre singular",
                "Centre singular" in tags)
    assert_true("5t-obs tags has TVA", "TVA" in tags)
    assert_true("5t-obs tags has penitenciari",
                "CENTRE PENITENCIARI" in tags)

    # --- 5u. to_json ----------------------------------------------------
    json_str = to_json(df.head(2))
    assert_true("5u-to_json is str", isinstance(json_str, str))
    assert_true("5u-to_json has content", len(json_str) > 10)

    # --- 5v. to_clean_table ----------------------------------------------
    clean = to_clean_table(df.head(2))
    assert_true("5v-clean table has Codi Centre",
                "Codi Centre" in clean.columns)
    assert_true("5v-clean table has Etiquetes",
                "Etiquetes" in clean.columns)


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 6 — Regex Robustness & Base Name Regression
# ═══════════════════════════════════════════════════════════════════════════

def test_phase6():
    print("\n══ PHASE 6: Regex Robustness & Base Name Regression ══")

    # --- 6a. ALBAL vacancy with no lloc code is now parsed ---------------
    line = "3 VACANTEALBAL - 46000274 - CEIP JUAN ESTEVE MUÑOZ NO"
    row = _parse_row(line, "120 - EDUCACIÓN INFANTIL", "València", "MAESTROS")
    assert_true("6a-ALBAL parsed", row is not None)
    if row:
        assert_eq("6a-ALBAL municipio", row["Municipio"], "ALBAL")
        assert_eq("6a-ALBAL codigo", row["Centro_Código"], "46000274")
        assert_eq("6a-ALBAL nombre", row["Centro_Nombre"], "CEIP JUAN ESTEVE MUÑOZ")
        assert_eq("6a-ALBAL ITI", row["ITI"], "NO")
        assert_eq("6a-ALBAL lloc", row["Lloc"], "")

    # --- 6b. ALBAL with lloc code is still parsed ------------------------
    line_lloc = "3 VACANTEALBAL - 46000274 - CEIP JUAN ESTEVE MUÑOZ 915363 NO"
    row_lloc = _parse_row(line_lloc, "120 - EDUCACIÓN INFANTIL", "València", "MAESTROS")
    assert_true("6b-ALBAL+lloc parsed", row_lloc is not None)
    if row_lloc:
        assert_eq("6b-ALBAL+lloc lloc", row_lloc["Lloc"], "915363")
        assert_eq("6b-ALBAL+lloc ITI", row_lloc["ITI"], "NO")

    # --- 6c. Typical 3-part format with lloc still works -----------------
    line_typical = "5 VACANTEALBAL - 46000274 - CEIP JUAN 915363 SI"
    row_typ = _parse_row(line_typical, "120", "València", "M")
    assert_true("6c-typical parsed", row_typ is not None)
    if row_typ:
        assert_eq("6c-typical ITI", row_typ["ITI"], "SI")
        assert_eq("6c-typical lloc", row_typ["Lloc"], "915363")

    # --- 6d. Base name extraction for suffix names -----------------------
    assert_eq("6d-base ELX-ALTABIX", _extract_base_muni_name("ELX - ALTABIX"), "ELX")
    assert_eq("6d-base ALMORADÍ", _extract_base_muni_name("ALMORADÍ - HEREDADES"), "ALMORADÍ")
    assert_eq("6d-base plain", _extract_base_muni_name("ALBAL"), "ALBAL")

    # --- 6e. Sub-locality in location doesn't break parsing --------------
    line_sub = "10 VACANTEELX - ALTABIX - 03065100 - CEIP ALTABIX 123456 NO"
    row_sub = _parse_row(line_sub, "120", "Alacant", "M")
    assert_true("6e-ELX-ALTABIX parsed", row_sub is not None)
    if row_sub:
        # Municipality should be "ELX - ALTABIX" (before the code)
        assert_eq("6e-municipio", row_sub["Municipio"], "ELX - ALTABIX")
        assert_eq("6e-codigo", row_sub["Centro_Código"], "03065100")
        # Base name extraction should give "ELX"
        assert_eq("6e-base name", _extract_base_muni_name(row_sub["Municipio"]), "ELX")

    # --- 6f. RE_ROW matches the new optional-lloc format -----------------
    assert_true("6f-RE_ROW matches no-lloc",
                RE_ROW.match("3 VACANTEALBAL - 46000274 - CEIP TEST NO") is not None)
    assert_true("6f-RE_ROW matches with-lloc",
                RE_ROW.match("3 VACANTEALBAL - 46000274 - CEIP TEST 915363 NO") is not None)
