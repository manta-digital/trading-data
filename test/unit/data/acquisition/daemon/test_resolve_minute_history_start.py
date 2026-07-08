"""Unit tests for _resolve_minute_history_start (P08).

The resolver computes the earliest UTC datetime to fetch 1-minute bars
for one symbol as:
    max(EODHD_INTRADAY_HORIZON,
        operator_floor (settings.minute_history_start),
        instruments.first_listing_date or instruments.first_data_date).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import EODHD_INTRADAY_HORIZON
from manta_trading.data.acquisition.daemon.minute import (
    _resolve_minute_history_start,
)

UTC = timezone.utc


def _make_conn(row: tuple | None) -> MagicMock:
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchone = MagicMock(return_value=row)
    cur.execute = MagicMock()
    conn = MagicMock()
    conn.cursor.return_value = cur
    return conn


def test_no_operator_no_per_symbol_falls_back_to_horizon() -> None:
    conn = _make_conn(row=(None, None))
    out = _resolve_minute_history_start(conn, "AAPL", operator_floor=None)
    assert out == datetime(
        EODHD_INTRADAY_HORIZON.year,
        EODHD_INTRADAY_HORIZON.month,
        EODHD_INTRADAY_HORIZON.day,
        tzinfo=UTC,
    )


def test_per_symbol_first_listing_date_wins_when_above_horizon() -> None:
    listing = date(2018, 6, 1)
    conn = _make_conn(row=(listing, None))
    out = _resolve_minute_history_start(conn, "NEW", operator_floor=None)
    assert out.date() == listing


def test_per_symbol_below_horizon_clamped_to_horizon() -> None:
    # Symbol listed in 1986; horizon is 2004-01-01.
    listing = date(1986, 3, 13)
    conn = _make_conn(row=(listing, None))
    out = _resolve_minute_history_start(conn, "MSFT", operator_floor=None)
    assert out.date() == EODHD_INTRADAY_HORIZON


def test_first_data_date_used_when_listing_date_null() -> None:
    fd = date(2012, 9, 1)
    conn = _make_conn(row=(None, fd))
    out = _resolve_minute_history_start(conn, "X", operator_floor=None)
    assert out.date() == fd


def test_listing_date_preferred_over_first_data_date() -> None:
    listing = date(2010, 5, 5)
    fd = date(2008, 1, 1)
    conn = _make_conn(row=(listing, fd))
    out = _resolve_minute_history_start(conn, "X", operator_floor=None)
    assert out.date() == listing


def test_operator_floor_above_per_symbol_wins() -> None:
    listing = date(2010, 1, 1)
    operator = date(2024, 1, 1)
    conn = _make_conn(row=(listing, None))
    out = _resolve_minute_history_start(conn, "X", operator_floor=operator)
    assert out.date() == operator


def test_per_symbol_above_operator_floor_wins() -> None:
    listing = date(2024, 6, 1)
    operator = date(2010, 1, 1)
    conn = _make_conn(row=(listing, None))
    out = _resolve_minute_history_start(conn, "X", operator_floor=operator)
    assert out.date() == listing


def test_no_instruments_row_falls_back_to_horizon_or_operator() -> None:
    conn = _make_conn(row=None)
    out_default = _resolve_minute_history_start(conn, "GHOST", operator_floor=None)
    assert out_default.date() == EODHD_INTRADAY_HORIZON

    operator = date(2020, 1, 1)
    out_with_op = _resolve_minute_history_start(conn, "GHOST", operator_floor=operator)
    assert out_with_op.date() == operator


def test_returned_datetime_is_utc_midnight() -> None:
    conn = _make_conn(row=(date(2015, 7, 4), None))
    out = _resolve_minute_history_start(conn, "X", operator_floor=None)
    assert out.tzinfo is UTC
    assert (out.hour, out.minute, out.second, out.microsecond) == (0, 0, 0, 0)
