"""The calendar sessions a plan covers, and the sessions ``[from, until)`` reach.

A session is dated by its close and runs ``[open_utc, close_utc]``. Instants
``[from, until)`` reach a session when it opens before ``until`` and closes at
or after ``from``. With no ``start``/``end``, those sessions are the plan (issue
#81), so ``incomplete`` and ``unresolved`` describe the window that was asked
for rather than everything held.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.active_contract import roll_conninfo
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import calendar_sessions

_DAY = timedelta(days=1)

#: How far from an instant to look for the session it reaches. Longer than any
#: exchange closure, so the nearest session is always inside it; a lookup
#: window, not a limit on the request.
_REACH = timedelta(days=8)

#: ``(from, until)``, aware UTC instants; either side may be absent.
Bounds = tuple[datetime | None, datetime | None]
Span = tuple[date | None, date | None]


async def sessions_dated(
    calendar_url: str, product: str, start: date, end: date
) -> list[Session]:
    """The calendar sessions dated ``[start, end)``, in date order; a range past
    the calendar refuses naming ``mt data extend``."""
    found = await asyncio.to_thread(
        calendar_sessions,
        roll_conninfo(calendar_url),
        calendar_for_product(product),
        utc_midnight(start),
        utc_midnight(end),
    )
    return sorted(
        (s for s in found if start <= s.session_date < end),
        key=lambda s: s.session_date,
    )


def first_reached(sessions: Sequence[Session], lower: datetime) -> date | None:
    """The first session closing at or after ``lower``."""
    return next((s.session_date for s in sessions if s.close_utc >= lower), None)


def last_reached(sessions: Sequence[Session], upper: datetime) -> date | None:
    """The last session opening before ``upper``."""
    return next(
        (s.session_date for s in reversed(sessions) if s.open_utc < upper), None
    )


async def span_reached(
    calendar_url: str, product: str, span: Span, bounds: Bounds
) -> Span | None:
    """``span`` with each missing side taken from the sessions ``bounds`` reach.

    A side given in ``span`` wins. ``None`` when a narrowed side reaches no
    session, or the narrowed span is empty: the window holds no session, so the
    plan is empty. A side neither given nor bounded stays ``None`` and defaults
    to the held sessions later.
    """
    start, end = span
    lower, upper = bounds
    narrowed = False
    if start is None and lower is not None:
        day = lower.astimezone(UTC).date()
        found = await sessions_dated(calendar_url, product, day, day + _REACH)
        start = first_reached(found, lower)
        if start is None:
            return None
        narrowed = True
    if end is None and upper is not None:
        day = upper.astimezone(UTC).date()
        found = await sessions_dated(
            calendar_url, product, day - _REACH, day + 2 * _DAY
        )
        last = last_reached(found, upper)
        if last is None:
            return None
        end = last + _DAY
        narrowed = True
    if narrowed and start is not None and end is not None and end <= start:
        return None
    return start, end
