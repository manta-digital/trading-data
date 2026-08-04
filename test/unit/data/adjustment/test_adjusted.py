"""Unit tests for manta_trading.data.adjustment.adjusted()."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from manta_trading.data.adjustment import adjusted
from manta_trading.data.adjustment._adjusted import _load_snapshot
from manta_trading.data.adjustment.k_factor import (
    CaSnapshot,
    Dividend,
    Split,
    compute_snapshot_id,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_df(dates: list[date]) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame with UTC DatetimeIndex."""
    timestamps = [
        datetime(d.year, d.month, d.day, 14, 30, tzinfo=UTC)
        for d in dates
    ]
    return pd.DataFrame(
        {
            "open":   [100.0] * len(dates),
            "high":   [110.0] * len(dates),
            "low":    [90.0]  * len(dates),
            "close":  [105.0] * len(dates),
            "volume": [1_000_000] * len(dates),
        },
        index=pd.DatetimeIndex(timestamps, tz="UTC"),
    )


def _snapshot_4for1(symbol: str = "AAPL") -> CaSnapshot:
    """CaSnapshot with one 4-for-1 split on 2020-08-31."""
    splits = (
        Split(
            symbol=symbol,
            ex_date=date(2020, 8, 31),
            ratio_to=Decimal("4"),
            ratio_from=Decimal("1"),
        ),
    )
    return CaSnapshot(
        symbol=symbol,
        splits=splits,
        dividends=(),
        prev_closes={},
        snapshot_id=compute_snapshot_id(splits, ()),
    )


def _snapshot_empty(symbol: str = "AAPL") -> CaSnapshot:
    return CaSnapshot(
        symbol=symbol,
        splits=(),
        dividends=(),
        prev_closes={},
        snapshot_id=compute_snapshot_id((), ()),
    )


def _snapshot_dividend_missing_prev_close(symbol: str = "AAPL") -> CaSnapshot:
    dividends = (
        Dividend(symbol=symbol, ex_date=date(2020, 5, 15), amount=Decimal("0.82")),
    )
    return CaSnapshot(
        symbol=symbol,
        splits=(),
        dividends=dividends,
        prev_closes={},  # missing prev_close for ex_date
        snapshot_id=compute_snapshot_id((), dividends),
    )


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

def test_split_adjusts_pre_exdate_bars() -> None:
    """Bars before ex_date are divided by 4; bars on/after are unchanged."""
    dates = [date(2020, 8, 28), date(2020, 8, 31), date(2020, 9, 1)]
    df = _make_df(dates)
    snap = _snapshot_4for1()
    conn = MagicMock()

    result = adjusted(df, "AAPL", conn, ca_snapshot=snap)

    # Bar on 2020-08-28 (before ex_date 2020-08-31): k = 1/4
    assert result["close"].iloc[0] == pytest.approx(105.0 / 4)
    assert result["open"].iloc[0]  == pytest.approx(100.0 / 4)
    assert result["high"].iloc[0]  == pytest.approx(110.0 / 4)
    assert result["low"].iloc[0]   == pytest.approx(90.0 / 4)

    # Bars on/after ex_date: k = 1 (no adjustment)
    assert result["close"].iloc[1] == pytest.approx(105.0)
    assert result["close"].iloc[2] == pytest.approx(105.0)


def test_split_volume_unchanged() -> None:
    dates = [date(2020, 8, 28)]
    df = _make_df(dates)
    snap = _snapshot_4for1()
    conn = MagicMock()

    result = adjusted(df, "AAPL", conn, ca_snapshot=snap)

    assert result["volume"].iloc[0] == 1_000_000


def test_no_cas_returns_same_object() -> None:
    dates = [date(2020, 8, 28)]
    df = _make_df(dates)
    snap = _snapshot_empty()
    conn = MagicMock()

    result = adjusted(df, "AAPL", conn, ca_snapshot=snap)

    assert result is df


def test_missing_prev_close_raises_keyerror() -> None:
    dates = [date(2020, 5, 14)]
    df = _make_df(dates)
    snap = _snapshot_dividend_missing_prev_close()
    conn = MagicMock()

    with pytest.raises(KeyError):
        adjusted(df, "AAPL", conn, ca_snapshot=snap)


def test_provided_snapshot_skips_load_snapshot() -> None:
    """When ca_snapshot is provided, _load_snapshot must not be called."""
    dates = [date(2020, 8, 28)]
    df = _make_df(dates)
    snap = _snapshot_empty()
    conn = MagicMock()

    with patch(
        "manta_trading.data.adjustment._adjusted._load_snapshot"
    ) as mock_load:
        adjusted(df, "AAPL", conn, ca_snapshot=snap)
        mock_load.assert_not_called()


def test_empty_df_returns_immediately() -> None:
    """Empty DataFrame is returned without any DB calls."""
    df = pd.DataFrame()
    conn = MagicMock()

    with patch(
        "manta_trading.data.adjustment._adjusted._load_snapshot"
    ) as mock_load:
        result = adjusted(df, "AAPL", conn)
        mock_load.assert_not_called()
        assert result is df


def test_does_not_mutate_input() -> None:
    dates = [date(2020, 8, 28)]
    df = _make_df(dates)
    original_close = df["close"].iloc[0]
    snap = _snapshot_4for1()
    conn = MagicMock()

    result = adjusted(df, "AAPL", conn, ca_snapshot=snap)

    assert result is not df
    assert df["close"].iloc[0] == original_close


# ---------------------------------------------------------------------------
# _load_snapshot: window filter and query count (slice 186)
# ---------------------------------------------------------------------------


def _snapshot_cursor(
    splits_rows: list[tuple] | None = None,
    dividend_rows: list[tuple] | None = None,
    prev_close_rows: list[tuple] | None = None,
) -> tuple[MagicMock, MagicMock]:
    """A connection whose cursor answers the loader's queries in order."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = [
        splits_rows or [],
        dividend_rows or [],
        prev_close_rows or [],
    ]
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    return conn, cursor


def test_load_snapshot_filters_actions_to_the_window() -> None:
    """Only actions after the earliest bar date can change a bar in the frame.

    Without this bound a three-month request loaded a symbol's whole dividend
    history — ~94 rows for AAPL, going back to the 1980s.
    """
    conn, cursor = _snapshot_cursor()
    _load_snapshot("AAPL", date(2024, 3, 1), conn)

    splits_sql, splits_params = cursor.execute.call_args_list[0].args
    dividends_sql, dividend_params = cursor.execute.call_args_list[1].args
    assert "ex_date > %s" in splits_sql
    assert "ex_date > %s" in dividends_sql
    assert splits_params == ("AAPL", date(2024, 3, 1))
    assert dividend_params == ("AAPL", date(2024, 3, 1))


def test_load_snapshot_has_no_upper_bound_on_ex_date() -> None:
    """Actions *after* the window rebase old prices onto the current basis;
    excluding them would silently return unadjusted values."""
    conn, cursor = _snapshot_cursor()
    _load_snapshot("AAPL", date(2024, 3, 1), conn)

    for call in cursor.execute.call_args_list[:2]:
        assert "ex_date <" not in call.args[0]


def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def test_prev_closes_are_fetched_in_one_statement() -> None:
    """The loop this replaces issued one query per dividend, and each query
    cost ~1.8s in *planning* against a thousands-of-chunks hypertable — ~92s
    for AAPL. One statement, one plan.
    """
    dividend_rows = [
        ("AAPL", date(2024, 5, 10), Decimal("0.25"), "USD"),
        ("AAPL", date(2024, 8, 12), Decimal("0.25"), "USD"),
    ]
    close_rows = [
        (_ts(date(2024, 5, 8)), Decimal("182.40")),
        (_ts(date(2024, 5, 9)), Decimal("184.57")),
        (_ts(date(2024, 8, 9)), Decimal("216.24")),
    ]
    conn, cursor = _snapshot_cursor(
        dividend_rows=dividend_rows, prev_close_rows=close_rows
    )
    snapshot = _load_snapshot("AAPL", date(2024, 3, 1), conn)

    # splits + dividends + one closes query = 3, not 2 + len(dividends)
    assert cursor.execute.call_count == 3
    closes_sql, closes_params = cursor.execute.call_args_list[2].args
    assert "ORDER BY time" in closes_sql
    assert closes_params == ("AAPL", date(2024, 8, 12))
    # The close on the most recent trading day strictly before each ex-date.
    assert snapshot.prev_closes == {
        date(2024, 5, 10): Decimal("184.57"),
        date(2024, 8, 12): Decimal("216.24"),
    }


def test_ex_date_before_all_closes_is_omitted_not_guessed() -> None:
    """``adjusted`` raises KeyError on a missing prev_close; inventing one
    would adjust by a wrong factor and never be noticed."""
    dividend_rows = [("AAPL", date(1990, 1, 5), Decimal("0.10"), "USD")]
    close_rows = [(_ts(date(2024, 5, 8)), Decimal("182.40"))]
    conn, _cursor = _snapshot_cursor(
        dividend_rows=dividend_rows, prev_close_rows=close_rows
    )
    snapshot = _load_snapshot("AAPL", date(1989, 1, 1), conn)
    assert snapshot.prev_closes == {}


def test_no_prev_close_query_when_there_are_no_dividends() -> None:
    conn, cursor = _snapshot_cursor()
    snapshot = _load_snapshot("AAPL", date(2024, 3, 1), conn)
    assert cursor.execute.call_count == 2
    assert snapshot.prev_closes == {}


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

_DB_URL = os.getenv("MT_TIMESCALE_DB_URL")


@pytest.mark.skipif(not _DB_URL, reason="MT_TIMESCALE_DB_URL not set")
def test_aapl_split_integration() -> None:
    """AAPL 4-for-1 split on 2020-08-31: adjusted close on 2020-08-28 ≈ raw / 4."""

    from manta_trading.constants import Granularity
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
        # Post-split raw and adjusted should be close (only dividend adjustments apply)
        assert adj_close_post == pytest.approx(raw_close_post, rel=0.10)
    finally:
        db.close()
