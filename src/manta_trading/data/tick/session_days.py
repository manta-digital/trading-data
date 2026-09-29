"""Session-touched UTC days: the days a product can have tick data on (223).

LLD 224 Technical Decision 4 (Days): the archive unit is one UTC day (222),
and the days wanted for a product are the UTC days one of its calendar's
sessions touches. A CME session opens the evening before its session date,
so a Sunday is touched (Sunday 22:00 UTC open) and a holiday whose next
session opens that evening is touched too (the 2024-12-26 session opens
2024-12-25 23:00 UTC). Saturdays and full closures are never touched.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol

from manta_trading.data.base.session_index import Session

_ONE_DAY = timedelta(days=1)
#: ``close_utc`` is the first instant after the session; the last instant in
#: it is one tick earlier (the calendar's grain is the microsecond).
_LAST_INSTANT = timedelta(microseconds=1)


class SessionSource(Protocol):
    """The one ``TradingCalendar`` call this module makes."""

    def sessions_between(
        self, start_utc: datetime, end_utc: datetime
    ) -> Sequence[Session]: ...


def _utc_midnight(day: date) -> datetime:
    return datetime.combine(day, time(), UTC)


def session_days(calendar: SessionSource, start: date, end: date) -> list[date]:
    """Every UTC day in ``[start, end)`` that a session touches, sorted.

    One ``sessions_between`` call. ``OutOfPopulatedRangeError`` from the
    calendar propagates: a range outside the populated span is never guessed.
    """
    touched: set[date] = set()
    for session in calendar.sessions_between(_utc_midnight(start), _utc_midnight(end)):
        day = session.open_utc.astimezone(UTC).date()
        last = (session.close_utc - _LAST_INSTANT).astimezone(UTC).date()
        while day <= last:
            if start <= day < end:
                touched.add(day)
            day += _ONE_DAY
    return sorted(touched)
