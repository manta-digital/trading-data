"""The read-time roll schedule and the latest active contract (slice 229).

LLD 229 TD9, TD10, API Contracts. :func:`build_schedule` (``active`` with a
range; 230 and 231) and :func:`latest_active` (``status``, ``active`` without
a range) take URLs, never connections. Each opens its own calendar and tick
connection from :func:`roll_conninfo` (connect timeout, keepalives, statement
timeout, read-only), runs every tick read in one ``REPEATABLE READ READ ONLY``
transaction, and closes both before returning. Resolution and the calendar
read run in ``asyncio.to_thread``.

The calendar read reaches forward to the calendar's last populated session
(clamped there): the forecast and the calendar offset need sessions after the
latest one held, and the candidates' expirations run past the calendar.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import psycopg
from psycopg.conninfo import make_conninfo

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import (
    TICK_DB_CONNECT_TIMEOUT_SECONDS,
    TICK_DB_KEEPALIVES_COUNT,
    TICK_DB_KEEPALIVES_IDLE_SECONDS,
    TICK_DB_KEEPALIVES_INTERVAL_SECONDS,
    TICK_ROLL_MAX_SESSIONS,
    TICK_ROLL_STATEMENT_TIMEOUT_SECONDS,
    VOLUME_RANK_LAG_SESSIONS,
    calendar_for_product,
)
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.roll_inputs import (
    definitions_as_of,
    held_session_bounds,
    outright_definitions,
    session_volumes,
)
from manta_trading.data.tick.roll_resolve import active_line, resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    ActiveLine,
    RollInputError,
    RollInputs,
    RollSchedule,
)
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import (
    open_calendar,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_calendar import TickCalendarError
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.universe import TickUniverseEntry

#: ``mt data extend``, the remedy for a calendar that does not reach a range.
_EXTEND = "run mt data extend"


class RollRangeTooLong(TickPreflightError):
    """A schedule range spans more than ``TICK_ROLL_MAX_SESSIONS`` sessions."""


def roll_conninfo(url: str) -> str:
    """``url`` bounded for the roll path (TD9): connect timeout, keepalives,
    a statement timeout, and read-only transactions by default."""
    return make_conninfo(
        url,
        connect_timeout=TICK_DB_CONNECT_TIMEOUT_SECONDS,
        keepalives=1,
        keepalives_idle=TICK_DB_KEEPALIVES_IDLE_SECONDS,
        keepalives_interval=TICK_DB_KEEPALIVES_INTERVAL_SECONDS,
        keepalives_count=TICK_DB_KEEPALIVES_COUNT,
        options=(
            f"-c statement_timeout={TICK_ROLL_STATEMENT_TIMEOUT_SECONDS}s"
            " -c default_transaction_read_only=on"
        ),
    )


def _margin(sessions: int) -> timedelta:
    """Calendar days that hold at least ``sessions`` sessions, holidays included."""
    return timedelta(days=2 * sessions + 7)


@dataclass(frozen=True)
class _CalendarRead:
    sessions: tuple[Session, ...]
    horizon: date
    first_open: datetime
    last_close: datetime


def _read_calendar(url: str, calendar_id: str, start: datetime) -> _CalendarRead:
    """Blocking: sessions from ``start`` (clamped) to the last populated one."""
    with open_calendar(url, calendar_id) as calendar:
        first_open, last_close = calendar.populated_span()
        horizon = calendar.horizon_end()
        if first_open is None or last_close is None or horizon is None:
            raise TickPreflightError(
                f"calendar {calendar_id} has no sessions; {_EXTEND}"
            )
        sessions = calendar.sessions_between(max(start, first_open), last_close)
    return _CalendarRead(tuple(sessions), horizon, first_open, last_close)


async def _calendar(calendar_url: str, product: str, start: datetime) -> _CalendarRead:
    return await asyncio.to_thread(
        _read_calendar,
        roll_conninfo(calendar_url),
        calendar_for_product(product),
        start,
    )


@asynccontextmanager
async def _snapshot(tick_url: str) -> AsyncIterator[Conn]:
    """The roll path's own tick connection, inside one REPEATABLE READ READ
    ONLY transaction; closed on exit."""
    conn = await psycopg.AsyncConnection.connect(roll_conninfo(tick_url))
    try:
        await conn.set_isolation_level(psycopg.IsolationLevel.REPEATABLE_READ)
        await conn.set_read_only(True)
        async with conn.transaction():
            yield conn
    finally:
        await conn.close()


async def _inputs(
    conn: Conn,
    entry: TickUniverseEntry,
    calendar: _CalendarRead,
    context: Sequence[Session],
) -> RollInputs:
    """Definitions, volume and verdicts over ``context`` from the open snapshot."""
    shape = shape_of(entry)
    contracts = await outright_definitions(conn, entry.product)
    as_of = await definitions_as_of(conn, shape)
    verdicts = await session_verdicts(conn, entry, context)
    volumes = await session_volumes(
        conn, shape, context[0].session_date, context[-1].session_date
    )
    return RollInputs(
        sessions=calendar.sessions,
        contracts=contracts,
        volumes=volumes,
        statuses={v.session_date: v.status for v in verdicts},
        definitions_as_of=as_of,
        calendar_horizon=calendar.horizon,
    )


async def build_schedule(
    tick_url: str,
    calendar_url: str,
    entry: TickUniverseEntry,
    rule: RollRule,
    start: date,
    end: date,
) -> RollSchedule:
    """One answer per session dated ``[start, end)`` under ``rule``.

    Raises:
        RollRangeTooLong: over ``TICK_ROLL_MAX_SESSIONS``, before any tick read.
        TickPreflightError: the range is outside the populated calendar.
        RollInputError: a broken invariant in the stored inputs.
        psycopg.OperationalError, TickCalendarError: a database failed.
    """
    calendar = await _calendar(
        calendar_url,
        entry.product,
        utc_midnight(start) - _margin(VOLUME_RANK_LAG_SESSIONS),
    )
    days = [s.session_date for s in calendar.sessions]
    wanted = [i for i, day in enumerate(days) if start <= day < end]
    if len(wanted) > TICK_ROLL_MAX_SESSIONS:
        raise RollRangeTooLong(
            f"{start} → {end} spans {len(wanted)} sessions, over "
            f"TICK_ROLL_MAX_SESSIONS ({TICK_ROLL_MAX_SESSIONS}); ask for a "
            "narrower range"
        )
    if (
        utc_midnight(start) < calendar.first_open
        or utc_midnight(end) > calendar.last_close
    ):
        raise TickPreflightError(
            f"{start} → {end} is outside the calendar's sessions "
            f"({calendar.first_open:%Y-%m-%d} → {calendar.horizon}); {_EXTEND}"
        )
    if not wanted:
        return RollSchedule(entry.product, rule, ())
    first = max(wanted[0] - VOLUME_RANK_LAG_SESSIONS, 0)
    context = calendar.sessions[first : wanted[-1] + 1]
    async with _snapshot(tick_url) as conn:
        inputs = await _inputs(conn, entry, calendar, context)
    answers = await asyncio.to_thread(resolve_sessions, rule, inputs, start, end)
    return RollSchedule(entry.product, rule, answers)


async def latest_active(
    tick_url: str,
    calendar_url: str,
    entry: TickUniverseEntry,
    rules: Sequence[RollRule],
) -> tuple[ActiveLine, ...]:
    """One line per rule for the latest complete session held; never raises.

    A failure of the shared snapshot or calendar sets ``error`` on every line;
    a broken invariant in one rule sets only that line's (TD9).
    """
    try:
        return await _latest(tick_url, calendar_url, entry, rules)
    except (
        psycopg.OperationalError,
        TickCalendarError,
        TickPreflightError,
        RollInputError,
    ) as exc:
        # Reported, not raised: a shared read failed (a database, the calendar's
        # range, or the product's definitions), so every rule's line carries it
        # and the caller (status) still prints its own lines.
        return tuple(ActiveLine(entry.product, r, error=str(exc)) for r in rules)


async def _latest(
    tick_url: str,
    calendar_url: str,
    entry: TickUniverseEntry,
    rules: Sequence[RollRule],
) -> tuple[ActiveLine, ...]:
    async with _snapshot(tick_url) as conn:
        bounds = await held_session_bounds(conn, shape_of(entry))
        if bounds is None:
            reason = f"no sessions held for {entry.product}"
            return tuple(ActiveLine(entry.product, r, reason=reason) for r in rules)
        calendar = await _calendar(
            calendar_url,
            entry.product,
            utc_midnight(bounds[0]) - _margin(VOLUME_RANK_LAG_SESSIONS),
        )
        held = [
            i
            for i, s in enumerate(calendar.sessions)
            if bounds[0] <= s.session_date <= bounds[1]
        ][-TICK_ROLL_MAX_SESSIONS:]
        if not held:
            raise TickPreflightError(
                f"held sessions {bounds[0]} → {bounds[1]} are outside the "
                f"calendar; {_EXTEND}"
            )
        first = max(held[0] - VOLUME_RANK_LAG_SESSIONS, 0)
        context = calendar.sessions[first : held[-1] + 1]
        inputs = await _inputs(conn, entry, calendar, context)
    window = [calendar.sessions[i].session_date for i in held]
    complete = [d for d in window if inputs.statuses[d] is TickSessionStatus.COMPLETE]
    if not complete:
        reason = f"no complete session in {window[0]} → {window[-1]}"
        return tuple(ActiveLine(entry.product, r, reason=reason) for r in rules)
    return await asyncio.to_thread(_lines, entry.product, rules, inputs, complete[-1])


def _lines(
    product: str, rules: Sequence[RollRule], inputs: RollInputs, latest: date
) -> tuple[ActiveLine, ...]:
    return tuple(_line(product, rule, inputs, latest) for rule in rules)


def _line(product: str, rule: RollRule, inputs: RollInputs, latest: date) -> ActiveLine:
    try:
        return active_line(product, rule, inputs, latest)
    except RollInputError as exc:
        # One rule's broken invariant is that rule's line, not the others'.
        return ActiveLine(product, rule, error=str(exc))
