"""The reads ``status``, ``coverage`` and the roll path make (225 TD9, TD10; 229).

Every read here is of the manifest, the ledger, the conditions, the edge or
the definitions — except :func:`raw_counts`, the one ``tick_trade`` scan,
which only ``coverage`` makes, bounded by the range's first open and last
close. "Current" is ``COVERAGE_PREDICATE``: superseded and reopened units
never count.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

import psycopg

from manta_trading.data.base.session_index import Session
from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.constants import (
    CME_DATASET,
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    DatasetCondition,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn
from manta_trading.data.tick.schema_families import (
    FAMILY_LEDGER,
    FAMILY_SCHEMAS,
    SchemaFamily,
)
from manta_trading.data.tick.session_days import days_touched
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.data.tick.tick_status import (
    DayFacts,
    DayUnit,
    SessionVerdict,
    day_unit,
    verdict,
)
from manta_trading.data.tick.universe import TickUniverseEntry


@dataclass(frozen=True)
class Shape:
    """A product's request shape: which units and ledger rows are its own."""

    dataset: str
    symbols: tuple[str, ...]
    stype_in: SType

    def params(self, family: SchemaFamily) -> dict[str, Any]:
        """Query parameters; ``tiers`` is the schemas of ``family`` (302 D6)."""
        return {
            "dataset": self.dataset,
            "symbols": list(self.symbols),
            "stype_in": self.stype_in.value,
            "tiers": sorted(schema.value for schema in FAMILY_SCHEMAS[family]),
        }


#: The unit/request join, and "current units of the shape" over it.
#: ``COVERAGE_PREDICATE``'s columns exist only on the unit, so they need no
#: alias here or when the ledger joins in.
_UNITS = "tick_archive_unit u JOIN tick_request r USING (request_id)"


def _ledger_units(family: SchemaFamily) -> str:
    """The family's ledger joined to its unit and request (a code constant)."""
    return (
        f"{FAMILY_LEDGER[family]} l JOIN tick_archive_unit u USING (unit_id)"
        " JOIN tick_request r USING (request_id)"
    )


SHAPE_WHERE = (
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


async def shape_units(
    conn: Conn, shape: Shape, family: SchemaFamily
) -> dict[date, list[DayUnit]]:
    """Every current unit of the shape in ``family``, by day."""
    cursor = await conn.execute(
        "SELECT u.unit_date, u.unit_id, r.schema, u.state, u.fetch_status"
        f" FROM {_UNITS} WHERE {SHAPE_WHERE} ORDER BY u.unit_date, u.unit_id",
        shape.params(family),
    )
    by_day: dict[date, list[DayUnit]] = defaultdict(list)
    for day, unit_id, schema, state, status in await cursor.fetchall():
        by_day[day].append(
            DayUnit(unit_id, TickSchema(schema), UnitState(state), FetchStatus(status))
        )
    return dict(by_day)


LedgerColumn = Literal["record_count", "volume"]


async def _ledger_sums(
    conn: Conn,
    shape: Shape,
    family: SchemaFamily,
    first: date,
    last: date,
    column: LedgerColumn,
) -> dict[tuple[int, date], int]:
    """``sum(column)`` per (instrument, session) over the family's current units."""
    cursor = await conn.execute(
        f"SELECT l.instrument_id, l.session_date, sum(l.{column})"
        f" FROM {_ledger_units(family)} WHERE {SHAPE_WHERE}"
        " AND l.session_date BETWEEN %(first)s AND %(last)s GROUP BY 1, 2",
        shape.params(family) | {"first": first, "last": last},
    )
    return {(i, d): int(n) for i, d, n in await cursor.fetchall()}


async def ledger_totals(
    conn: Conn, shape: Shape, family: SchemaFamily, first: date, last: date
) -> dict[tuple[int, date], int]:
    """``record_count`` per (instrument, session) over current units."""
    return await _ledger_sums(conn, shape, family, first, last, "record_count")


async def ledger_volumes(
    conn: Conn, shape: Shape, family: SchemaFamily, first: date, last: date
) -> dict[tuple[int, date], int]:
    """``volume`` per (instrument, session) over current units."""
    return await _ledger_sums(conn, shape, family, first, last, "volume")


async def newest_event_ns(conn: Conn) -> int | None:
    """The newest stored tick, from the ledger over current units (230 TD7):
    ``max(last_event_ns)``, never a scan of ``tick_trade``. ``None`` if empty."""
    cursor = await conn.execute(
        "SELECT max(l.last_event_ns) FROM tick_ingest_ledger l"
        f" JOIN tick_archive_unit u USING (unit_id) WHERE {COVERAGE_PREDICATE}"
    )
    row = await cursor.fetchone()
    return None if row is None or row[0] is None else int(row[0])


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


async def contract_lines(
    conn: Conn, shape: Shape, family: SchemaFamily
) -> list[ContractLine]:
    """Per instrument with records: sessions, records, volume and span, with
    the latest definition's symbol, class and expiration."""
    cursor = await conn.execute(
        "WITH held AS ("
        " SELECT l.instrument_id, count(DISTINCT l.session_date) AS sessions,"
        " sum(l.record_count) AS records, sum(l.volume) AS volume,"
        " min(l.session_date) AS first_session, max(l.session_date) AS last_session"
        f" FROM {_ledger_units(family)} WHERE {SHAPE_WHERE}"
        " AND l.record_count > 0 GROUP BY 1),"
        " latest AS (SELECT DISTINCT ON (instrument_id) instrument_id, raw_symbol,"
        " instrument_class, expiration_ns FROM tick_definition"
        " ORDER BY instrument_id, activation_ns DESC)"
        " SELECT h.instrument_id, d.raw_symbol, d.instrument_class, d.expiration_ns,"
        " h.sessions, h.records, h.volume, h.first_session, h.last_session"
        " FROM held h LEFT JOIN latest d USING (instrument_id)"
        " ORDER BY d.expiration_ns NULLS LAST, h.instrument_id",
        shape.params(family),
    )
    return [
        ContractLine(i, sym, cls, exp, int(s), int(r), int(v), first, last)
        for i, sym, cls, exp, s, r, v, first, last in await cursor.fetchall()
    ]


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


@contextmanager
def open_calendar(url: str, calendar_id: str) -> Iterator[TradingCalendar]:
    """Blocking: a calendar whose failures are tick refusals; closed on exit.

    A database failure is :class:`TickCalendarError` (exit 4); a range outside
    the populated sessions is :class:`TickPreflightError` naming the remedy.
    """
    # Bounded like the roll path's own connections (231 TD2): a refused
    # calendar is an outage within the connect timeout, not the pool's 30 s.
    calendar = TradingCalendar(
        calendar_id, url, checkout_timeout=TICK_DB_CONNECT_TIMEOUT_SECONDS
    )
    try:
        yield calendar
    except (psycopg.OperationalError, CalendarNotFoundError) as exc:
        raise TickCalendarError(f"calendar {calendar_id} unavailable: {exc}") from exc
    except OutOfPopulatedRangeError as exc:
        # The database answered; the calendar just has no rows that far. A
        # refusal naming the remedy (mt data extend), not a storage outage.
        raise TickPreflightError(f"calendar {calendar_id}: {exc}") from exc
    finally:
        calendar.close()


def calendar_sessions(
    url: str, calendar_id: str, start: datetime, end: datetime
) -> list[Session]:
    """Blocking: the calendar's sessions meeting ``[start, end)``."""
    with open_calendar(url, calendar_id) as calendar:
        return calendar.sessions_between(start, end)


def shape_of(entry: TickUniverseEntry) -> Shape:
    return Shape(CME_DATASET, entry.symbols, entry.stype_in)


async def session_verdicts(
    conn: Conn,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
    family: SchemaFamily,
) -> list[SessionVerdict]:
    if not sessions:
        return []
    units = await shape_units(conn, shape_of(entry), family)
    days = [days_touched(s) for s in sessions]
    held = await conditions(conn, CME_DATASET, days[0][0], days[-1][-1])
    return [
        verdict(
            session.session_date,
            [
                DayFacts(d, day_unit(units.get(d, []), family), held.get(d))
                for d in touched
            ],
        )
        for session, touched in zip(sessions, days, strict=True)
    ]
