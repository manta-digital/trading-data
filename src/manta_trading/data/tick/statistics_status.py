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
    UNCONDITIONAL_STAT_TYPES,
)
from manta_trading.data.tick.status_reads import (
    SHAPE_WHERE,
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
    """Per judged session, the UTC days it and its next
    ``STATISTICS_PUBLICATION_SESSIONS`` sessions touch.

    A day no session touches (a Saturday) is not in the window: nothing is
    published or wanted on it, so it has neither a unit nor a condition.
    """
    position = {s.session_date: i for i, s in enumerate(calendar)}
    windows = []
    for session in judged:
        start = position[session.session_date]
        span = calendar[start : start + STATISTICS_PUBLICATION_SESSIONS + 1]
        windows.append(sorted({day for s in span for day in days_touched(s)}))
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


#: The outrights listed for each session, as the CTEs two statements share
#: (305 D10): ``complete`` is the sessions' dates and UTC bounds, ``symbols``
#: each outright's first symbol, ``listed`` the (session, outright) pairs whose
#: definition window holds the whole session. A contract that expires (or first
#: lists) inside the session is not listed for it: nothing is required of it.
_LISTING = """
complete (session_date, open_ns, close_ns) AS (
    SELECT * FROM unnest(
        %(dates)s::date[], %(opens)s::bigint[], %(closes)s::bigint[])
),
symbols AS (
    SELECT DISTINCT ON (instrument_id) instrument_id, raw_symbol
    FROM tick_definition
    WHERE asset = %(product)s AND instrument_class = %(outright)s
    ORDER BY instrument_id, activation_ns
),
listed AS (
    SELECT DISTINCT c.session_date, d.instrument_id
    FROM complete c JOIN tick_definition d
      ON d.asset = %(product)s AND d.instrument_class = %(outright)s
     AND d.activation_ns <= c.open_ns AND d.expiration_ns >= c.close_ns
)
"""


def _session_params(product: str, sessions: Sequence[Session]) -> dict[str, object]:
    return {
        "dates": [s.session_date for s in sessions],
        "opens": [utc_ns(s.open_utc) for s in sessions],
        "closes": [utc_ns(s.close_utc) for s in sessions],
        "product": product,
        "outright": OUTRIGHT_CLASS,
    }


async def listed_outrights(
    conn: Conn, product: str, sessions: Sequence[Session]
) -> dict[date, list[tuple[int, str | None]]]:
    """Per session, its listed outrights as (instrument id, symbol), by id."""
    cursor = await conn.execute(
        f"WITH {_LISTING} SELECT l.session_date, l.instrument_id, s.raw_symbol"
        " FROM listed l JOIN symbols s USING (instrument_id)"
        " ORDER BY l.session_date, l.instrument_id",
        _session_params(product, sessions),
    )
    found: dict[date, list[tuple[int, str | None]]] = {}
    for session_date, instrument_id, symbol in await cursor.fetchall():
        found.setdefault(session_date, []).append((instrument_id, symbol))
    return found


#: Findings in one statement (305 D10): a listed outright (its definition
#: window holds the whole session) missing a type it is owed. ``held`` is the
#: ledger's (instrument, session, type) triples over the complete sessions'
#: range; ``reported`` the types an instrument reported anywhere in it. What a
#: contract is owed (``owed``) is ``always`` plus the required types it
#: reported; the anti-join against ``held`` leaves what is missing. Set-based:
#: the per-session version took about 6 s of Python at 16 years (305's
#: measurement).
_FINDINGS_SQL = f"""
WITH {_LISTING},
held AS (
    SELECT l.instrument_id, l.session_date, l.stat_type
    FROM {ledger_units(SchemaFamily.STATISTICS)}
    WHERE {SHAPE_WHERE} AND l.record_count > 0
      AND l.session_date BETWEEN %(first)s AND %(last)s
    GROUP BY 1, 2, 3
),
reported AS (SELECT DISTINCT instrument_id, stat_type FROM held),
owed AS (
    SELECT instrument_id, stat_type FROM reported
    WHERE stat_type = ANY(%(required)s::smallint[])
    UNION
    SELECT s.instrument_id, a.stat_type
    FROM symbols s CROSS JOIN unnest(%(always)s::smallint[]) AS a (stat_type)
),
needed AS (
    SELECT l.session_date, l.instrument_id, o.stat_type
    FROM listed l JOIN owed o ON o.instrument_id = l.instrument_id
)
SELECT n.session_date, n.instrument_id, s.raw_symbol,
       array_agg(n.stat_type ORDER BY n.stat_type)
FROM needed n
JOIN symbols s ON s.instrument_id = n.instrument_id
LEFT JOIN held h ON h.instrument_id = n.instrument_id
 AND h.session_date = n.session_date AND h.stat_type = n.stat_type
WHERE h.instrument_id IS NULL
GROUP BY n.session_date, n.instrument_id, s.raw_symbol
ORDER BY n.session_date, n.instrument_id
"""


async def content_findings(
    conn: Conn,
    entry: TickUniverseEntry,
    sessions: Sequence[Session],
    verdicts: Sequence[SessionVerdict],
) -> tuple[ContentFinding, ...]:
    """Gaps in the load-complete sessions: a listed outright (its definition
    window holds the whole session) missing a required type. A contract expiring
    inside the session is exempt; spreads are never required. Cleared volume and
    open interest are required of a contract only if it reported them somewhere
    in the judged range (``UNCONDITIONAL_STAT_TYPES``)."""
    complete = [
        session
        for session, v in zip(sessions, verdicts, strict=True)
        if v.status is TickSessionStatus.COMPLETE
    ]
    if not complete:
        return ()
    params = (
        shape_of(entry).params(SchemaFamily.STATISTICS)
        | _session_params(entry.product, complete)
        | {
            "first": min(s.session_date for s in complete),
            "last": max(s.session_date for s in complete),
            "always": sorted(int(t) for t in UNCONDITIONAL_STAT_TYPES),
            "required": sorted(int(t) for t in REQUIRED_STAT_TYPES),
        }
    )
    cursor = await conn.execute(_FINDINGS_SQL, params)
    return tuple(
        ContentFinding(session_date, instrument_id, symbol, tuple(missing))
        for session_date, instrument_id, symbol, missing in await cursor.fetchall()
    )
