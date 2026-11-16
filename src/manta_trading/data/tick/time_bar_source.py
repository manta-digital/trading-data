"""Which source each planned session's time bars come from (slice 309).

The rule, per session:

1. a provider schema, when one can make the size (``provider_schema_for``) and
   the session is held complete in it;
2. else ``ticks``, from whatever ticks are loaded, unless no ticks are held for
   the session at all (tick verdict ``missing`` or no ingest-ledger row);
3. else nothing (``None``): the session is not read, and it is already listed
   in the plan's ``incomplete``.

The plan is built over the tick tier, so its ``incomplete`` carries tick
verdicts. ``served_plan`` drops the provider-served sessions from that list
(they are held), and ``source_runs`` groups the sessions that returned bars
into runs of one source for the response.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import date

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.get_plan import GetPlan, sessions_dated
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SCHEMA_FAMILY, SchemaFamily
from manta_trading.data.tick.status_reads import ledger_totals, shape_of
from manta_trading.data.tick.tick_bar_reads import plan_entry
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_scope import verdicts_for
from manta_trading.data.tick.time_bar_size import BarSize, provider_schema_for

#: Per planned session date: ``TICKS_SOURCE``, a provider schema value, or
#: ``None`` (not read).
Sources = Mapping[date, str | None]


async def _provider_held(
    conn: Conn, calendar_url: str, plan: GetPlan, family: SchemaFamily
) -> set[date]:
    """The planned sessions whose ``family`` verdict is ``complete``."""
    assert plan.start is not None and plan.end is not None  # a plan with segments
    entry = plan_entry(plan)
    planned = {w.session_date for w in plan.segments}
    sessions = [
        s
        for s in await sessions_dated(calendar_url, entry.product, plan.start, plan.end)
        if s.session_date in planned
    ]
    verdicts = await verdicts_for(family)(conn, entry, sessions, family)
    return {v.session_date for v in verdicts if v.status is TickSessionStatus.COMPLETE}


async def choose_sources(
    conn: Conn, calendar_url: str, plan: GetPlan, size: BarSize
) -> dict[date, str | None]:
    """The source of each planned session, in plan order (a plan has one
    window per session)."""
    if not plan.segments:
        return {}
    schema = provider_schema_for(size)
    held: set[date] = set()
    if schema is not None:
        held = await _provider_held(conn, calendar_url, plan, SCHEMA_FAMILY[schema])
    first, last = plan.segments[0].session_date, plan.segments[-1].session_date
    ledger = await ledger_totals(
        conn, shape_of(plan_entry(plan)), SchemaFamily.TICK_TIER, first, last
    )
    missing = {
        i.session_date for i in plan.incomplete if i.status is TickSessionStatus.MISSING
    }
    sources: dict[date, str | None] = {}
    for window in plan.segments:
        day = window.session_date
        if schema is not None and day in held:
            sources[day] = schema.value
        elif day in missing or (window.instrument_id, day) not in ledger:
            sources[day] = None
        else:
            sources[day] = TICKS_SOURCE
    return sources


def _provider(source: str | None) -> bool:
    return source is not None and source != TICKS_SOURCE


def served_plan(plan: GetPlan, sources: Sources) -> GetPlan:
    """``plan`` with the provider-served sessions out of ``incomplete``."""
    return replace(
        plan,
        incomplete=tuple(
            i for i in plan.incomplete if not _provider(sources.get(i.session_date))
        ),
    )


@dataclass(frozen=True)
class SourceRun:
    """Consecutive planned sessions that returned bars from one source."""

    first_session: date
    last_session: date
    source: str


def source_runs(
    plan: GetPlan, sources: Sources, returned: Collection[date]
) -> list[SourceRun]:
    """The runs covering exactly the sessions in ``returned``, in plan order;
    a planned session that returned no bars ends a run."""
    runs: list[SourceRun] = []
    open_run: SourceRun | None = None
    for window in plan.segments:
        day, source = window.session_date, sources.get(window.session_date)
        if day not in returned or source is None:
            open_run = None
            continue
        if open_run is not None and open_run.source == source:
            open_run = runs[-1] = replace(open_run, last_session=day)
        else:
            open_run = SourceRun(day, day, source)
            runs.append(open_run)
    return runs
