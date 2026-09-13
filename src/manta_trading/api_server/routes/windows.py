"""Time-window resolution shared by the bars and Kalshi time-series routes.

The day-boundary convention lives here rather than in ``bars.py`` because two
route families now depend on it, and two derivations of "the last instant of a
day" is exactly how one of them ends up exclusive by accident (see
:func:`window_end_utc`).

This module belongs with the routes, not the readers: it raises
``HTTPException``. Readers take resolved ``datetime | None`` bounds only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from fastapi import HTTPException
from fastapi import status as http_status


def window_start_utc(d: date) -> datetime:
    """Midnight UTC on ``d`` — the inclusive lower bound of the window."""
    return datetime.combine(d, time.min, tzinfo=UTC)


def window_end_utc(d: date) -> datetime:
    """Last instant of ``d`` in UTC — the inclusive upper bound of the window.

    ``end`` is inclusive at every granularity. The daily path gets this for
    free by passing dates straight to a ``time <= %s`` predicate; the minute
    path converts to a timestamp first, and converting ``end`` to *midnight*
    made the bound effectively exclusive — a Mon–Fri ``1m`` request returned
    Mon–Thu, silently, with nothing in the response to say so. Measured on prod
    2026-08-04: 2,975 bars ending 06-13 23:59 for a window ending 06-14.
    """
    return datetime.combine(d, time.max, tzinfo=UTC)


def _as_utc(value: datetime | date, *, upper: bool) -> datetime:
    """Resolve one bound to an aware UTC datetime.

    A bare date takes the day-boundary convention above. A naive datetime is
    read as UTC rather than as local time: the API's every other timestamp is
    UTC, and guessing the server's zone would make the same request mean
    different windows on different hosts.
    """
    if not isinstance(value, datetime):
        return window_end_utc(value) if upper else window_start_utc(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def resolve_window(
    start: datetime | date | None, end: datetime | date | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve an optional time window to aware UTC bounds.

    ``None`` on either side means unbounded there — the reader emits no
    predicate for it rather than substituting a sentinel date.

    Raises:
        HTTPException: 422 when the resolved ``start`` is after the resolved
            ``end``, with the bars route's reversed-range message.
    """
    lower = None if start is None else _as_utc(start, upper=False)
    upper = None if end is None else _as_utc(end, upper=True)
    if lower is not None and upper is not None and lower > upper:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"start ({lower.isoformat()}) is after end ({upper.isoformat()}); "
                "the requested range is empty"
            ),
        )
    return lower, upper
