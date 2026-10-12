"""What ``mt data tick get`` reads: the session windows, in order (slice 230).

LLD 230 TD2, TD3, Data Flow. A :class:`GetPlan` is built before the first
row is read, so every output key but the rows is known up front (TD5).

- **Contract:** ``raw_symbol`` → one ``instrument_id`` from ``tick_definition``
  (an exchange symbol repeats every decade; two candidates refuse rather than
  guess). Its held sessions by default, or the calendar sessions of the range
  inside its validity window. No schedule, so nothing is unresolved.
- **Product:** 229's ``build_schedule`` under the rule, session by session.
  Unresolved sessions are listed, never filled from another rule or contract.

Either way, a planned session whose 225 verdict is not ``complete`` is still
read and is listed in ``incomplete``. No CLI import: 231 builds on this.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.active_contract import build_schedule, roll_conninfo
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn
from manta_trading.data.tick.roll_inputs import held_session_bounds
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import ActiveContract, Unresolved
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import (
    calendar_sessions,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_format import ts_text
from manta_trading.data.tick.tick_reads import TickWindow
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry

_DAY = timedelta(days=1)


class ContractNotFound(TickPreflightError):
    """No definition carries the raw symbol (in the range, when one is given)."""


class ContractAmbiguous(TickPreflightError):
    """More than one instrument carries the raw symbol in the range."""


@dataclass(frozen=True)
class ContractTarget:
    contract: str
    instrument_id: int


@dataclass(frozen=True)
class ProductTarget:
    product: str
    rule: RollRule


@dataclass(frozen=True)
class Incomplete:
    session_date: date
    status: TickSessionStatus


@dataclass(frozen=True)
class DisplaySegment:
    """A run of consecutive planned sessions served by one contract."""

    first_session: date
    last_session: date
    raw_symbol: str
    instrument_id: int


@dataclass(frozen=True)
class GetPlan:
    target: ContractTarget | ProductTarget
    start: date | None  # None only when nothing is held and no range was given
    end: date | None  # exclusive
    segments: tuple[TickWindow, ...]
    display: tuple[DisplaySegment, ...]
    unresolved: tuple[Unresolved, ...]
    incomplete: tuple[Incomplete, ...]
    definitions_as_of: date | None


@dataclass(frozen=True)
class _Candidate:
    instrument_id: int
    asset: str | None
    activation_ns: int
    expiration_ns: int


async def _sessions(
    calendar_url: str, product: str, start: date, end: date
) -> list[Session]:
    """The calendar sessions dated ``[start, end)``; a range past the calendar
    refuses naming ``mt data extend``."""
    found = await asyncio.to_thread(
        calendar_sessions,
        roll_conninfo(calendar_url),
        calendar_for_product(product),
        utc_midnight(start),
        utc_midnight(end),
    )
    return [s for s in found if start <= s.session_date < end]


async def _incomplete(
    conn: Conn, entry: TickUniverseEntry, sessions: Sequence[Session]
) -> tuple[Incomplete, ...]:
    verdicts = await session_verdicts(conn, entry, sessions)
    return tuple(
        Incomplete(v.session_date, v.status)
        for v in verdicts
        if v.status is not TickSessionStatus.COMPLETE
    )


def _window(session: Session, instrument_id: int, raw_symbol: str) -> TickWindow:
    return TickWindow(
        session.session_date,
        instrument_id,
        raw_symbol,
        utc_ns(session.open_utc),
        utc_ns(session.close_utc),
    )


def _check_range(start: date, end: date) -> None:
    if end <= start:
        raise TickPreflightError(
            f"the range {start} → {end} is empty (end is exclusive)"
        )


# -- contract ------------------------------------------------------------------


async def _candidates(
    conn: Conn, symbol: str, start: date | None, end: date | None
) -> list[_Candidate]:
    """Instruments carrying ``symbol`` whose validity meets the range (a
    session dated ``start`` opens the day before)."""
    lo = utc_ns(utc_midnight(start) - _DAY) if start is not None else None
    hi = utc_ns(utc_midnight(end)) if end is not None else None
    cursor = await conn.execute(
        "SELECT instrument_id, max(asset), min(activation_ns), max(expiration_ns)"
        " FROM tick_definition WHERE raw_symbol = %(symbol)s"
        " AND (%(hi)s::bigint IS NULL OR activation_ns <= %(hi)s)"
        " AND (%(lo)s::bigint IS NULL OR expiration_ns >= %(lo)s)"
        " GROUP BY instrument_id ORDER BY 4, 1",
        {"symbol": symbol, "lo": lo, "hi": hi},
    )
    return [_Candidate(*row) for row in await cursor.fetchall()]


def _one(symbol: str, found: list[_Candidate]) -> _Candidate:
    if not found:
        raise ContractNotFound(f"no definition held for contract {symbol}")
    if len(found) > 1:
        listed = ", ".join(
            f"{c.instrument_id} (expires {ts_text(c.expiration_ns)})" for c in found
        )
        raise ContractAmbiguous(
            f"contract {symbol} names {len(found)} instruments: {listed}; give "
            "--start/--end that separate them"
        )
    return found[0]


def _entry_for(candidate: _Candidate, symbol: str) -> TickUniverseEntry:
    for entry in TICK_UNIVERSE:
        if entry.product == candidate.asset:
            return entry
    raise TickPreflightError(
        f"contract {symbol} belongs to {candidate.asset}, which is not in the "
        "tick universe"
    )


async def _held(conn: Conn, instrument_id: int) -> tuple[date, date] | None:
    """First and last session holding records of the instrument, under
    current units."""
    cursor = await conn.execute(
        "SELECT min(l.session_date), max(l.session_date) FROM tick_ingest_ledger l"
        " JOIN tick_archive_unit u USING (unit_id)"
        f" WHERE l.instrument_id = %s AND l.record_count > 0 AND {COVERAGE_PREDICATE}",
        (instrument_id,),
    )
    row = await cursor.fetchone()
    return None if row is None or row[0] is None else (row[0], row[1])


async def contract_plan(
    conn: Conn,
    calendar_url: str,
    symbol: str,
    start: date | None,
    end: date | None,
) -> GetPlan:
    """The plan for ``--contract``; ``start``/``end`` default to its held
    sessions (TD2)."""
    candidate = _one(symbol, await _candidates(conn, symbol, start, end))
    entry = _entry_for(candidate, symbol)
    target = ContractTarget(symbol, candidate.instrument_id)
    if start is None or end is None:
        held = await _held(conn, candidate.instrument_id)
        if held is None:
            return GetPlan(target, start, end, (), (), (), (), None)
        start = start or held[0]
        end = end or held[1] + _DAY
    _check_range(start, end)
    sessions = [
        s
        for s in await _sessions(calendar_url, entry.product, start, end)
        if utc_ns(s.open_utc) <= candidate.expiration_ns
        and utc_ns(s.close_utc) >= candidate.activation_ns
    ]
    windows = tuple(_window(s, candidate.instrument_id, symbol) for s in sessions)
    display = (
        (
            DisplaySegment(
                sessions[0].session_date,
                sessions[-1].session_date,
                symbol,
                candidate.instrument_id,
            ),
        )
        if sessions
        else ()
    )
    incomplete = await _incomplete(conn, entry, sessions)
    return GetPlan(target, start, end, windows, display, (), incomplete, None)


# -- product -------------------------------------------------------------------


async def product_plan(
    conn: Conn,
    urls: tuple[str, str],
    entry: TickUniverseEntry,
    rule: RollRule,
    start: date | None,
    end: date | None,
) -> GetPlan:
    """The plan for ``--product`` under ``rule``; ``start``/``end`` default to
    the held sessions (TD2). ``urls`` is ``(tick_url, calendar_url)``:
    ``build_schedule`` opens its own connections (229 TD9). Its failures
    (``RollRangeTooLong``, a calendar refusal, an outage) propagate."""
    tick_url, calendar_url = urls
    target = ProductTarget(entry.product, rule)
    if start is None or end is None:
        held = await held_session_bounds(conn, shape_of(entry))
        if held is None:
            return GetPlan(target, start, end, (), (), (), (), None)
        start = start or held[0]
        end = end or held[1] + _DAY
    _check_range(start, end)
    schedule = await build_schedule(tick_url, calendar_url, entry, rule, start, end)
    answers = {
        a.session_date: a for a in schedule.answers if isinstance(a, ActiveContract)
    }
    sessions = [
        s
        for s in await _sessions(calendar_url, entry.product, start, end)
        if s.session_date in answers
    ]
    windows = tuple(
        _window(
            s, answers[s.session_date].instrument_id, answers[s.session_date].raw_symbol
        )
        for s in sessions
    )
    display = tuple(
        DisplaySegment(
            seg.first_session,
            seg.last_session,
            seg.contract.raw_symbol,
            seg.contract.instrument_id,
        )
        for seg in schedule.segments()
    )
    as_of = next(iter(answers.values())).definitions_as_of if answers else None
    incomplete = await _incomplete(conn, entry, sessions)
    return GetPlan(
        target, start, end, windows, display, schedule.unresolved, incomplete, as_of
    )
