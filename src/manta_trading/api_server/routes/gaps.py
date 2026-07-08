"""Route handler for ``GET /api/v1/gaps/{symbol}``."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timezone
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends

from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import GapRecord, GapsResponse
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

_ALL_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s
    ORDER BY gap_start
"""

_GRAN_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s AND granularity = %s
    ORDER BY gap_start
"""

_WINDOWED_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s
      AND gap_start < %s
      AND gap_end   > %s
    ORDER BY gap_start
"""

_WINDOWED_GRAN_GAPS_SQL = """
    SELECT gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts
    FROM data_gaps
    WHERE symbol = %s AND granularity = %s
      AND gap_start < %s
      AND gap_end   > %s
    ORDER BY gap_start
"""


def _date_to_utc_datetime(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=timezone.utc)


@router.get("/api/v1/gaps/{symbol}")
async def get_gaps(
    symbol: str,
    granularity: Granularity | None = None,
    start: date | None = None,
    end: date | None = None,
    db: Annotated[psycopg.Connection[Any], Depends(get_db)] = None,  # type: ignore[assignment]
) -> GapsResponse:
    """Return data gaps for ``symbol``, optionally filtered by granularity and date window."""
    loop = asyncio.get_running_loop()
    db_gran: str | None = _DB_GRANULARITY[granularity] if granularity is not None else None

    def _query() -> list[GapRecord]:
        has_window = start is not None or end is not None
        start_dt = _date_to_utc_datetime(start) if start is not None else None
        end_dt = _date_to_utc_datetime(end) if end is not None else None

        if db_gran is not None and has_window:
            cursor = db.execute(
                _WINDOWED_GRAN_GAPS_SQL, (symbol, db_gran, end_dt, start_dt)
            )
        elif db_gran is not None:
            cursor = db.execute(_GRAN_GAPS_SQL, (symbol, db_gran))
        elif has_window:
            cursor = db.execute(_WINDOWED_GAPS_SQL, (symbol, end_dt, start_dt))
        else:
            cursor = db.execute(_ALL_GAPS_SQL, (symbol,))

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
