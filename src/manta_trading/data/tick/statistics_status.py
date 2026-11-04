"""The statistics family's verdict: load, then content (slice 303, D7).

CME publishes session D's statistics during the following session, so D's
statistics units are not all there until that session has been loaded. D's
*window* is the UTC days from D's first day through the close day of the session
``STATISTICS_PUBLICATION_SESSIONS`` after D, read from the calendar; its status
is 225's ``classify_session`` over the statistics units of those days (a
``missing`` day with no unit is ``provider_hole``). That is load-complete only.

A load-complete session is then checked for content: every listed outright must
have ledger records for each ``REQUIRED_STAT_TYPES``. Reads only the manifest,
``tick_statistics_ledger`` and ``tick_definition``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import (
    CME_DATASET,
    OUTRIGHT_CLASS,
    calendar_for_product,
)
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.session_days import days_touched
from manta_trading.data.tick.statistics_constants import (
    REQUIRED_STAT_TYPES,
    STATISTICS_PUBLICATION_SESSIONS,
)
from manta_trading.data.tick.status_reads import (
    SHAPE_WHERE,
    Shape,
    calendar_sessions,
    conditions,
    ledger_units,
    shape_of,
    shape_units,
)
from manta_trading.data.tick.tick_status import (
    DayFacts,
    SessionVerdict,
    TickSessionStatus,
    day_unit,
    verdict,
)
from manta_trading.data.tick.tick_status_scope import VerdictReader
from manta_trading.data.tick.universe import TickUniverseEntry

#: How far past the last judged session each calendar read reaches, widened by
#: this step until the publication session is in it (a holiday run is shorter).
_LOOKAHEAD_STEP = timedelta(days=7)


@dataclass(frozen=True)
class ContentFinding:
    """A load-complete session in which a listed outright lacks required types."""

    session_date: date
    instrument_id: int
    raw_symbol: str | None
    missing_stat_types: tuple[int, ...]


def _calendar_window(
    url: str, calendar_id: str, sessions: Sequence[Session]
) -> list[Session]:
    """Blocking: the calendar's sessions from the first judged session through
    the last one's publication sessions."""
    start, last = sessions[0].open_utc, sessions[-1]
    reach = last.close_utc + _LOOKAHEAD_STEP
    while True:
        found = calendar_sessions(url, calendar_id, start, reach)
        after = [s for s in found if s.open_utc > last.close_utc]
        if len(after) >= STATISTICS_PUBLICATION_SESSIONS:
            return found
        reach += _LOOKAHEAD_STEP


def publication_windows(
    judged: Sequence[Session], calendar: Sequence[Session]
) -> list[list[date]]:
    """Per judged session, its UTC days: its own first day through the close day
    of the session ``STATISTICS_PUBLICATION_SESSIONS`` later."""
    position = {s.session_date: i for i, s in enumerate(calendar)}
    windows = []
    for session in judged:
        later = calendar[
            position[session.session_date] + STATISTICS_PUBLICATION_SESSIONS
        ]
        first, last = days_touched(session)[0], days_touched(later)[-1]
        windows.append(
            [first + timedelta(days=n) for n in range((last - first).days + 1)]
        )
    return windows


def statistics_verdicts(calendar_url: str) -> VerdictReader:
    """The verdict reader for the ``STATISTICS`` family (see the module doc)."""

    async def read(
        conn: Conn,
        entry: TickUniverseEntry,
        sessions: Sequence[Session],
        family: SchemaFamily,
    ) -> list[SessionVerdict]:
        if not sessions:
            return []
        calendar = await asyncio.to_thread(
            _calendar_window,
            calendar_url,
            calendar_for_product(entry.product),
            sessions,
        )
        windows = publication_windows(sessions, calendar)
        units = await shape_units(conn, shape_of(entry), family)
        days = [day for window in windows for day in window]
        held = await conditions(conn, CME_DATASET, min(days), max(days))
        return [
            verdict(
                session.session_date,
                [
                    DayFacts(day, day_unit(units.get(day, []), family), held.get(day))
                    for day in window
                ],
            )
            for session, window in zip(sessions, windows, strict=True)
        ]

    return read


async def outrights(
    conn: Conn, product: str
) -> dict[int, tuple[str | None, list[tuple[int, int]]]]:
    """Per outright: its symbol and every definition window (activation, expiration)."""
    cursor = await conn.execute(
        "SELECT instrument_id, raw_symbol, activation_ns, expiration_ns"
        " FROM tick_definition WHERE asset = %s AND instrument_class = %s"
        " ORDER BY instrument_id, activation_ns",
        (product, OUTRIGHT_CLASS),
    )
    found: dict[int, tuple[str | None, list[tuple[int, int]]]] = {}
    for instrument_id, symbol, activation, expiration in await cursor.fetchall():
        found.setdefault(instrument_id, (symbol, []))[1].append(
            (activation, expiration)
        )
    return found


def listed_instruments(
    definitions: dict[int, tuple[str | None, list[tuple[int, int]]]], session: Session
) -> list[int]:
    """The outrights whose definition window holds the whole session, by id.

    A contract that expires (or first lists) inside the session is not listed
    for it: nothing is required of it.
    """
    opened, closed = utc_ns(session.open_utc), utc_ns(session.close_utc)
    return [
        instrument_id
        for instrument_id, (_, windows) in sorted(definitions.items())
        if any(a <= opened and e >= closed for a, e in windows)
    ]


async def _held_types(
    conn: Conn, shape: Shape, first: date, last: date
) -> dict[tuple[int, date], set[int]]:
    """The stat types each (instrument, session) has ledger records for."""
    cursor = await conn.execute(
        "SELECT l.instrument_id, l.session_date, l.stat_type"
        f" FROM {ledger_units(SchemaFamily.STATISTICS)} WHERE {SHAPE_WHERE}"
        " AND l.record_count > 0 AND l.session_date BETWEEN %(first)s AND %(last)s"
        " GROUP BY 1, 2, 3",
        shape.params(SchemaFamily.STATISTICS) | {"first": first, "last": last},
    )
    held: dict[tuple[int, date], set[int]] = {}
    for instrument_id, session_date, stat_type in await cursor.fetchall():
        held.setdefault((instrument_id, session_date), set()).add(stat_type)
    return held


async def content_findings(
    conn: Conn,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
    verdicts: Sequence[SessionVerdict],
) -> tuple[ContentFinding, ...]:
    """Gaps in the load-complete sessions: a listed outright (its definition
    window holds the whole session) missing a required type. A contract expiring
    inside the session is exempt; spreads are never required."""
    complete = [
        session
        for session, v in zip(sessions, verdicts, strict=True)
        if v.status is TickSessionStatus.COMPLETE
    ]
    if not complete:
        return ()
    listed_for = await outrights(conn, entry.product)
    held = await _held_types(
        conn,
        shape_of(entry),
        min(s.session_date for s in complete),
        max(s.session_date for s in complete),
    )
    required = {int(stat_type) for stat_type in REQUIRED_STAT_TYPES}
    findings = []
    for session in complete:
        for instrument_id in listed_instruments(listed_for, session):
            symbol = listed_for[instrument_id][0]
            missing = required - held.get((instrument_id, session.session_date), set())
            if missing:
                findings.append(
                    ContentFinding(
                        session.session_date,
                        instrument_id,
                        symbol,
                        tuple(sorted(missing)),
                    )
                )
    return tuple(findings)
