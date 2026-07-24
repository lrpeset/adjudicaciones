"""Regresiones mínimas del parser principal con texto extraído del PDF."""

from pathlib import Path
from unittest.mock import patch

import pytest

from adjudicacion import parse_adjudicacion


FIXTURES = Path(__file__).parent / "tests" / "fixtures"


def _parse_fixture(name: str):
    page_text = (FIXTURES / name).read_text(encoding="utf-8")
    with patch("adjudicacion._extract_text_pages", return_value=[page_text]):
        return parse_adjudicacion("fixture.pdf")


def test_normal_row_fixture():
    result = _parse_fixture("adjudicacion_normal.txt")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["Índex"] == 1
    assert row["Municipio"] == "ALBAL"
    assert row["Centro_Código"] == "46000274"
    assert row["Lloc"] == "915363"


@pytest.mark.xfail(
    strict=True,
    reason="Defecto conocido: el parser no recompone puestos partidos en varias líneas",
)
def test_multiline_row_fixture():
    result = _parse_fixture("adjudicacion_multiline.txt")

    assert len(result) == 1
    row = result.iloc[0]
    assert row["Índex"] == 14136
    assert row["Municipio"] == "UTIEL"
    assert row["Centro_Código"] == "46024217"
