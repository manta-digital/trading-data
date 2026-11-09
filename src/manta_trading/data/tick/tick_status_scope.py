"""The scope step of ``status``: sessions judged per family and their verdicts.

LLD 225 TD9; slice 302 D6. Sessions come from the production calendar (one
``sessions_between`` per product); a family's verdicts come from its current
units, the conditions and the calendar. ``status`` (``tick_status_build``) and
230's accounting line both call :func:`product_scope`, so their buckets are the
same function's.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import (
    FAMILY_LEDGER,
    SCHEMA_FAMILY,
    SchemaFamily,
)
from manta_trading.data.tick.session_days import days_touched, utc_midnight
from manta_trading.data.tick.status_reads import (
    Edge,
    calendar_sessions,
    session_verdicts,
    shape_of,
    shape_units,
)
from manta_trading.data.tick.tick_status import (
    SessionVerdict,
    TickSessionStatus,
    caught_up,
)
from manta_trading.data.tick.universe import TickUniverseEntry

#: Judges a family's sessions: ``session_verdicts`` unless a family's session
#: takes more days than its own (303: statistics are published a session later).
VerdictReader = Callable[
    [Conn, TickUniverseEntry, Sequence[Session], SchemaFamily],
    Awaitable[list[SessionVerdict]],
]


#: The families whose sessions ``session_verdicts`` judges.
_SESSION_VERDICT_FAMILIES = frozenset({SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M})


def verdicts_for(family: SchemaFamily) -> VerdictReader:
    """The one place a family's verdict reader is chosen (306 D3), so no caller
    pairs a family with a reader by hand. Statistics are judged by their own
    reader (``statistics_status``) and are not served by a plan yet."""
    if family not in _SESSION_VERDICT_FAMILIES:
        raise ValueError(f"no session verdict reader for family {family.value}")
    return session_verdicts


@dataclass(frozen=True)
class FamilyWant:
    """A family an entry wants, over its own range (end exclusive, None = edge).

    ``start`` is None only for a family nothing wants but a surface still reports
    (the tick tier of an untiered product: only what units touch)."""

    family: SchemaFamily
    start: date | None
    end: date | None


def family_wants(entry: TickUniverseEntry) -> list[FamilyWant]:
    """The families ``entry`` wants: its tier, then its stored history schemas.

    A history schema of a family with no ledger (``definition``) is a companion
    of the others, not a family of its own to report.
    """
    wants = []
    if entry.tier is not None and entry.start is not None:
        wants.append(FamilyWant(SchemaFamily.TICK_TIER, entry.start, entry.end))
    for want in entry.history:
        family = SCHEMA_FAMILY[want.schema]
        if family in FAMILY_LEDGER:
            wants.append(FamilyWant(family, want.start, want.end))
    return wants


def _wanted_end(end: date | None, edge: Edge | None) -> datetime | None:
    """``min(end, the dataset's available end)``; ``None`` when neither is known."""
    ends = [] if end is None else [utc_midnight(end)]
    ends += [] if edge is None else [edge.available_end]
    return min(ends) if ends else None


def overlaps(session: Session, wanted: tuple[datetime, datetime]) -> bool:
    """The session meets the wanted UTC window ``[start, end)``."""
    return session.close_utc > wanted[0] and session.open_utc < wanted[1]


async def _scope(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    wanted: tuple[datetime, datetime] | None,
    family: SchemaFamily,
) -> list[Session]:
    """Sessions in scope (TD9): the wanted range, plus every session a current
    unit of the family and shape touches."""
    held = set(await shape_units(conn, shape_of(entry), family))
    bounds = [utc_midnight(d) for d in held]
    bounds += [utc_midnight(d + timedelta(days=1)) for d in held]
    bounds += list(wanted or ())
    if not bounds:
        return []
    found = await asyncio.to_thread(
        calendar_sessions,
        calendar_url,
        calendar_for_product(entry.product),
        min(bounds),
        max(bounds),
    )

    def in_scope(session: Session) -> bool:
        if held.intersection(days_touched(session)):
            return True
        return wanted is not None and overlaps(session, wanted)

    return [session for session in found if in_scope(session)]


@dataclass(frozen=True)
class ProductScope:
    """The sessions ``status`` judges a product on, and their verdicts (TD9).

    ``sessions`` is the wanted range plus every session a current tier unit
    touches; ``wanted`` is the UTC window, ``None`` for an untiered product.
    """

    wanted_start: date | None
    wanted_end: datetime | None
    wanted: tuple[datetime, datetime] | None
    sessions: list[Session]
    verdicts: list[SessionVerdict]


async def family_scope(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    edge: Edge | None,
    family: SchemaFamily,
    wanted_start: date | None,
    wanted_end_day: date | None,
    verdicts_of: VerdictReader = session_verdicts,
) -> ProductScope:
    """The bucket step for one family over its wanted range (``None`` start: no
    range wanted, only what units touch). ``verdicts_of`` judges the sessions."""
    wanted_end = None if wanted_start is None else _wanted_end(wanted_end_day, edge)
    wanted = (
        None
        if wanted_start is None or wanted_end is None
        else (utc_midnight(wanted_start), wanted_end)
    )
    sessions = await _scope(conn, calendar_url, entry, wanted, family)
    verdicts = await verdicts_of(conn, entry, sessions, family)
    return ProductScope(wanted_start, wanted_end, wanted, sessions, verdicts)


async def product_scope(
    conn: Conn, calendar_url: str, entry: TickUniverseEntry, edge: Edge | None
) -> ProductScope:
    """The tick tier's bucket step: ``status`` and 230's accounting line both
    call it."""
    wanted_start = entry.start if entry.tier is not None else None
    return await family_scope(
        conn,
        calendar_url,
        entry,
        edge,
        SchemaFamily.TICK_TIER,
        wanted_start,
        entry.end,
    )


def judged(scope: ProductScope, source_edge: datetime | None) -> list[SessionVerdict]:
    """TD9 (226), 305 D2: caught up is judged over the sessions that overlap the
    family's wanted range **and** close at or before its source edge. Sessions
    in scope because other units touch them neither help nor hurt it; a session
    whose publication window is still open is ``pending``, not judged. No edge:
    nothing is judged (the caller reports ``caught_up`` as unknown)."""
    wanted = scope.wanted
    if wanted is None or source_edge is None:
        return []
    return [
        v
        for session, v in zip(scope.sessions, scope.verdicts, strict=True)
        if overlaps(session, wanted) and session.close_utc <= source_edge
    ]


def scope_caught_up(scope: ProductScope, source_edge: datetime | None) -> bool | None:
    """225's rule over :func:`judged`; ``None`` when no range is wanted or no
    edge is known."""
    wanted = scope.wanted_start is not None and source_edge is not None
    return caught_up(judged(scope, source_edge), wanted=wanted)


def bucket_counts(verdicts: Sequence[SessionVerdict]) -> dict[str, int]:
    """Sessions per status, every status present (zeros included)."""
    counted = Counter(v.status.value for v in verdicts)
    return {s.value: counted.get(s.value, 0) for s in TickSessionStatus}
