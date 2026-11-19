"""Unit tests for the EODHD-row to upsert-row mapping in the universe rebuild."""

from __future__ import annotations

import pytest

from manta_trading.constants import US_EQUITY_CALENDAR_ID
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


# Real row shapes from EODHD's /exchange-symbol-list (issue #46). fetch_v1_universe
# sets Exchange to INDX on index rows before they reach build_upsert_rows.
_DJT_EQUITY = {
    "Code": "DJT",
    "Name": "Trump Media & Technology Group Corp",
    "Country": "USA",
    "Exchange": "NASDAQ",
    "Currency": "USD",
    "Type": "Common Stock",
}
_DJT_INDEX = {
    "Code": "DJT",
    "Name": "Dow Jones Transportation Average",
    "Country": "USA",
    "Exchange": "INDX",
    "Currency": "USD",
    "Type": "INDEX",
}


def test_an_index_and_an_equity_with_one_code_get_distinct_symbols() -> None:
    """Bars are keyed by symbol, so an index must never take an equity's (#46)."""
    equity, index = build_upsert_rows([_DJT_EQUITY, _DJT_INDEX], {})
    assert (equity["symbol"], equity["canonical_id"]) == ("DJT", "DJT.US")
    assert (index["symbol"], index["canonical_id"]) == ("DJT.INDX", "DJT.INDX")
    assert index["asset_class"] == "index"


def test_an_index_gets_the_equity_calendar() -> None:
    """Without a calendar the daily pass skips the instrument (#4)."""
    equity, index = build_upsert_rows([_DJT_EQUITY, _DJT_INDEX], {})
    assert index["trading_calendar_id"] == US_EQUITY_CALENDAR_ID
    assert equity["trading_calendar_id"] == US_EQUITY_CALENDAR_ID


def test_an_existing_index_row_is_matched_by_its_indx_symbol() -> None:
    """The equity DJT in the registry must not capture the index row."""
    existing = {
        "DJT": {"canonical_id": "DJT.US", "venue": "US", "trading_calendar_id": "NYSE"},
        "DJT.INDX": {
            "canonical_id": "DJT.INDX",
            "venue": "INDX",
            "trading_calendar_id": "NYSE",
        },
    }
    (index,) = build_upsert_rows([_DJT_INDEX], existing)
    assert index["canonical_id"] == "DJT.INDX"
    assert index["symbol"] == "DJT.INDX"
