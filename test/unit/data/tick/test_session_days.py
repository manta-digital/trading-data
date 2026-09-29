"""``session_days`` over hand-built sessions (slice 223; LLD 224 TD4, Days)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.session_days import session_days


def _utc(month: int, day: int, hour: int) -> datetime:
    return datetime(2024, month, day, hour, tzinfo=UTC)


class StubCalendar:
    """Returns the given sessions and records each call's bounds."""

    def __init__(self, sessions: Sequence[Session]) -> None:
        self.sessions = sessions
        self.calls: list[tuple[datetime, datetime]] = []

    def sessions_between(
        self, start_utc: datetime, end_utc: datetime
    ) -> Sequence[Session]:
        self.calls.append((start_utc, end_utc))
        return self.sessions


def _session(close_day: int, open_at: datetime, close_at: datetime) -> Session:
    return Session("CME_EQUITY", date(2024, 9, close_day), open_at, close_at)


#: Sunday 09-08 22:00 → Monday 09-09 21:00, then Monday 22:00 → Tuesday 21:00.
#: Friday 09-06 session: Thursday 22:00 → Friday 21:00. Saturday 09-07 is idle.
WEEK = [
    _session(6, _utc(9, 5, 22), _utc(9, 6, 21)),
    _session(9, _utc(9, 8, 22), _utc(9, 9, 21)),
    _session(10, _utc(9, 9, 22), _utc(9, 10, 21)),
]


def test_sunday_open_touches_sunday_and_monday() -> None:
    days = session_days(StubCalendar(WEEK), date(2024, 9, 8), date(2024, 9, 10))
    assert days == [date(2024, 9, 8), date(2024, 9, 9)]


def test_saturday_never_appears() -> None:
    days = session_days(StubCalendar(WEEK), date(2024, 9, 5), date(2024, 9, 11))
    assert date(2024, 9, 7) not in days
    assert days == [
        date(2024, 9, 5),
        date(2024, 9, 6),
        date(2024, 9, 8),
        date(2024, 9, 9),
        date(2024, 9, 10),
    ]


def test_clipped_at_both_ends_and_end_exclusive() -> None:
    days = session_days(StubCalendar(WEEK), date(2024, 9, 6), date(2024, 9, 9))
    assert days == [date(2024, 9, 6), date(2024, 9, 8)]


def test_close_at_midnight_does_not_touch_the_next_day() -> None:
    sessions = [_session(3, _utc(9, 2, 22), datetime(2024, 9, 3, tzinfo=UTC))]
    days = session_days(StubCalendar(sessions), date(2024, 9, 1), date(2024, 9, 5))
    assert days == [date(2024, 9, 2)]


def test_one_calendar_call_over_the_utc_range() -> None:
    calendar = StubCalendar(WEEK)
    session_days(calendar, date(2024, 9, 5), date(2024, 9, 11))
    assert calendar.calls == [(_utc(9, 5, 0), _utc(9, 11, 0))]
