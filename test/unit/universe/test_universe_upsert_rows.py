"""Unit tests for the EODHD-row to upsert-row mapping in the universe rebuild."""

from __future__ import annotations

import pytest

from manta_trading.data.universe.rebuild import build_upsert_rows

# A real row shape from EODHD's /exchange-symbol-list (fetched 2026-11-01).
_EODHD_ROW = {
    "Code": "AAPL",
    "Name": "Apple Inc",
    "Country": "USA",
    "Exchange": "NASDAQ",
    "Currency": "USD",
    "Type": "Common Stock",
    "Isin": "US0378331005",
}


def test_name_is_carried_from_the_eodhd_row() -> None:
    (row,) = build_upsert_rows([_EODHD_ROW], {})
    assert row["name"] == "Apple Inc"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_missing_or_blank_name_is_stored_as_null(raw: str | None) -> None:
    """No invented placeholder: an unnamed row stores NULL."""
    (row,) = build_upsert_rows([{**_EODHD_ROW, "Name": raw}], {})
    assert row["name"] is None


def test_name_is_trimmed() -> None:
    (row,) = build_upsert_rows([{**_EODHD_ROW, "Name": "  Apple Inc "}], {})
    assert row["name"] == "Apple Inc"
