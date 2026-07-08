"""Unit and integration tests for TimescaleDailyDataDB."""

from __future__ import annotations

import os
from datetime import date

import pytest

from manta_trading.constants import GRANULARITY_SOURCE, Granularity


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------

def test_minute_grains_raise_value_error() -> None:
    """ValueError for every minute-grain token."""
    from unittest.mock import MagicMock, patch
    from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB

    with patch.object(TimescaleDailyDataDB, "_init_pool"):
        db = TimescaleDailyDataDB.__new__(TimescaleDailyDataDB)
        db._pool = MagicMock()

    minute_grains = [Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4]
    for grain in minute_grains:
        with pytest.raises(ValueError, match="minute-grain"):
            db.get_daily_data("AAPL", date(2024, 1, 1), date(2024, 1, 31), grain)


@pytest.mark.parametrize("member,expected_source", [
    (Granularity.D1,  "daily_ohlcv"),
    (Granularity.W1,  "daily_weekly_ohlcv"),
    (Granularity.MO1, "daily_monthly_ohlcv"),
    (Granularity.Q1,  "daily_quarterly_ohlcv"),
])
def test_granularity_source_routing(member: Granularity, expected_source: str) -> None:
    assert GRANULARITY_SOURCE[member] == expected_source


# ---------------------------------------------------------------------------
# Integration tests — require MT_TIMESCALE_DB_URL + AAPL data
# ---------------------------------------------------------------------------

_DB_URL = os.getenv("MT_TIMESCALE_DB_URL")


@pytest.mark.skipif(not _DB_URL, reason="MT_TIMESCALE_DB_URL not set")
def test_d1_raw_returns_dataframe() -> None:
    from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB

    db = TimescaleDailyDataDB(_DB_URL)  # type: ignore[arg-type]
    try:
        df = db.get_daily_data(
            "AAPL", date(2020, 1, 1), date(2020, 12, 31), Granularity.D1, adjusted=False
        )
        assert not df.empty
        assert set(["open", "high", "low", "close", "volume"]).issubset(df.columns)
    finally:
        db.close()


@pytest.mark.skipif(not _DB_URL, reason="MT_TIMESCALE_DB_URL not set")
def test_w1_raw_returns_dataframe() -> None:
    from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB

    db = TimescaleDailyDataDB(_DB_URL)  # type: ignore[arg-type]
    try:
        df = db.get_daily_data(
            "AAPL", date(2020, 1, 1), date(2020, 12, 31), Granularity.W1, adjusted=False
        )
        assert not df.empty
        assert set(["open", "high", "low", "close", "volume"]).issubset(df.columns)
    finally:
        db.close()


@pytest.mark.skipif(not _DB_URL, reason="MT_TIMESCALE_DB_URL not set")
def test_aapl_split_raw_vs_adjusted() -> None:
    """AAPL 4-for-1 split 2020-08-31: adjusted close on 2020-08-28 ≈ raw / 4."""
    from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB

    db = TimescaleDailyDataDB(_DB_URL)  # type: ignore[arg-type]
    try:
        raw = db.get_daily_data(
            "AAPL", date(2020, 8, 25), date(2020, 9, 4), Granularity.D1, adjusted=False
        )
        adj = db.get_daily_data(
            "AAPL", date(2020, 8, 25), date(2020, 9, 4), Granularity.D1, adjusted=True
        )
        raw_close_pre = float(raw.loc["2020-08-28", "close"])
        raw_close_post = float(raw.loc["2020-08-31", "close"])
        adj_close_pre = float(adj.loc["2020-08-28", "close"])
        adj_close_post = float(adj.loc["2020-08-31", "close"])
        # Adjusted < raw (k-factor < 1 from split + dividends)
        assert adj_close_pre < raw_close_pre
        assert adj_close_post < raw_close_post
        # Pre-split adjusted ≈ raw/4 within 10% (dividends account for the rest)
        assert adj_close_pre == pytest.approx(raw_close_pre / 4, rel=0.10)
        # Post-split raw and adjusted should be close (only dividend adjustments)
        assert adj_close_post == pytest.approx(raw_close_post, rel=0.10)
    finally:
        db.close()
