"""Unit tests for era segmentation in session extension (301 D1, D3). No DB."""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from manta_trading.data.base.session_eras import (
    Era,
    NoHoursEraError,
    Segment,
    plan_segments,
    with_sunday_opens,
)
from manta_trading.data.base.session_population import populate_trading_sessions
from manta_trading.data.base.trading_calendar import MarketStatus
from manta_trading.market.schema.seed_cme_calendar import early_close_day

_CT = ZoneInfo("America/Chicago")
_CAL = "CME_EQUITY"
_EARLY = Era(date(2010, 6, 6), time(15, 30), time(15, 15), time(17, 0))
_MIDDLE = Era(date(2012, 11, 19), time(17, 0), time(16, 15), time(17, 0))
_LATEST = Era(date(2015, 9, 21), time(17, 0), time(16, 0), time(17, 0))
_ERAS = [_EARLY, _MIDDLE, _LATEST]


def _sessions(
    start: date, end: date, holidays: list[dict[str, Any]] | None = None
) -> dict[date, tuple[datetime, datetime]]:
    """Populate through the planned segments, as extension does."""
    rows: list[dict[str, Any]] = []
    for segment in plan_segments(_CAL, start, end, _ERAS):
        era_row = {
            "timezone": "America/Chicago",
            "market_open": segment.era.market_open,
            "market_close": segment.era.market_close,
        }
        rows += populate_trading_sessions(
            _CAL, segment.start, segment.end, era_row,
            with_sunday_opens(holidays or [], segment),
        )  # fmt: skip
    return {
        r["session_date"]: (
            r["session_open_utc"].astimezone(_CT),
            r["session_close_utc"].astimezone(_CT),
        )
        for r in rows
    }


def _ct(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=_CT)


def test_a_range_before_the_first_era_names_calendar_and_date() -> None:
    with pytest.raises(NoHoursEraError) as raised:
        plan_segments(_CAL, date(2010, 6, 5), date(2010, 6, 30), _ERAS)
    assert _CAL in str(raised.value)
    assert "2010-06-05" in str(raised.value)


def test_a_dated_calendar_with_no_eras_raises() -> None:
    with pytest.raises(NoHoursEraError, match="no eras"):
        plan_segments(_CAL, date(2015, 1, 5), date(2015, 1, 9), [])


def test_a_range_starting_on_an_eras_first_date_is_one_segment() -> None:
    segments = plan_segments(_CAL, date(2012, 11, 19), date(2012, 11, 23), _ERAS)
    assert segments == [Segment(date(2012, 11, 19), date(2012, 11, 23), _MIDDLE)]


def test_a_range_across_a_change_splits_at_the_boundary() -> None:
    segments = plan_segments(_CAL, date(2012, 11, 12), date(2015, 9, 25), _ERAS)
    assert segments == [
        Segment(date(2012, 11, 12), date(2012, 11, 18), _EARLY),
        Segment(date(2012, 11, 19), date(2015, 9, 20), _MIDDLE),
        Segment(date(2015, 9, 21), date(2015, 9, 25), _LATEST),
    ]


def test_sessions_carry_their_eras_hours_across_a_change() -> None:
    sessions = _sessions(date(2012, 11, 12), date(2012, 11, 20))
    # Early era: 15:30 previous day to 15:15, Monday opening Sunday 17:00.
    assert sessions[date(2012, 11, 12)] == (
        _ct(date(2012, 11, 11), 17), _ct(date(2012, 11, 12), 15, 15),
    )  # fmt: skip
    assert sessions[date(2012, 11, 16)] == (
        _ct(date(2012, 11, 15), 15, 30), _ct(date(2012, 11, 16), 15, 15),
    )  # fmt: skip
    # SER-6465 era from trade date 2012-11-19: Sunday 17:00 open, 16:15 close.
    assert sessions[date(2012, 11, 19)] == (
        _ct(date(2012, 11, 18), 17), _ct(date(2012, 11, 19), 16, 15),
    )  # fmt: skip
    assert sessions[date(2012, 11, 20)] == (
        _ct(date(2012, 11, 19), 17), _ct(date(2012, 11, 20), 16, 15),
    )  # fmt: skip


def test_the_current_era_matches_todays_hours() -> None:
    sessions = _sessions(date(2015, 9, 21), date(2015, 9, 22))
    assert sessions[date(2015, 9, 22)] == (
        _ct(date(2015, 9, 21), 17), _ct(date(2015, 9, 22), 16),
    )  # fmt: skip


def test_a_late_open_exception_overrides_the_eras_open() -> None:
    late = {
        "holiday_date": date(2011, 1, 18),
        "holiday_name": "test late open",
        "market_status": MarketStatus.LATE_OPEN.value,
        "early_close_time": None,
        "late_open_time": time(17, 0),
    }
    sessions = _sessions(date(2011, 1, 17), date(2011, 1, 19), [late])
    assert sessions[date(2011, 1, 18)][0] == _ct(date(2011, 1, 17), 17)
    assert sessions[date(2011, 1, 19)][0] == _ct(date(2011, 1, 18), 15, 30)


def test_a_monday_early_close_still_opens_on_sunday() -> None:
    mlk = early_close_day(date(2011, 1, 17), "Martin Luther King Jr. Day", time(12))
    holidays = [{**mlk, "market_status": mlk["market_status"].value}]
    sessions = _sessions(date(2011, 1, 17), date(2011, 1, 17), holidays)
    assert sessions[date(2011, 1, 17)] == (
        _ct(date(2011, 1, 16), 17), _ct(date(2011, 1, 17), 12),
    )  # fmt: skip


def test_a_closed_monday_stays_closed() -> None:
    closed = {
        "holiday_date": date(2011, 1, 17),
        "holiday_name": "test closure",
        "market_status": MarketStatus.CLOSED.value,
        "early_close_time": None,
        "late_open_time": None,
    }
    assert date(2011, 1, 17) not in _sessions(
        date(2011, 1, 17), date(2011, 1, 18), [closed]
    )


def test_an_era_without_a_distinct_sunday_open_adds_no_rows() -> None:
    segment = Segment(date(2016, 1, 4), date(2016, 1, 29), _LATEST)
    assert with_sunday_opens([], segment) == []
