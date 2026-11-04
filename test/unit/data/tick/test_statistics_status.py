"""The statistics publication window, from calendar sessions (slice 303, D7)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.statistics_status import publication_windows

CHICAGO = ZoneInfo("America/Chicago")


def _session(day: date, *, opened: date | None = None) -> Session:
    """CME equity's regular 17:00 -> 16:00 Chicago, dated by its close day."""
    start = opened or day - timedelta(days=1)
    return Session(
        "CME_EQUITY",
        day,
        datetime.combine(start, time(17), CHICAGO).astimezone(UTC),
        datetime.combine(day, time(16), CHICAGO).astimezone(UTC),
    )


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def test_a_midweek_window_runs_from_the_sessions_first_day_to_the_next_close_day() -> (
    None
):
    tuesday, wednesday = date(2024, 12, 3), date(2024, 12, 4)
    calendar = [_session(tuesday), _session(wednesday)]
    # Tuesday opens Monday 23:00Z, so its first UTC day is Monday.
    assert publication_windows([calendar[0]], calendar) == [
        _days(date(2024, 12, 2), wednesday)
    ]


def test_a_friday_window_reaches_mondays_close_day() -> None:
    friday, monday = date(2024, 12, 6), date(2024, 12, 9)
    calendar = [_session(friday), _session(monday, opened=date(2024, 12, 8))]
    (window,) = publication_windows([calendar[0]], calendar)
    assert window == _days(date(2024, 12, 5), monday)
    assert date(2024, 12, 7) in window  # the weekend between is part of it


def test_a_holiday_is_skipped_by_taking_the_next_session_of_the_calendar() -> None:
    wednesday, friday = date(2024, 11, 27), date(2024, 11, 29)
    calendar = [_session(wednesday), _session(friday, opened=date(2024, 11, 28))]
    (window,) = publication_windows([calendar[0]], calendar)
    assert window == _days(date(2024, 11, 26), friday)


def test_each_judged_session_has_its_own_window() -> None:
    days = [date(2024, 12, 3), date(2024, 12, 4), date(2024, 12, 5)]
    calendar = [_session(day) for day in days]
    windows = publication_windows(calendar[:2], calendar)
    assert [(w[0], w[-1]) for w in windows] == [
        (date(2024, 12, 2), days[1]),
        (date(2024, 12, 3), days[2]),
    ]
