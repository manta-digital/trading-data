"""The immutable per-unit plan a worker ingests from (slice 225, TD5–TD7).

Built on the loop, one unit at a time, before the unit is handed to a worker:

- the product (``planning_product``) and its calendar;
- the sessions the unit's day touches — one ``sessions_between(day 00:00Z,
  day+1 00:00Z)`` on the production calendar — with the populated span and
  the calendar's time zone (``SessionFrame``);
- the product's definitions whose window meets ``[first open, last close]``
  of those sessions;
- the current lower-tier units of the same shape and day this unit
  supersedes, with their ledger's min first and max last event times.

A plan that cannot be built for a reason of the unit raises
:class:`UnitCheckFailed` (``shape``, or ``session_boundary`` for a day past the
populated span, TD7). A calendar that cannot answer raises
:class:`TickCalendarError` (→ ``storage_abort``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg

from manta_trading.data.base.session_index import SessionIndex
from manta_trading.data.base.trading_calendar import (
    CalendarNotFoundError,
    OutOfPopulatedRangeError,
    TradingCalendar,
)
from manta_trading.data.tick.constants import (
    SType,
    TickSchema,
    UnitState,
    calendar_for_product,
)
from manta_trading.data.tick.ingest_checks import (
    IngestCheck,
    planning_span_reason,
    shape_reason,
)
from manta_trading.data.tick.ingest_records import (
    Definitions,
    SessionFrame,
    UnitCheckFailed,
    utc_ns,
)
from manta_trading.data.tick.manifest_reads import Conn, UnitRow
from manta_trading.data.tick.projectors import INGEST_PROJECTORS
from manta_trading.data.tick.schema_families import (
    FAMILY_LEDGER,
    SCHEMA_FAMILY,
    SchemaFamily,
    supersedes_sql,
)
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.statistics_session import ReferenceCalendar
from manta_trading.data.tick.tick_calendar import TickCalendarError, planning_product


@dataclass(frozen=True)
class SupersededUnit:
    """A current lower-tier unit of the same shape and day (TD5)."""

    unit_id: int
    schema: TickSchema
    state: UnitState
    first_event_ns: int | None
    last_event_ns: int | None


@dataclass(frozen=True)
class UnitIngestPlan:
    unit: UnitRow
    product: str
    calendar_id: str
    frame: SessionFrame
    definitions: Definitions
    superseded: tuple[SupersededUnit, ...]
    #: Every session date of the product's calendar from the dataset start to
    #: the unit's day; read for a statistics unit only (303 D4), else ``None``.
    reference_calendar: ReferenceCalendar | None = None


class Calendars:
    """One ``TradingCalendar`` per product for a run; blocking, close when done."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._open: dict[str, TradingCalendar] = {}
        # Per calendar: the last day the cached session dates reach, and them.
        self._references: dict[str, tuple[date, ReferenceCalendar]] = {}

    def _calendar(self, product: str) -> TradingCalendar:
        try:
            calendar_id = calendar_for_product(product)
        except KeyError as exc:
            raise TickCalendarError(str(exc.args[0])) from exc
        calendar = self._open.get(calendar_id)
        if calendar is None:
            calendar = self._open[calendar_id] = TradingCalendar(calendar_id, self._url)
        return calendar

    def frame(self, product: str, day: date) -> SessionFrame:
        """The sessions ``day`` touches, the populated span and the zone."""
        calendar = self._calendar(product)
        try:
            sessions = calendar.sessions_between(
                utc_midnight(day), utc_midnight(day + timedelta(days=1))
            )
            first_open, last_close = calendar.populated_span()
            zone = calendar.zone()
        except (psycopg.OperationalError, CalendarNotFoundError) as exc:
            raise TickCalendarError(
                f"calendar {calendar.calendar_id} unavailable: {exc}"
            ) from exc
        except OutOfPopulatedRangeError as exc:
            reason = planning_span_reason(day, exc.first_open, exc.last_close)
            raise UnitCheckFailed(IngestCheck.SESSION_BOUNDARY, reason) from exc
        assert first_open is not None and last_close is not None  # sessions exist
        return SessionFrame(SessionIndex(sessions), first_open, last_close, zone)

    def reference_calendar(self, product: str, day: date) -> ReferenceCalendar:
        """Every session date of the populated span up to ``day`` (303 D4).

        The span starts at the dataset start (301 filled it back to 2010), so
        no date the dataset covers is missing. Cached per calendar for the run:
        a cache that reaches past ``day`` is reused, as the assignment checks
        "after the unit" before membership.
        """
        calendar = self._calendar(product)
        cached = self._references.get(calendar.calendar_id)
        if cached is not None and cached[0] >= day:
            return cached[1]
        try:
            first_open, _ = calendar.populated_span()
            assert first_open is not None  # a populated calendar has a span
            sessions = calendar.sessions_between(
                first_open, utc_midnight(day + timedelta(days=1))
            )
        except (psycopg.OperationalError, CalendarNotFoundError) as exc:
            raise TickCalendarError(
                f"calendar {calendar.calendar_id} unavailable: {exc}"
            ) from exc
        except OutOfPopulatedRangeError as exc:
            reason = planning_span_reason(day, exc.first_open, exc.last_close)
            raise UnitCheckFailed(IngestCheck.SESSION_BOUNDARY, reason) from exc
        reference = ReferenceCalendar(
            calendar.calendar_id, frozenset(s.session_date for s in sessions)
        )
        self._references[calendar.calendar_id] = (day, reference)
        return reference

    def close(self) -> None:
        for calendar in self._open.values():
            calendar.close()
        self._open.clear()
        self._references.clear()


async def _definitions(
    conn: Conn, product: str, frame: SessionFrame, *, by_id: bool
) -> Definitions:
    """Windows meeting the span; with ``by_id``, every window of the product.

    Statistics are published before an instrument's definition activates and
    after it expires, and only the id is checked, so the statistics family
    loads them all.
    """
    select = "SELECT instrument_id, activation_ns, expiration_ns FROM tick_definition"
    if by_id:
        cursor = await conn.execute(select + " WHERE asset = %s", (product,))
        return Definitions.of(await cursor.fetchall())
    sessions = frame.index.sessions
    first_open = utc_ns(sessions[0].open_utc) if sessions else 0
    last_close = utc_ns(sessions[-1].close_utc) if sessions else 0
    cursor = await conn.execute(
        select + " WHERE asset = %s AND activation_ns <= %s AND expiration_ns >= %s",
        (product, last_close, first_open),
    )
    return Definitions.of(await cursor.fetchall())


#: Rank expressions and the ledger table come from code constants only
#: (``supersedes_sql``, ``FAMILY_LEDGER``). A superseded unit shares the new
#: unit's family, so its times are in that family's ledger.
_SUPERSEDED = (
    "SELECT o.unit_id, p.schema, o.state, min(l.first_event_ns),"
    " max(l.last_event_ns)"
    " FROM tick_archive_unit o JOIN tick_request p USING (request_id)"
    " LEFT JOIN {ledger} l ON l.unit_id = o.unit_id"
    " WHERE p.dataset = %(dataset)s AND p.symbols = %(symbols)s"
    " AND p.stype_in = %(stype_in)s AND o.unit_date = %(day)s"
    " AND p.schema = ANY(%(tiers)s)"
    " AND o.superseded_by_unit_id IS NULL AND o.reopened_at IS NULL"
    " AND {supersedes}"
    " GROUP BY o.unit_id, p.schema, o.state ORDER BY o.unit_id"
)


def superseded_sql(schema: TickSchema) -> str:
    """The superseded-units query, reading the ledger of ``schema``'s family."""
    ledger = FAMILY_LEDGER[SCHEMA_FAMILY[schema]]
    return _SUPERSEDED.format(
        ledger=ledger, supersedes=supersedes_sql("%(schema)s", "p.schema")
    )


async def _superseded(conn: Conn, unit: UnitRow) -> tuple[SupersededUnit, ...]:
    if SCHEMA_FAMILY[unit.schema] not in FAMILY_LEDGER:
        # A family with no ledger (definition) is never ingested, so it has
        # no stored rows for a unit to supersede.
        return ()
    cursor = await conn.execute(
        superseded_sql(unit.schema),
        {
            "dataset": unit.dataset,
            "symbols": list(unit.symbols),
            "stype_in": unit.stype_in.value,
            "day": unit.unit_date,
            "tiers": sorted(schema.value for schema in INGEST_PROJECTORS),
            "schema": unit.schema.value,
        },
    )
    return tuple(
        SupersededUnit(unit_id, TickSchema(schema), UnitState(state), first, last)
        for unit_id, schema, state, first, last in await cursor.fetchall()
    )


async def build_plan(conn: Conn, calendars: Calendars, unit: UnitRow) -> UnitIngestPlan:
    """The unit's plan; raises ``UnitCheckFailed`` or ``TickCalendarError``."""
    if unit.stype_in is not SType.PARENT:
        raise UnitCheckFailed(IngestCheck.SHAPE, shape_reason(unit.stype_in.value))
    product = planning_product(unit.stype_in, unit.symbols)
    frame = await asyncio.to_thread(calendars.frame, product, unit.unit_date)
    statistics = SCHEMA_FAMILY[unit.schema] is SchemaFamily.STATISTICS
    reference = (
        await asyncio.to_thread(calendars.reference_calendar, product, unit.unit_date)
        if statistics
        else None
    )
    return UnitIngestPlan(
        unit=unit,
        product=product,
        calendar_id=calendar_for_product(product),
        frame=frame,
        definitions=await _definitions(conn, product, frame, by_id=statistics),
        superseded=await _superseded(conn, unit),
        reference_calendar=reference,
    )
