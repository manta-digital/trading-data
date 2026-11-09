"""Which planned sessions ``mt data tick parity`` compares (306 D7).

The plan (``family=TICK_TIER``) supplies the windows and the tick verdicts. The
bar family's verdicts come from ``verdicts_for(BAR_1M)`` over the same sessions
on the same connection, so inside one snapshot. A session is compared only when
both are ``complete``; every other planned session is listed as skipped with
both statuses, so a partial load is never reported as a disagreement.
"""

from __future__ import annotations

from dataclasses import dataclass

from manta_trading.data.tick.bar_parity_report import SkippedSession
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan, sessions_dated
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_reads import TickWindow
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_scope import verdicts_for
from manta_trading.data.tick.universe import TICK_UNIVERSE, TickUniverseEntry


@dataclass(frozen=True)
class ParityInputs:
    compared: tuple[TickWindow, ...]
    skipped: tuple[SkippedSession, ...]


async def universe_entry_of(conn: Conn, plan: GetPlan) -> TickUniverseEntry:
    """The universe entry the plan's target belongs to: a product names it, a
    contract names it through its definition's asset."""
    target = plan.target
    if isinstance(target, ContractTarget):
        cursor = await conn.execute(
            "SELECT max(asset) FROM tick_definition WHERE instrument_id = %s",
            (target.instrument_id,),
        )
        row = await cursor.fetchone()
        product = None if row is None else row[0]
    else:
        product = target.product
    for entry in TICK_UNIVERSE:
        if entry.product == product:
            return entry
    raise TickPreflightError(f"{product} is not in the tick universe")


async def parity_inputs(conn: Conn, calendar_url: str, plan: GetPlan) -> ParityInputs:
    """Split ``plan``'s windows into compared and skipped sessions."""
    if not plan.segments or plan.start is None or plan.end is None:
        return ParityInputs((), ())
    entry = await universe_entry_of(conn, plan)
    planned = {w.session_date for w in plan.segments}
    sessions = [
        s
        for s in await sessions_dated(calendar_url, entry.product, plan.start, plan.end)
        if s.session_date in planned
    ]
    bar_verdicts = await verdicts_for(SchemaFamily.BAR_1M)(
        conn, entry, sessions, SchemaFamily.BAR_1M
    )
    bar = {v.session_date: v.status for v in bar_verdicts}
    tick_incomplete = {i.session_date: i.status for i in plan.incomplete}
    compared: list[TickWindow] = []
    skipped: list[SkippedSession] = []
    for window in plan.segments:
        day = window.session_date
        tick_status = tick_incomplete.get(day, TickSessionStatus.COMPLETE)
        bar_status = bar[day]
        if tick_status is bar_status is TickSessionStatus.COMPLETE:
            compared.append(window)
        else:
            skipped.append(SkippedSession(day, tick_status, bar_status))
    return ParityInputs(tuple(compared), tuple(skipped))
