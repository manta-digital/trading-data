"""Measure the data facts of a tick database's ``statistics`` family (slice 303, D11).

Read-only: every statement is a ``SELECT``. Prints, per universe product:

- the ``ts_ref`` time of day and ``stat_flags`` values seen per referenced type;
- how many distinct reference dates there are and which are not calendar sessions;
- the latest receive time of each type relative to its session's close, in hours;
- V / C (traded over cleared volume) quantiles and verdict counts per volume
  family, over the sessions load-complete in both;
- the time one resolved read of the three types takes over the range.

URLs come from ``Settings`` (``MT_TICK_DB_URL``, ``MT_TIMESCALE_DB_URL``); an unset
one stops the script naming it. Manual, developer-run only. Usage::

    uv run python scripts/measure_303_statistics.py --start 2024-09-01 --end 2025-01-01
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, date, datetime
from datetime import time as day_time
from typing import Any

import psycopg

from manta_trading.config import Settings
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.schema_families import VOLUME_FAMILIES
from manta_trading.data.tick.statistics_constants import (
    REFERENCED_STAT_TYPES,
    STATISTICS_PUBLICATION_SESSIONS,
)
from manta_trading.data.tick.statistics_reads import (
    checkable_sessions,
    resolved,
    volume_check,
)
from manta_trading.data.tick.status_reads import calendar_sessions, shape_of
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

_TYPES = sorted(int(t) for t in REFERENCED_STAT_TYPES)
_OF_PRODUCT = (
    " JOIN (SELECT DISTINCT instrument_id FROM tick_definition WHERE asset = %s)"
    " d USING (instrument_id)"
)


def _required(value: Any, name: str) -> str:
    if not value:
        sys.exit(f"{name} is not set")
    return str(value)


async def _rows(conn: Any, sql: str, params: tuple[Any, ...]) -> list[Any]:
    return await (await conn.execute(sql, params)).fetchall()


async def _reference(conn: Any, product: str, closes: dict[date, datetime]) -> None:
    rows = await _rows(
        conn,
        "SELECT s.stat_type, s.stat_flags,"
        " (to_timestamp(s.ts_ref / 1e9) AT TIME ZONE 'UTC')::time, count(*),"
        " count(DISTINCT s.session_date) FROM tick_statistics s"
        + _OF_PRODUCT
        + " WHERE s.stat_type = ANY(%s) AND s.session_date IS NOT NULL"
        " GROUP BY 1, 2, 3 ORDER BY 1, 2, 3",
        (product, _TYPES),
    )
    for stat_type, flags, ref_time, count, _ in rows:
        print(f"  type {stat_type} flags {flags} ts_ref {ref_time} records {count}")
    found = {
        d
        for (d,) in await _rows(
            conn,
            "SELECT DISTINCT s.session_date FROM tick_statistics s"
            + _OF_PRODUCT
            + " WHERE s.session_date IS NOT NULL",
            (product,),
        )
    }
    print(f"  reference dates {len(found)}; not sessions {sorted(found - set(closes))}")


async def _latency(
    conn: Any, product: str, closes: dict[date, datetime], first: date, last: date
) -> None:
    rows = await _rows(
        conn,
        "SELECT s.session_date, s.stat_type, max(s.ts_recv) FROM tick_statistics s"
        + _OF_PRODUCT
        + " WHERE s.stat_type = ANY(%s) AND s.session_date BETWEEN %s AND %s"
        " GROUP BY 1, 2",
        (product, _TYPES, first, last),
    )
    for stat_type in _TYPES:
        hours = [
            (datetime.fromtimestamp(ns / 1e9, UTC) - closes[day]).total_seconds() / 3600
            for day, t, ns in rows
            if t == stat_type and day in closes
        ]
        if hours:
            print(
                f"  type {stat_type} last recv after close (h): min {min(hours):.1f}"
                f" median {statistics.median(hours):.1f} max {max(hours):.1f}"
                f" over {len(hours)} sessions"
                f" (publication sessions {STATISTICS_PUBLICATION_SESSIONS})"
            )


async def _volume(conn: Any, url: str, entry: TickUniverseEntry, sessions: Any) -> None:
    for family in sorted(VOLUME_FAMILIES, key=str):
        ok = await checkable_sessions(conn, url, entry, sessions, family)
        if not ok:
            continue
        found = await volume_check(conn, shape_of(entry), family, entry.product, ok)
        ratios = sorted(v.ratio for v in found if v.ratio is not None)
        spread = [ratios[int(p * (len(ratios) - 1))] for p in (0.1, 0.5, 0.9)]
        counts = dict(Counter(v.verdict.value for v in found))
        print(f"  volume {family}: {len(ok)} sessions {counts} V/C p10/50/90 {spread}")


async def _measure(tick_url: str, calendar_url: str, first: date, last: date) -> None:
    start = datetime.combine(first, day_time(), UTC)
    end = datetime.combine(last, day_time(), UTC)
    async with await psycopg.AsyncConnection.connect(tick_url, autocommit=True) as c:
        for entry in TICK_UNIVERSE:
            print(f"== {entry.product}")
            calendar_id = calendar_for_product(entry.product)
            sessions = calendar_sessions(calendar_url, calendar_id, start, end)
            closes = {s.session_date: s.close_utc for s in sessions}
            await _reference(c, entry.product, closes)
            await _latency(c, entry.product, closes, first, last)
            await _volume(c, calendar_url, entry, sessions)
            began = time.monotonic()
            got = await resolved(
                c, shape_of(entry), list(REFERENCED_STAT_TYPES), first, last
            )
            print(f"  resolved read {len(got)} rows in {time.monotonic() - began:.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = Settings()
    tick_url = _required(settings.tick_db_url, "MT_TICK_DB_URL")
    calendar_url = _required(settings.timescale_db_url, "MT_TIMESCALE_DB_URL")
    asyncio.run(_measure(tick_url, calendar_url, args.start, args.end))


if __name__ == "__main__":
    main()
