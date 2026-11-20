"""Shared refusals for the Kalshi routes: the row ceiling and the 404.

Extracted from ``kalshi_catalog.py`` and ``kalshi_timeseries.py``, which each
carried their own near-identical copy (188 code review F002). The same
extraction ``windows.py`` and ``serialization.py`` got in that slice, for the
same reason: two copies of a refusal are two places for the status code, the
wording, or the ceiling to drift apart.

The one thing that legitimately differed between the copies was the *remedy* —
"narrow the filter" for a catalog list, "narrow start/end" for a time window —
so that is a parameter rather than a reason to keep two functions.
"""

from __future__ import annotations

from enum import StrEnum

from fastapi import HTTPException
from starlette import status as http_status

__all__ = [
    "CLIENT_CLOSED_REQUEST",
    "NARROW_FILTER",
    "NARROW_TICKS",
    "NARROW_TICK_BARS",
    "NARROW_TIME_BARS",
    "NARROW_WINDOW",
    "Refusal",
    "RefusalReason",
    "admit_rows",
    "not_found",
    "over_ceiling",
    "range_reversed",
    "session_ceiling",
    "trade_ceiling",
]

NARROW_FILTER = "narrow the filter"
"""Remedy for a catalog list: the caller bounds it with query parameters."""

NARROW_WINDOW = "narrow start/end"
"""Remedy for a time series: the caller bounds it with a shorter window."""

NARROW_TICKS = "narrow start/end or from/until"
"""Remedy for futures ticks: a session can hold more than the ceiling, so the
instant bounds are the way through it (231 TD3)."""

NARROW_TICK_BARS = "raise ticks_per_bar, or narrow start/end or from/until"
"""Remedy for futures tick bars over the row ceiling: fewer, larger bars, or
a shorter span (308 D5)."""

NARROW_TIME_BARS = "use a larger granularity, or narrow start/end or from/until"
"""Remedy for futures time bars over the row ceiling: a full session at ``1s``
can exceed it, so the instant bounds page through it (309)."""

CLIENT_CLOSED_REQUEST = 499
"""Not an IANA code: the de facto "client closed request". Nobody receives it;
the futures tick-bar and time-bar routes answer it when the client has gone."""


class RefusalReason(StrEnum):
    """What a structured ``422`` refused (issue #23): the ``reason`` field."""

    ROW_CEILING = "row_ceiling"
    TRADE_CEILING = "trade_ceiling"
    SESSION_CEILING = "session_ceiling"
    RANGE_REVERSED = "range_reversed"
    CALENDAR_HORIZON = "calendar_horizon"
    MINUTE_CEILING = "minute_ceiling"
    SESSION_GRANULARITY = "session_granularity"


class Refusal(HTTPException):
    """An ``HTTPException`` whose body carries machine-readable fields beside
    ``error`` (issue #23). The prose is unchanged, so a client parsing it keeps
    working; ``app``'s handler merges ``fields`` into the body."""

    def __init__(self, status_code: int, message: str, **fields: object) -> None:
        super().__init__(status_code=status_code, detail=message)
        self.fields = fields


def _refuse(message: str, reason: RefusalReason, **fields: object) -> Refusal:
    return Refusal(
        http_status.HTTP_422_UNPROCESSABLE_CONTENT, message, reason=reason, **fields
    )


def range_reversed(message: str) -> Refusal:
    """The ``422`` for a window whose lower bound is after its upper."""
    return _refuse(message, RefusalReason.RANGE_REVERSED)


def trade_ceiling(message: str, *, limit: int, estimate: int) -> Refusal:
    """The ``422`` for a futures read over the trade limit."""
    return _refuse(message, RefusalReason.TRADE_CEILING, limit=limit, estimate=estimate)


def session_ceiling(message: str, *, limit: int, estimate: int) -> Refusal:
    """The ``422`` for a futures product read spanning too many sessions."""
    return _refuse(
        message, RefusalReason.SESSION_CEILING, limit=limit, estimate=estimate
    )


def admit_rows(count: int, max_rows: int, *, remedy: str) -> None:
    """Refuse a request whose result would exceed the shared rows ceiling.

    There is no pagination and no truncation: a response is complete or it is
    refused (188 D8). Both numbers come from the live count and the configured
    setting, never from a literal, so the message cannot drift from the
    ceiling actually in force — and quoting the real count is what tells the
    caller how far to narrow.
    """
    if count > max_rows:
        raise _refuse(
            f"the request matches {count:,} rows, over the {max_rows:,} "
            f"row limit; {remedy}",
            RefusalReason.ROW_CEILING,
            limit=max_rows,
            estimate=count,
        )


def over_ceiling(max_rows: int, *, remedy: str) -> Refusal:
    """The refusal for a read stopped at the ceiling (231 TD4).

    The futures routes enforce the ceiling while reading, so the true count
    is never computed and the message quotes only the limit. Returned, like
    :func:`not_found`, so the caller raises it after releasing its connection.
    """
    return _refuse(
        f"the request matches more than the {max_rows:,} row limit; {remedy}",
        RefusalReason.ROW_CEILING,
        limit=max_rows,
    )


def not_found(resource: str, ticker: str) -> HTTPException:
    """The 404 for an unknown ticker. Returned, not raised, so a caller can
    raise it from inside an executor callback where raising would be lost."""
    return HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"{resource} '{ticker}' not found",
    )
