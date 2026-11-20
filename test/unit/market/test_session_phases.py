"""Session phases, bucket placement and estimates (slice 174 task 8.2)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from manta_trading.constants import SessionMode, SessionPhase
from manta_trading.market.session_phases import (
    SessionBounds,
    assign,
    estimate_bars,
    estimate_minutes,
    phases_for,
)

ET = ZoneInfo("America/New_York")
HOUR = timedelta(hours=1)
REG, EXT = SessionMode.REGULAR, SessionMode.EXTENDED


def _et(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=ET)


def _bounds(
    day: date, close: str = "16:00", ext_close: str | None = "20:00"
) -> SessionBounds:
    return SessionBounds(
        session_date=day,
        open=_et(day, "09:30"),
        close=_et(day, close),
        extended_open=_et(day, "04:00") if ext_close else None,
        extended_close=_et(day, ext_close) if ext_close else None,
    )


FULL = _bounds(date(2026, 1, 14))
EARLY = _bounds(date(2025, 11, 28), close="13:00", ext_close="17:00")
# Sessions either side of both DST changes.
DST = [_bounds(date(2025, 10, 31)), _bounds(date(2025, 11, 3)),
       _bounds(date(2026, 3, 6)), _bounds(date(2026, 3, 9))]  # fmt: skip


@pytest.mark.parametrize("bounds", [FULL, *DST])
def test_full_session_counts(bounds: SessionBounds) -> None:
    assert estimate_bars([bounds], REG, HOUR) == 7
    assert estimate_bars([bounds], EXT, HOUR) == 17
    assert estimate_bars([bounds], EXT, 4 * HOUR) == 5
    assert estimate_minutes([bounds], REG) == 390
    assert estimate_minutes([bounds], EXT) == 960
    phases = phases_for(bounds, EXT)
    per_phase = [estimate_bars([bounds], EXT, HOUR)]
    assert [p.phase for p in phases] == [
        SessionPhase.PRE,
        SessionPhase.REGULAR,
        SessionPhase.POST,
    ]
    assert per_phase == [6 + 7 + 4]
    assert [p.minutes for p in phases] == [330, 390, 240]


def test_early_close_counts() -> None:
    assert estimate_bars([EARLY], REG, HOUR) == 4
    assert estimate_minutes([EARLY], REG) == 210


def test_estimates_sum_over_sessions() -> None:
    assert estimate_bars([FULL, EARLY], REG, HOUR) == 11
    assert estimate_minutes([FULL, EARLY], REG) == 600


def test_missing_extended_bound_raises_for_extended_only() -> None:
    bounds = _bounds(date(2026, 1, 14), ext_close=None)
    with pytest.raises(ValueError, match="2026-01-14"):
        phases_for(bounds, EXT)
    assert estimate_bars([bounds], REG, HOUR) == 7


def test_utc_is_not_a_session_mode() -> None:
    with pytest.raises(ValueError):
        phases_for(FULL, SessionMode.UTC)


@pytest.mark.parametrize(
    ("hhmm", "expected"),
    [
        ("04:00", (SessionPhase.PRE, "04:00")),
        ("09:29", (SessionPhase.PRE, "09:00")),
        ("09:30", (SessionPhase.REGULAR, "09:30")),
        ("10:29", (SessionPhase.REGULAR, "09:30")),
        ("10:30", (SessionPhase.REGULAR, "10:30")),
        ("15:59", (SessionPhase.REGULAR, "15:30")),
        ("16:00", (SessionPhase.POST, "16:00")),
        ("19:59", (SessionPhase.POST, "19:00")),
        ("20:00", None),
        ("03:59", None),
    ],
)
def test_assign_full_day(hhmm: str, expected: tuple[SessionPhase, str] | None) -> None:
    got = assign(_et(FULL.session_date, hhmm), FULL, EXT, HOUR)
    want = (expected[0], _et(FULL.session_date, expected[1])) if expected else None
    assert got == want


@pytest.mark.parametrize(
    ("hhmm", "expected"),
    [
        ("12:59", (SessionPhase.REGULAR, "12:30")),
        ("13:00", (SessionPhase.POST, "13:00")),
        ("16:59", (SessionPhase.POST, "16:00")),
        ("17:00", None),
    ],
)
def test_assign_early_close(
    hhmm: str, expected: tuple[SessionPhase, str] | None
) -> None:
    got = assign(_et(EARLY.session_date, hhmm), EARLY, EXT, HOUR)
    want = (expected[0], _et(EARLY.session_date, expected[1])) if expected else None
    assert got == want


def test_regular_mode_ignores_extended_minutes() -> None:
    assert assign(_et(FULL.session_date, "09:29"), FULL, REG, HOUR) is None
    assert assign(_et(FULL.session_date, "16:00"), FULL, REG, HOUR) is None


def test_same_et_time_either_side_of_dst_is_an_hour_apart_in_utc() -> None:
    before, after = DST[0], DST[1]  # 2025-10-31 (EDT), 2025-11-03 (EST)
    utc = ZoneInfo("UTC")
    b = assign(_et(before.session_date, "10:45"), before, REG, HOUR)
    a = assign(_et(after.session_date, "10:45"), after, REG, HOUR)
    assert b is not None and a is not None
    assert b[1].astimezone(utc).time() == time(14, 30)
    assert a[1].astimezone(utc).time() == time(15, 30)


@pytest.mark.parametrize("side", ["extended_open", "extended_close"])
def test_one_missing_extended_side_raises(side: str) -> None:
    from dataclasses import replace

    bounds = replace(FULL, **{side: None})
    with pytest.raises(ValueError, match="2026-01-14"):
        phases_for(bounds, EXT)
    assert estimate_minutes([bounds], REG) == 390
