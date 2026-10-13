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

from fastapi import HTTPException
from starlette import status as http_status

__all__ = [
    "NARROW_FILTER",
    "NARROW_TICKS",
    "NARROW_WINDOW",
    "admit_rows",
    "not_found",
    "over_ceiling",
]

NARROW_FILTER = "narrow the filter"
"""Remedy for a catalog list: the caller bounds it with query parameters."""

NARROW_WINDOW = "narrow start/end"
"""Remedy for a time series: the caller bounds it with a shorter window."""

NARROW_TICKS = "narrow start/end or from/until"
"""Remedy for futures ticks: a session can hold more than the ceiling, so the
instant bounds are the way through it (231 TD3)."""


def admit_rows(count: int, max_rows: int, *, remedy: str) -> None:
    """Refuse a request whose result would exceed the shared rows ceiling.

    There is no pagination and no truncation: a response is complete or it is
    refused (188 D8). Both numbers come from the live count and the configured
    setting, never from a literal, so the message cannot drift from the
    ceiling actually in force — and quoting the real count is what tells the
    caller how far to narrow.
    """
    if count > max_rows:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"the request matches {count:,} rows, over the {max_rows:,} "
                f"row limit; {remedy}"
            ),
        )


def over_ceiling(max_rows: int, *, remedy: str) -> HTTPException:
    """The refusal for a read stopped at the ceiling (231 TD4).

    The futures routes enforce the ceiling while reading, so the true count
    is never computed and the message quotes only the limit. Returned, like
    :func:`not_found`, so the caller raises it after releasing its connection.
    """
    return HTTPException(
        status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=(f"the request matches more than the {max_rows:,} row limit; {remedy}"),
    )


def not_found(resource: str, ticker: str) -> HTTPException:
    """The 404 for an unknown ticker. Returned, not raised, so a caller can
    raise it from inside an executor callback where raising would be lost."""
    return HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"{resource} '{ticker}' not found",
    )
