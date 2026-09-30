"""The reads ``status`` and ``coverage`` make (slice 225, TD9, TD10).

Every read here is of the manifest, the ledger, the conditions, the edge or
the definitions — except :func:`raw_counts`, the one ``tick_trade`` scan,
which only ``coverage`` makes, bounded by the range's first open and last
close. "Current" is ``COVERAGE_PREDICATE``: superseded and reopened units
never count.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    STORED_TIERS,
    DatasetCondition,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn
from manta_trading.data.tick.tick_status import DayUnit


@dataclass(frozen=True)
class Shape:
    """A product's request shape: which units and ledger rows are its own."""

    dataset: str
    symbols: tuple[str, ...]
    stype_in: SType

    def params(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "symbols": list(self.symbols),
            "stype_in": self.stype_in.value,
            "tiers": sorted(tier.value for tier in STORED_TIERS),
        }


#: The unit/request join, and "current tier units of the shape" over it.
#: ``COVERAGE_PREDICATE``'s columns exist only on the unit, so they need no
#: alias here or when the ledger joins in.
_UNITS = "tick_archive_unit u JOIN tick_request r USING (request_id)"
_LEDGER_UNITS = (
    "tick_ingest_ledger l JOIN tick_archive_unit u USING (unit_id)"
    " JOIN tick_request r USING (request_id)"
)
_SHAPE_WHERE = (
    "r.dataset = %(dataset)s AND r.symbols = %(symbols)s"
    " AND r.stype_in = %(stype_in)s AND r.schema = ANY(%(tiers)s)"
    f" AND {COVERAGE_PREDICATE}"
)


@dataclass(frozen=True)
class Edge:
    available_end: datetime
    observed_at: datetime


@dataclass(frozen=True)
class ContractLine:
    instrument_id: int
    raw_symbol: str | None
    instrument_class: str | None
    expiration_ns: int | None
    sessions: int
    records: int
    volume: int
    first_session: date
    last_session: date


async def shape_units(conn: Conn, shape: Shape) -> dict[date, list[DayUnit]]:
    """Every current tier unit of the shape, by day."""
    cursor = await conn.execute(
        "SELECT u.unit_date, u.unit_id, r.schema, u.state, u.fetch_status"
        f" FROM {_UNITS} WHERE {_SHAPE_WHERE} ORDER BY u.unit_date, u.unit_id",
        shape.params(),
    )
    by_day: dict[date, list[DayUnit]] = defaultdict(list)
    for day, unit_id, schema, state, status in await cursor.fetchall():
        by_day[day].append(
            DayUnit(unit_id, TickSchema(schema), UnitState(state), FetchStatus(status))
        )
    return dict(by_day)


async def ledger_totals(
    conn: Conn, shape: Shape, first: date, last: date
) -> dict[tuple[int, date], int]:
    """``record_count`` per (instrument, session) over current units."""
    cursor = await conn.execute(
        "SELECT l.instrument_id, l.session_date, sum(l.record_count)"
        f" FROM {_LEDGER_UNITS} WHERE {_SHAPE_WHERE}"
        " AND l.session_date BETWEEN %(first)s AND %(last)s GROUP BY 1, 2",
        shape.params() | {"first": first, "last": last},
    )
    return {(i, d): int(n) for i, d, n in await cursor.fetchall()}


async def conditions(
    conn: Conn, dataset: str, first: date, last: date
) -> dict[date, DatasetCondition]:
    cursor = await conn.execute(
        "SELECT condition_date, condition FROM tick_day_condition"
        " WHERE dataset = %s AND condition_date BETWEEN %s AND %s",
        (dataset, first, last),
    )
    return {day: DatasetCondition(value) for day, value in await cursor.fetchall()}


async def dataset_edge(conn: Conn, dataset: str) -> Edge | None:
    cursor = await conn.execute(
        "SELECT available_end, observed_at FROM tick_dataset_edge WHERE dataset = %s",
        (dataset,),
    )
    row = await cursor.fetchone()
    return None if row is None else Edge(row[0], row[1])


async def contract_lines(conn: Conn, shape: Shape) -> list[ContractLine]:
    """Per instrument with records: sessions, records, volume and span, with
    the latest definition's symbol, class and expiration."""
    cursor = await conn.execute(
        "WITH held AS ("
        " SELECT l.instrument_id, count(DISTINCT l.session_date) AS sessions,"
        " sum(l.record_count) AS records, sum(l.volume) AS volume,"
        " min(l.session_date) AS first_session, max(l.session_date) AS last_session"
        f" FROM {_LEDGER_UNITS} WHERE {_SHAPE_WHERE}"
        " AND l.record_count > 0 GROUP BY 1),"
        " latest AS (SELECT DISTINCT ON (instrument_id) instrument_id, raw_symbol,"
        " instrument_class, expiration_ns FROM tick_definition"
        " ORDER BY instrument_id, activation_ns DESC)"
        " SELECT h.instrument_id, d.raw_symbol, d.instrument_class, d.expiration_ns,"
        " h.sessions, h.records, h.volume, h.first_session, h.last_session"
        " FROM held h LEFT JOIN latest d USING (instrument_id)"
        " ORDER BY d.expiration_ns NULLS LAST, h.instrument_id",
        shape.params(),
    )
    return [
        ContractLine(i, sym, cls, exp, int(s), int(r), int(v), first, last)
        for i, sym, cls, exp, s, r, v, first, last in await cursor.fetchall()
    ]


async def raw_counts(
    conn: Conn, sessions: Sequence[tuple[date, int, int]]
) -> dict[tuple[int, date], int]:
    """``count(*)`` of ``tick_trade`` per (instrument, session) — the one scan.

    ``sessions`` are ``(session_date, open_ns, close_ns)``; the scan is
    bounded by the first open and last close, and each row is assigned to its
    session by a join against the sessions passed as arrays.
    """
    if not sessions:
        return {}
    dates, opens, closes = zip(*sessions, strict=True)
    cursor = await conn.execute(
        "SELECT t.instrument_id, s.session_date, count(*) FROM tick_trade t"
        " JOIN unnest(%s::date[], %s::bigint[], %s::bigint[])"
        " AS s(session_date, open_ns, close_ns)"
        " ON t.ts_event BETWEEN s.open_ns AND s.close_ns"
        " WHERE t.ts_event BETWEEN %s AND %s GROUP BY 1, 2",
        (list(dates), list(opens), list(closes), min(opens), max(closes)),
    )
    return {(i, d): int(n) for i, d, n in await cursor.fetchall()}
