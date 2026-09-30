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
    STORED_TIERS,
    SType,
    UnitState,
    calendar_for_product,
    tier_rank_sql,
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
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.tick_calendar import TickCalendarError, planning_product


@dataclass(frozen=True)
class SupersededUnit:
    """A current lower-tier unit of the same shape and day (TD5)."""

    unit_id: int
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


class Calendars:
    """One ``TradingCalendar`` per product for a run; blocking, close when done."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._open: dict[str, TradingCalendar] = {}

    def frame(self, product: str, day: date) -> SessionFrame:
        """The sessions ``day`` touches, the populated span and the zone."""
        try:
            calendar_id = calendar_for_product(product)
        except KeyError as exc:
            raise TickCalendarError(str(exc.args[0])) from exc
        calendar = self._open.get(calendar_id)
        if calendar is None:
            calendar = self._open[calendar_id] = TradingCalendar(calendar_id, self._url)
        try:
            sessions = calendar.sessions_between(
                utc_midnight(day), utc_midnight(day + timedelta(days=1))
            )
            first_open, last_close = calendar.populated_span()
            zone = calendar.zone()
        except (psycopg.OperationalError, CalendarNotFoundError) as exc:
            raise TickCalendarError(
                f"calendar {calendar_id} unavailable: {exc}"
            ) from exc
        except OutOfPopulatedRangeError as exc:
            reason = planning_span_reason(day, exc.first_open, exc.last_close)
            raise UnitCheckFailed(IngestCheck.SESSION_BOUNDARY, reason) from exc
        assert first_open is not None and last_close is not None  # sessions exist
        return SessionFrame(SessionIndex(sessions), first_open, last_close, zone)

    def close(self) -> None:
        for calendar in self._open.values():
            calendar.close()
        self._open.clear()


async def _definitions(conn: Conn, product: str, frame: SessionFrame) -> Definitions:
    sessions = frame.index.sessions
    first_open = utc_ns(sessions[0].open_utc) if sessions else 0
    last_close = utc_ns(sessions[-1].close_utc) if sessions else 0
    cursor = await conn.execute(
        "SELECT instrument_id, activation_ns, expiration_ns FROM tick_definition"
        " WHERE asset = %s AND activation_ns <= %s AND expiration_ns >= %s",
        (product, last_close, first_open),
    )
    return Definitions.of(await cursor.fetchall())


#: Rank expressions come from code constants only (``tier_rank_sql``).
_SUPERSEDED = (
    "SELECT o.unit_id, o.state, min(l.first_event_ns), max(l.last_event_ns)"
    " FROM tick_archive_unit o JOIN tick_request p USING (request_id)"
    " LEFT JOIN tick_ingest_ledger l ON l.unit_id = o.unit_id"
    " WHERE p.dataset = %(dataset)s AND p.symbols = %(symbols)s"
    " AND p.stype_in = %(stype_in)s AND o.unit_date = %(day)s"
    " AND p.schema = ANY(%(tiers)s)"
    " AND o.superseded_by_unit_id IS NULL AND o.reopened_at IS NULL"
    f" AND {tier_rank_sql('p.schema')} < {tier_rank_sql('%(schema)s')}"
    " GROUP BY o.unit_id, o.state ORDER BY o.unit_id"
)


async def _superseded(conn: Conn, unit: UnitRow) -> tuple[SupersededUnit, ...]:
    cursor = await conn.execute(
        _SUPERSEDED,
        {
            "dataset": unit.dataset,
            "symbols": list(unit.symbols),
            "stype_in": unit.stype_in.value,
            "day": unit.unit_date,
            "tiers": sorted(tier.value for tier in STORED_TIERS),
            "schema": unit.schema.value,
        },
    )
    return tuple(
        SupersededUnit(unit_id, UnitState(state), first, last)
        for unit_id, state, first, last in await cursor.fetchall()
    )


async def build_plan(conn: Conn, calendars: Calendars, unit: UnitRow) -> UnitIngestPlan:
    """The unit's plan; raises ``UnitCheckFailed`` or ``TickCalendarError``."""
    if unit.stype_in is not SType.PARENT:
        raise UnitCheckFailed(IngestCheck.SHAPE, shape_reason(unit.stype_in.value))
    product = planning_product(unit.stype_in, unit.symbols)
    frame = await asyncio.to_thread(calendars.frame, product, unit.unit_date)
    return UnitIngestPlan(
        unit=unit,
        product=product,
        calendar_id=calendar_for_product(product),
        frame=frame,
        definitions=await _definitions(conn, product, frame),
        superseded=await _superseded(conn, unit),
    )
