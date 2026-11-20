"""Extended session bounds from ``populate_trading_sessions`` (slice 174 task 3.2)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pytest

from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.market.schema.nyse_calendar_history import us_equity_holidays
from manta_trading.market.schema.seed_cme_calendar import (
    CME_EQUITY_CALENDAR,
    CME_EQUITY_CALENDAR_ID,
)

NYSE_ROW: dict[str, Any] = {
    "timezone": "America/New_York",
    "market_open": time(9, 30),
    "market_close": time(16, 0),
    "has_extended_hours": True,
    "extended_open": time(4, 0),
    "extended_close": time(20, 0),
}
HOLIDAYS = us_equity_holidays("NYSE")


def _session(day: date, calendars_row: dict[str, Any] = NYSE_ROW) -> dict[str, Any]:
    rows = populate_trading_sessions("NYSE", day, day, calendars_row, HOLIDAYS)
    assert len(rows) == 1
    return rows[0]


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@pytest.mark.parametrize(
    ("day", "ext_open", "ext_close"),
    [
        # Winter (EST): 04:00 = 09:00Z, 20:00 = 01:00Z next day.
        (date(2026, 1, 14), _utc(2026, 1, 14, 9), _utc(2026, 1, 15, 1)),
        # Summer (EDT): 04:00 = 08:00Z, 20:00 = 00:00Z next day.
        (date(2026, 7, 15), _utc(2026, 7, 15, 8), _utc(2026, 7, 16, 0)),
        # Early close (winter): late session to 17:00 EST = 22:00Z.
        (date(2025, 11, 28), _utc(2025, 11, 28, 9), _utc(2025, 11, 28, 22)),
        # DST ends 2025-11-02: Friday before EDT, Monday after EST.
        (date(2025, 10, 31), _utc(2025, 10, 31, 8), _utc(2025, 11, 1, 0)),
        (date(2025, 11, 3), _utc(2025, 11, 3, 9), _utc(2025, 11, 4, 1)),
        # DST starts 2026-03-08: Friday before EST, Monday after EDT.
        (date(2026, 3, 6), _utc(2026, 3, 6, 9), _utc(2026, 3, 7, 1)),
        (date(2026, 3, 9), _utc(2026, 3, 9, 8), _utc(2026, 3, 10, 0)),
    ],
)
def test_extended_bounds(day: date, ext_open: datetime, ext_close: datetime) -> None:
    row = _session(day)
    assert row["extended_open_utc"] == ext_open
    assert row["extended_close_utc"] == ext_close


def test_early_close_keeps_regular_close_at_13() -> None:
    row = _session(date(2025, 11, 28))
    assert row["session_close_utc"] == _utc(2025, 11, 28, 18)


def test_cme_calendar_has_no_extended_bounds() -> None:
    rows = populate_trading_sessions(
        CME_EQUITY_CALENDAR_ID,
        date(2026, 1, 13),
        date(2026, 1, 13),
        CME_EQUITY_CALENDAR,
        [],
    )
    assert rows[0]["extended_open_utc"] is None
    assert rows[0]["extended_close_utc"] is None


def test_extended_calendar_missing_time_raises() -> None:
    broken = {**NYSE_ROW, "extended_close": None}
    with pytest.raises(ValueError, match="NYSE"):
        _session(date(2026, 1, 14), broken)


def test_without_flag_rows_have_no_extended_keys() -> None:
    legacy = {k: NYSE_ROW[k] for k in ("timezone", "market_open", "market_close")}
    row = _session(date(2026, 1, 14), legacy)
    assert "extended_open_utc" not in row
    assert "extended_close_utc" not in row
