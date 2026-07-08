"""Unit tests for eodhd_classification module."""

from __future__ import annotations

import pytest

from manta_trading.data.universe.eodhd_classification import (
    EodhdType,
    filter_v1_universe,
)


def _row(type_: str, code: str = "TEST", delisted: bool = False) -> dict:
    return {"Code": code, "Type": type_, "Exchange": "US", "_delisted": delisted}


class TestEodhdType:
    def test_all_three_values(self):
        assert EodhdType.COMMON_STOCK == "Common Stock"
        assert EodhdType.ETF == "ETF"
        assert EodhdType.INDEX == "INDEX"

    def test_str_comparison(self):
        assert EodhdType.COMMON_STOCK == "Common Stock"


class TestFilterV1Universe:
    def test_mutual_fund_removed(self):
        rows = [_row("Mutual Fund", "MF1"), _row("Common Stock", "CS1")]
        result = filter_v1_universe(rows)
        codes = {r["Code"] for r in result}
        assert "MF1" not in codes
        assert "CS1" in codes

    def test_all_three_equity_types_pass_through(self):
        rows = [
            _row("Common Stock", "CS1"),
            _row("ETF", "ETF1"),
            _row("INDEX", "IDX1"),
        ]
        result = filter_v1_universe(rows)
        assert len(result) == 3

    def test_preferred_stock_filtered(self):
        result = filter_v1_universe([
            {"Code": "PS1", "Type": "Preferred Stock", "Exchange": "US", "_delisted": False}
        ])
        assert result == []

    def test_delisted_flag_propagated(self):
        rows = [_row("Common Stock", "DL1", delisted=True)]
        result = filter_v1_universe(rows)
        assert result[0]["delisted_at_eodhd"] is True

    def test_not_delisted_flag(self):
        rows = [_row("ETF", "ETF1", delisted=False)]
        result = filter_v1_universe(rows)
        assert result[0]["delisted_at_eodhd"] is False

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="empty"):
            filter_v1_universe([])

    def test_unknown_type_filtered(self):
        rows = [_row("Bond"), _row("Warrant"), _row("ETF", "ETF1")]
        result = filter_v1_universe(rows)
        assert len(result) == 1
        assert result[0]["Code"] == "ETF1"

    @pytest.mark.parametrize("type_", list(EodhdType))
    def test_parametrized_kept_types(self, type_: EodhdType):
        rows = [_row(type_.value, "SYM")]
        result = filter_v1_universe(rows)
        assert len(result) == 1

    @pytest.mark.parametrize("excluded_exchange", [
        "PINK", "OTCQX", "OTCQB", "OTCGREY", "OTCCE",
        "OTCMKTS", "OTCBB", "OTC", "NMFQS",
    ])
    def test_otc_tiers_filtered_out(self, excluded_exchange: str):
        """OTC and OTC-tier rows are dropped regardless of Type."""
        row = {"Code": "FOO", "Type": "Common Stock",
               "Exchange": excluded_exchange, "_delisted": False}
        result = filter_v1_universe([row])
        assert result == []

    def test_nasdaq_kept_after_otc_filter(self):
        """Authoritative venues survive the OTC exclusion."""
        rows = [
            {"Code": "AAPL", "Type": "Common Stock", "Exchange": "NASDAQ", "_delisted": False},
            {"Code": "FOO",  "Type": "Common Stock", "Exchange": "PINK",   "_delisted": False},
        ]
        result = filter_v1_universe(rows)
        assert len(result) == 1
        assert result[0]["Code"] == "AAPL"
