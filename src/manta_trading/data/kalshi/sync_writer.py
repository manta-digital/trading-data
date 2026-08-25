"""The page writer of the catalog pass — the storage failure taxonomy in code.

One transaction per page. ``psycopg.IntegrityError`` (an out-of-vocabulary
status, a parent that vanished between resolution and write) rolls the page
back and re-writes it row by row, each row in its own transaction, so only
the offending rows become item errors; the run continues (exit 3).
``OperationalError`` and every other ``psycopg.Error`` propagate untouched —
the former is a storage abort (exit 4), the latter a bug.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import psycopg

from manta_trading.data.kalshi.sync_types import Page, SyncPhase

if TYPE_CHECKING:
    from manta_trading.data.kalshi.sync import CatalogSync

logger = logging.getLogger(__name__)

#: Rows written per kind: (series, events, markets).
Written = tuple[int, int, int]
_NOTHING: Written = (0, 0, 0)


def _add(a: Written, b: Written) -> Written:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _own_kind(phase: SyncPhase, written: Written) -> int:
    """The count the phase reports: its own row kind, never its parents'."""
    if phase is SyncPhase.SERIES:
        return written[0]
    if phase is SyncPhase.EVENTS:
        return written[1]
    return written[2]


async def write_page(core: CatalogSync, phase: SyncPhase, page: Page) -> int:
    """Write ``page`` in one transaction; on ``IntegrityError`` fall back row by row.

    Returns the number of rows of the phase's own kind written (series for
    the series phase, events for the events phase, markets otherwise).
    """
    try:
        async with core.repository.transaction():
            written = await _write_rows(core, page)
    except psycopg.IntegrityError as exc:
        logger.error(
            "%s: page rejected (%s %s); rewriting row by row",
            phase,
            type(exc).__name__,
            exc.sqlstate,
        )
        written = await _write_row_by_row(core, phase, page)
    return _own_kind(phase, written)


async def _write_rows(core: CatalogSync, page: Page) -> Written:
    repo = core.repository
    series = await repo.upsert_series(page.series) if page.series else 0
    events = await repo.upsert_events(page.events) if page.events else 0
    markets = 0
    if page.markets:
        outcome = await repo.upsert_markets(page.markets)
        for edge, count in outcome.transitions.items():
            core.result.transitions[edge] = core.result.transitions.get(edge, 0) + count
        markets = outcome.written
    return (series, events, markets)


async def _write_row_by_row(core: CatalogSync, phase: SyncPhase, page: Page) -> Written:
    written = _NOTHING
    for series in page.series:
        one = await _write_one(core, phase, series.ticker, Page(series=[series]))
        written = _add(written, one)
    for event in page.events:
        one = await _write_one(core, phase, event.event_ticker, Page(events=[event]))
        written = _add(written, one)
    for market in page.markets:
        one = await _write_one(core, phase, market.ticker, Page(markets=[market]))
        written = _add(written, one)
    return written


async def _write_one(
    core: CatalogSync, phase: SyncPhase, ticker: str, page: Page
) -> Written:
    try:
        async with core.repository.transaction():
            written = await _write_rows(core, page)
    except psycopg.IntegrityError as exc:
        # Taxonomy rows 4/5: the offending row is an item error; the run continues.
        core.item_error(phase, ticker, f"{type(exc).__name__} {exc.sqlstate}")
        return _NOTHING
    return written
