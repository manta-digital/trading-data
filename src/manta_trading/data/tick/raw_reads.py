"""The raw-row scans ``coverage`` makes: the only reads of the row tables.

Every other status read is of the manifest, the ledger, the conditions, the edge
or the definitions. These scan ``tick_trade`` (225 TD10), ``tick_ohlcv_1m``
(302 D6) or ``tick_statistics`` (303 D8), each bounded by the range, to prove the
ledger against the stored rows.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date
from typing import Any

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import STATISTICS_TABLE, StatType
from manta_trading.data.tick.status_reads import SHAPE_WHERE, Shape


async def _raw_rows(
    conn: Conn,
    table: str,
    aggregates: str,
    product: str,
    sessions: Sequence[tuple[date, int, int]],
) -> list[tuple[Any, ...]]:
    """One bounded scan of ``table`` per (instrument, session): the rows are
    ``(instrument_id, session_date, *aggregates)``. ``table`` and ``aggregates``
    are code constants.

    ``sessions`` are ``(session_date, open_ns, close_ns)``; the scan is
    bounded by the first open and last close, and each row is assigned to its
    session by a join against the sessions passed as arrays. Only instruments
    defined for ``product`` count: products share the table and their
    sessions overlap (232), and ingest stores no row without its definition.
    """
    dates, opens, closes = zip(*sessions, strict=True)
    cursor = await conn.execute(
        f"SELECT t.instrument_id, s.session_date, {aggregates} FROM {table} t"
        " JOIN unnest(%s::date[], %s::bigint[], %s::bigint[])"
        " AS s(session_date, open_ns, close_ns)"
        " ON t.ts_event BETWEEN s.open_ns AND s.close_ns"
        " WHERE t.ts_event BETWEEN %s AND %s"
        " AND t.instrument_id IN"
        " (SELECT instrument_id FROM tick_definition WHERE asset = %s)"
        " GROUP BY 1, 2",
        (list(dates), list(opens), list(closes), min(opens), max(closes), product),
    )
    return await cursor.fetchall()


async def raw_counts(
    conn: Conn, product: str, sessions: Sequence[tuple[date, int, int]]
) -> dict[tuple[int, date], int]:
    """``count(*)`` of ``tick_trade`` per (instrument, session) — the one scan."""
    if not sessions:
        return {}
    rows = await _raw_rows(conn, "tick_trade", "count(*)", product, sessions)
    return {(i, d): int(n) for i, d, n in rows}


async def raw_bar_totals(
    conn: Conn, product: str, sessions: Sequence[tuple[date, int, int]]
) -> dict[tuple[int, date], tuple[int, int]]:
    """``(count(*), sum(volume))`` of ``tick_ohlcv_1m`` per (instrument,
    session) — the bar tier's one scan, bounded as :func:`raw_counts`."""
    if not sessions:
        return {}
    aggregates = "count(*), sum(t.volume)"
    rows = await _raw_rows(conn, BAR_1M_TABLE, aggregates, product, sessions)
    return {(i, d): (int(n), int(v)) for i, d, n, v in rows}


async def raw_statistics_counts(
    conn: Conn,
    shape: Shape,
    stat_types: Collection[StatType],
    first: date,
    last: date,
) -> dict[tuple[int, date, int], int]:
    """``count(*)`` of ``tick_statistics`` per (instrument, session, stat type)
    over the shape's current units. Rows carry their session, so the scan is
    bounded by the session dates; records with no session never count."""
    cursor = await conn.execute(
        "SELECT s.instrument_id, s.session_date, s.stat_type, count(*)"
        f" FROM {STATISTICS_TABLE} s JOIN tick_archive_unit u ON u.unit_id = s.unit_id"
        " JOIN tick_request r ON r.request_id = u.request_id"
        f" WHERE {SHAPE_WHERE} AND s.stat_type = ANY(%(stat_types)s)"
        " AND s.session_date BETWEEN %(first)s AND %(last)s GROUP BY 1, 2, 3",
        shape.params(SchemaFamily.STATISTICS)
        | {
            "stat_types": sorted(int(t) for t in stat_types),
            "first": first,
            "last": last,
        },
    )
    return {(i, d, t): int(n) for i, d, t, n in await cursor.fetchall()}
