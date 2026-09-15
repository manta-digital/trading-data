"""Route handlers for the Kalshi catalog under ``/api/v1/kalshi`` (slice 188).

Seven routes: the category listing, three scoped lists, and three seeks.
Every one is the same shape — seek the parent (404 if absent), count (422 if
over the ceiling), fetch, model — except ``/categories``, which has no parent
and whose row count is the number of distinct categories.
"""

from __future__ import annotations

import asyncio
from datetime import datetime  # noqa: TC003 — FastAPI needs the runtime type
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status

# ``get_max_bars`` reads ``MT_API_MAX_BARS_PER_REQUEST``, whose name is
# bars-specific but whose meaning is not: it is the one ceiling on rows in a
# single response, shared by the equity bars routes and these catalog and
# time-series routes alike (188 code review F003, and the shared-ceiling
# rationale in ``constants.py``). One knob an operator can reason about beats
# three that can disagree; the name is historical.
from manta_trading.api_server.admission import (
    NARROW_FILTER,
    admit_rows,
    not_found,
)
from manta_trading.api_server.deps import get_db, get_max_bars
from manta_trading.api_server.models.kalshi_catalog import (
    CategoryListResponse,
    EventListResponse,
    EventRecord,
    MarketListResponse,
    MarketRecord,
    SeriesListResponse,
    SeriesRecord,
)
from manta_trading.api_server.models.responses import GATEWAY_TIMEOUT_RESPONSE
from manta_trading.data.kalshi import serve_catalog as catalog
from manta_trading.data.kalshi.constants import MarketStatus

router = APIRouter(prefix="/api/v1/kalshi")

_VALID_STATUS: tuple[str, ...] = tuple(member.value for member in MarketStatus)
"""Every accepted ``status`` token, derived from the enum rather than restated.

``MarketStatus`` is the *served* vocabulary — what the column actually holds —
not ``MarketStatusFilter``, which is Kalshi's documented request vocabulary and
a different set of words (D2).
"""


def _resolve_status_filter(status: str | None) -> list[str] | None:
    """Resolve the ``status`` query parameter to a list of statuses, or no filter.

    Modelled on ``status.py::_resolve_health_filter``. ``None`` (omitted) means
    no filter; a present-but-empty value is an error rather than a silent
    "everything", because an unset ``?status={filter}`` template deserves a
    diagnostic rather than a plausible-looking full result.

    Raises:
        HTTPException: 422 when ``status`` is present but names no valid value.
    """
    if status is None:
        return None

    tokens = [token.strip() for token in status.split(",") if token.strip()]
    if not tokens:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "Query parameter 'status' was provided but empty. Omit it for "
                "no filter, or name one or more of: "
                f"{', '.join(sorted(_VALID_STATUS))}"
            ),
        )

    invalid = [token for token in tokens if token not in _VALID_STATUS]
    if invalid:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"Invalid status values: {', '.join(invalid)}. "
                f"Valid: {', '.join(sorted(_VALID_STATUS))}"
            ),
        )
    return tokens




@router.get("/categories", responses=GATEWAY_TIMEOUT_RESPONSE)
async def list_categories(
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> CategoryListResponse:
    """Return every series category with the number of series carrying it.

    Categories are free text Kalshi assigns, so this is how a client learns
    which values the series list's ``category`` filter accepts. The series
    counts double as a size map for planning those calls.
    """
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, catalog.fetch_categories, db)
    return CategoryListResponse.from_rows(rows)


@router.get("/series", responses=GATEWAY_TIMEOUT_RESPONSE)
async def list_series(
    category: str | None = None,
    search: str | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
    max_rows: Annotated[int, Depends(get_max_bars)] = 0,
) -> SeriesListResponse:
    """Return series matching an optional category and ticker-prefix search.

    ``category`` is an exact match; ``search`` matches the start of a ticker.
    The response is complete or refused — there is no pagination and no
    truncation, so a 200 always carries every matching row.
    """
    loop = asyncio.get_running_loop()

    def _read() -> tuple[int, list[catalog.SeriesRow]]:
        # Sequential statements on one connection, the symbols.py::_fetch_ranges
        # pattern: concurrency here would need a second pool checkout per
        # request, which 187 D7 rejected for reads this short.
        count = catalog.count_series(db, category=category, search=search)
        admit_rows(count, max_rows, remedy=NARROW_FILTER)
        return count, catalog.fetch_series_list(db, category=category, search=search)

    _, rows = await loop.run_in_executor(None, _read)
    return SeriesListResponse.from_rows(rows)


@router.get("/series/{ticker}", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_series(
    ticker: str,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> SeriesRecord:
    """Return one series by its ticker."""
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, catalog.fetch_series, db, ticker)
    if row is None:
        raise not_found("Series", ticker)
    return SeriesRecord.from_row(row)


@router.get("/series/{ticker}/events", responses=GATEWAY_TIMEOUT_RESPONSE)
async def list_events(
    ticker: str,
    strike_from: datetime | None = None,
    strike_to: datetime | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
    max_rows: Annotated[int, Depends(get_max_bars)] = 0,
) -> EventListResponse:
    """Return the events of one series, optionally bounded by strike date.

    ``strike_from`` and ``strike_to`` are inclusive. An event with no strike
    date is excluded whenever either bound is given, since a null cannot
    satisfy a comparison.
    """
    loop = asyncio.get_running_loop()

    def _read() -> list[catalog.EventRow]:
        # Sequential statements on one connection (symbols.py::_fetch_ranges,
        # 187 D7). The parent seek is what distinguishes an unknown series
        # (404) from a known one with no events (200 with an empty list).
        if catalog.fetch_series(db, ticker) is None:
            raise not_found("Series", ticker)
        count = catalog.count_events(
            db, ticker, strike_from=strike_from, strike_to=strike_to
        )
        admit_rows(count, max_rows, remedy=NARROW_FILTER)
        return catalog.fetch_events(
            db, ticker, strike_from=strike_from, strike_to=strike_to
        )

    rows = await loop.run_in_executor(None, _read)
    return EventListResponse.from_rows(ticker, rows)


@router.get("/events/{event_ticker}", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_event(
    event_ticker: str,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> EventRecord:
    """Return one event by its event ticker."""
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, catalog.fetch_event, db, event_ticker)
    if row is None:
        raise not_found("Event", event_ticker)
    return EventRecord.from_row(row)


@router.get("/events/{event_ticker}/markets", responses=GATEWAY_TIMEOUT_RESPONSE)
async def list_markets(
    event_ticker: str,
    status: str | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
    max_rows: Annotated[int, Depends(get_max_bars)] = 0,
) -> MarketListResponse:
    """Return the markets of one event, optionally filtered by status.

    ``status`` accepts a comma-separated list of market statuses; omit it for
    every status. Naming a value the column cannot hold is an error rather
    than an empty result.
    """
    statuses = _resolve_status_filter(status)
    loop = asyncio.get_running_loop()

    def _read() -> list[catalog.MarketRow]:
        # Sequential statements on one connection (symbols.py::_fetch_ranges,
        # 187 D7).
        if catalog.fetch_event(db, event_ticker) is None:
            raise not_found("Event", event_ticker)
        count = catalog.count_markets(db, event_ticker, statuses=statuses)
        admit_rows(count, max_rows, remedy=NARROW_FILTER)
        return catalog.fetch_markets(db, event_ticker, statuses=statuses)

    rows = await loop.run_in_executor(None, _read)
    return MarketListResponse.from_rows(event_ticker, rows)


@router.get("/markets/{ticker}", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_market(
    ticker: str,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> MarketRecord:
    """Return one market by its ticker."""
    loop = asyncio.get_running_loop()
    row = await loop.run_in_executor(None, catalog.fetch_market, db, ticker)
    if row is None:
        raise not_found("Market", ticker)
    return MarketRecord.from_row(row)
