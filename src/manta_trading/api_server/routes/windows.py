"""Time-window resolution shared by the bars and Kalshi time-series routes.

The Kalshi routes use the half-open ``[start, end)`` convention (issue 68);
:func:`window_end_utc`, the inclusive "last instant of a day", remains for the
stock bars route only, until it moves to half-open too.

This module belongs with the routes, not the readers: it raises
``HTTPException``. Readers take resolved ``datetime | None`` bounds only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

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


def instant_utc(value: datetime) -> datetime:
    """``value`` as an aware UTC instant; a naive one is read as UTC.

    The API's every other timestamp is UTC, and guessing the server's zone
    would make the same request mean different windows on different hosts. The
    futures ``from``/``until`` bounds use it too (231)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _reject_reversed(lower: datetime | None, upper: datetime | None) -> None:
    """422 when both bounds are set and ``lower`` is after ``upper``."""
    if lower is not None and upper is not None and lower > upper:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"start ({lower.isoformat()}) is after end ({upper.isoformat()}); "
                "the requested range is empty"
            ),
        )


def resolve_half_open_window(
    start: datetime | date | None, end: datetime | date | None
) -> tuple[datetime | None, datetime | None]:
    """Resolve an optional window to aware UTC bounds ``[start, end)``.

    The Kalshi routes' convention (issues 61, 68): a bare date range ``D..E``
    is ``[D 00:00 UTC, E+1 00:00 UTC)``; explicit date-times are used as given.
    Candlesticks hold the periods that *open* inside the window.

    Raises:
        HTTPException: 422 when ``start`` is after ``end``.
    """
    if start is None:
        lower = None
    elif isinstance(start, datetime):
        lower = instant_utc(start)
    else:
        lower = window_start_utc(start)
    if end is None:
        upper = None
    elif isinstance(end, datetime):
        upper = instant_utc(end)
    else:
        upper = window_start_utc(end + timedelta(days=1))
    _reject_reversed(lower, upper)
    return lower, upper
