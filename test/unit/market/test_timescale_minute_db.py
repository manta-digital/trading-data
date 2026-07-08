"""Tests for TimescaleMinuteDataDB slice-153 additions: key rename + adjusted kwarg."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

from manta_trading.constants import Granularity
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


def _make_db() -> TimescaleMinuteDataDB:
    with patch("manta_trading.market.timescale_minute_db.ConnectionPool"):
        db = TimescaleMinuteDataDB(conninfo="postgresql://host/db")
    db._pool = MagicMock()
    return db


def _mock_pool_returning_rows(db: TimescaleMinuteDataDB, rows: list) -> None:  # type: ignore[type-arg]
    conn_cm = MagicMock()
    cur_cm = MagicMock()
    cur_cm.__enter__ = MagicMock(return_value=cur_cm)
    cur_cm.__exit__ = MagicMock(return_value=False)
    cur_cm.fetchall.return_value = rows
    conn_cm.__enter__ = MagicMock(return_value=conn_cm)
    conn_cm.__exit__ = MagicMock(return_value=False)
    conn_cm.cursor.return_value = cur_cm
    db._pool.connection.return_value = conn_cm


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------

def test_aggregation_views_canonical_keys() -> None:
    """AGGREGATION_VIEWS must use canonical Granularity string values."""
    expected_keys = {Granularity.M5.value, Granularity.M15.value, Granularity.H1.value, Granularity.H4.value}
    assert set(TimescaleMinuteDataDB.AGGREGATION_VIEWS.keys()) == expected_keys


def test_aggregation_views_no_old_keys() -> None:
    old_keys = {"5min", "15min", "1hour", "4hour"}
    for key in old_keys:
        assert key not in TimescaleMinuteDataDB.AGGREGATION_VIEWS


def test_adjusted_false_skips_adjusted_fn() -> None:
    """adjusted=False must return raw DataFrame without calling adjusted_fn."""
    db = _make_db()
    _mock_pool_returning_rows(db, [])  # empty result

    with patch("manta_trading.market.timescale_minute_db.adjusted_fn") as mock_adj:
        db.get_minute_data(
            "AAPL",
            datetime(2020, 8, 28, 9, 30, tzinfo=timezone.utc),
            datetime(2020, 8, 28, 16, 0, tzinfo=timezone.utc),
            adjusted=False,
        )
        mock_adj.assert_not_called()


def test_adjusted_true_calls_adjusted_fn_when_nonempty() -> None:
    """adjusted=True must call adjusted_fn when the DataFrame is non-empty."""
    db = _make_db()
    ts = datetime(2020, 8, 28, 14, 30, tzinfo=timezone.utc)
    _mock_pool_returning_rows(db, [(ts, 100.0, 110.0, 90.0, 105.0, 1_000_000)])

    sentinel_df = pd.DataFrame({"close": [99.9]})
    with patch(
        "manta_trading.market.timescale_minute_db.adjusted_fn",
        return_value=sentinel_df,
    ) as mock_adj:
        result = db.get_minute_data(
            "AAPL",
            datetime(2020, 8, 28, 9, 30, tzinfo=timezone.utc),
            datetime(2020, 8, 28, 16, 0, tzinfo=timezone.utc),
            adjusted=True,
        )
        assert mock_adj.call_count == 1
        assert result is sentinel_df


# ---------------------------------------------------------------------------
# Integration test — requires MT_TIMESCALE_DB_URL + AAPL minute data
# ---------------------------------------------------------------------------

_DB_URL = os.getenv("MT_TIMESCALE_DB_URL")


@pytest.mark.skipif(not _DB_URL, reason="MT_TIMESCALE_DB_URL not set")
@pytest.mark.xfail(
    reason=(
        "Test rot — expectation has a built-in expiration date the author "
        "did not account for. The system applies BOTH splits and dividends "
        "when adjusted=True (standard total-return convention). The 4-for-1 "
        "AAPL split on 2020-08-31 contributes the 4.0 factor, but every "
        "AAPL dividend ex-date since 2020-08-28 multiplies the adjustment "
        "further. As of 2026-05 the actual ratio is ~4.12 (split alone × "
        "cumulative dividend factor) and grows with each new dividend. "
        "Proper fix: compute the expected ratio dynamically from the "
        "splits + dividends tables, OR pin the test to a fixed historical "
        "snapshot. Surfaced during slice 156 cold-start verification "
        "2026-05-09; deferred (separate concern from cold-start integrity)."
    ),
    strict=False,
)
def test_aapl_minute_split_adjusted() -> None:
    """AAPL split 2020-08-31: close on 2020-08-28 should differ ~4x from adjusted.

    NOTE: marked xfail — see decorator. The naive ~4x assertion has been
    invalid since cumulative AAPL dividends pushed the ratio past the ±0.1
    tolerance (early 2023-ish). Do NOT silence this by widening the
    tolerance; it would mask the actual concern, which is whether
    adjustment math is correct. Replace with a dynamic computation
    against splits + dividends when this test is revisited.
    """
    db = TimescaleMinuteDataDB(_DB_URL)  # type: ignore[arg-type]
    try:
        start = datetime(2020, 8, 28, 9, 30, tzinfo=timezone.utc)
        end   = datetime(2020, 8, 28, 16, 0, tzinfo=timezone.utc)

        raw_m = db.get_minute_data("AAPL", start, end, adjusted=False)
        adj_m = db.get_minute_data("AAPL", start, end, adjusted=True)

        assert not raw_m.empty and not adj_m.empty
        raw_close = float(raw_m["close"].iloc[0])
        adj_close = float(adj_m["close"].iloc[0])
        assert raw_close / adj_close == pytest.approx(4.0, abs=0.1)
    finally:
        db.close()
