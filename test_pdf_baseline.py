"""Baseline de integración contra el PDF oficial completo."""

import json
from pathlib import Path

import pytest

from scripts.generate_pdf_baseline import (
    DEFAULT_OUTPUT_DIR,
    EXPECTED_PATH,
    assert_expected,
    generate_baseline,
)


@pytest.mark.full_pdf
def test_official_pdf_matches_versioned_baseline():
    summary = generate_baseline(DEFAULT_OUTPUT_DIR)

    assert_expected(summary)
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    assert summary == expected
    assert (DEFAULT_OUTPUT_DIR / "missing_indices.csv").is_file()
    assert (DEFAULT_OUTPUT_DIR / "unresolved_coordinates.csv").is_file()
