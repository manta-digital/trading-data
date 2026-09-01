"""Route handler for ``GET /api/v1/gaps/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    GATEWAY_TIMEOUT_RESPONSE,
    GapRecord,
    GapsResponse,
)
from manta_trading.constants import Granularity

router = APIRouter()

_MINUTE_GRAINS: frozenset[Granularity] = frozenset(
    {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}
)
_DAILY_GRAINS: frozenset[Granularity] = frozenset(
    {Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1}
)

_DB_GRANULARITY: dict[Granularity, str] = {g: "minute" for g in _MINUTE_GRAINS} | {
    g: "daily" for g in _DAILY_GRAINS
}

_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s
      AND (%s::text IS NULL OR granularity = %s)
      AND gap_start <  COALESCE(%s::timestamptz,  'infinity')
      AND gap_end   >  COALESCE(%s::timestamptz, '-infinity')
    ORDER BY gap_start
"""
"""One statement covering every combination of the optional filters.

An absent filter is expressed as an unbounded value rather than as a different
query. Four hand-written variants selected by an ``if`` ladder is what produced
the defect this replaced: ``has_window`` was true when *either* bound was given,
so a one-sided window ran the two-sided query with the other bound as ``NULL``,
and ``gap_start < NULL`` is ``NULL`` — never true. Every one-sided request
therefore returned an empty gap list, silently, which on the endpoint that
reports data-integrity holes is the most dangerous possible wrong answer.
"""


def _window_start_utc(d: date) -> datetime:
    """Midnight UTC on ``d`` — the inclusive lower bound of the window."""
    return datetime.combine(d, time.min, tzinfo=UTC)


def _window_end_utc(d: date) -> datetime:
    """Midnight UTC on the day *after* ``d``, making ``end`` inclusive.

    The bars route reaches the same meaning with ``time.max`` because its
    predicate is closed (``time <= %s``); this predicate is half-open
    (``gap_start < %s``), so the exclusive next-midnight bound is the exact
    analogue and avoids a sub-microsecond edge. Before this, ``end`` was
    midnight of the end date itself, so a gap beginning anywhere on the last
    requested day was excluded — verified on prod with SPY's gap at
    2004-01-01T00:00Z, which ``end=2004-01-01`` missed and ``end=2004-01-02``
    returned.
    """
    return datetime.combine(d + timedelta(days=1), time.min, tzinfo=UTC)


@router.get("/api/v1/gaps/{symbol}", responses=GATEWAY_TIMEOUT_RESPONSE)
async def get_gaps(
    symbol: str,
    granularity: Granularity | None = None,
    start: date | None = None,
    end: date | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> GapsResponse:
    """Return data gaps for ``symbol``, filtered by granularity and date window."""
    loop = asyncio.get_running_loop()
    db_gran: str | None = (
        _DB_GRANULARITY[granularity] if granularity is not None else None
    )

    def _query() -> list[GapRecord]:
        # Each optional filter is passed as itself or as NULL; the SQL turns a
        # NULL into "unbounded". No branching, so no combination can select a
        # query that silently drops a bound.
        start_dt = _window_start_utc(start) if start is not None else None
        end_dt = _window_end_utc(end) if end is not None else None
        cursor = db.execute(
            _GAPS_SQL, (symbol, db_gran, db_gran, end_dt, start_dt)
        )

        rows = cursor.fetchall()
        return [
            GapRecord(
                gap_start=row[0],
                gap_end=row[1],
                granularity=row[2],
                fetch_status=row[3],
                attempt_count=row[4],
                last_attempt_ts=row[5],
            )
            for row in rows
        ]

    gaps = await loop.run_in_executor(None, _query)
    return GapsResponse(symbol=symbol, count=len(gaps), gaps=gaps)
