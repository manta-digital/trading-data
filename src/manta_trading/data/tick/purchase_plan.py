"""Build one run's purchase plan: wants, costs, and both guards' verdicts (224).

LLD 224 TD4–TD7 and the purchase phase's data flow. Everything here is free:
the calendar read, the manifest reads, the provider's ``cost``,
``billable_size`` and ``batch_jobs_since``. Nothing is submitted. The result is
what :mod:`~manta_trading.data.tick.purchase_phase` submits, or the reason it
does not.

Planning needs the calendar (TD6), read only when something can be wanted, so
a run with nothing to buy never touches the production database.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from manta_trading.data.tick.adopt_files import FreeBytes, free_bytes
from manta_trading.data.tick.availability import planning_state
from manta_trading.data.tick.constants import (
    CME_DATASET,
    TICK_SPEND_WINDOW,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import (
    covered_keys,
    owned_tier_days,
    resubmittable_requests,
    trailing_spend_rows,
    units_of_request,
)
from manta_trading.data.tick.planner import (
    OwnedTierDay,
    Plan,
    PlanInputs,
    PlannedRequest,
    plan_purchases,
)
from manta_trading.data.tick.provider import BatchJob
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.spend_guard import (
    ListedJob,
    PlannedCost,
    SpaceVerdict,
    SpendVerdict,
    TrailingRow,
    UnheldJob,
    evaluate_space,
    evaluate_spend,
    unheld_listed,
)
from manta_trading.data.tick.tick_calendar import (
    calendar_url,
    product_of_shape,
    product_session_days,
)
from manta_trading.data.tick.universe import TICK_UNIVERSE

Window = tuple[date | None, date | None]


@dataclass(frozen=True)
class PlanItem:
    """One request to submit: a new entry, or ``resubmit_of`` (the request id
    of a row ``reset`` put back to *requested* with no attempts)."""

    planned: PlannedRequest
    cost: Decimal
    billable_bytes: int
    resubmit_of: int | None = None


@dataclass(frozen=True)
class PurchasePlan:
    plan: Plan
    items: tuple[PlanItem, ...]
    spend: SpendVerdict | None
    space: SpaceVerdict | None
    trailing_total: Decimal

    @property
    def has_wants(self) -> bool:
        return bool(self.items)


async def _sessions(
    run: TickRun,
    owned: Sequence[OwnedTierDay],
    edges: Mapping[str, date],
    window: Window,
) -> dict[str, list[date]]:
    """Session days per product over the span its wants can touch."""
    spans: dict[str, tuple[date, date]] = {}

    def widen(product: str, start: date, end: date) -> None:
        lo, hi = spans.get(product, (start, end))
        spans[product] = (min(lo, start), max(hi, end))

    for entry in TICK_UNIVERSE:
        end = entry.end if entry.end is not None else edges.get(CME_DATASET)
        if entry.tier is not None and entry.start is not None and end is not None:
            widen(entry.product, entry.start, end)
    for day in owned:
        widen(day.product, day.day, day.day + timedelta(days=1))
    if not spans:
        return {}
    url = calendar_url(run.settings)
    sessions: dict[str, list[date]] = {}
    for product, (lo, hi) in spans.items():
        lo = max(lo, window[0]) if window[0] else lo
        hi = min(hi, window[1]) if window[1] else hi
        if lo < hi:
            sessions[product] = await asyncio.to_thread(
                product_session_days, url, product, lo, hi
            )
    return sessions


def _owned(keys: Sequence[Any]) -> list[OwnedTierDay]:
    return [
        OwnedTierDay(
            product_of_shape(key.stype_in, key.symbols),
            key.dataset,
            key.schema,
            key.symbols,
            key.stype_in,
            key.day,
        )
        for key in keys
    ]


async def _resubmits(run: TickRun) -> list[PlanItem]:
    """Rows ``reset`` put back to *requested*: submitted again after reconcile
    searched the job list once more (TD9). Costed at the recorded estimate."""
    items = []
    for row in await resubmittable_requests(run.conn):
        request = row.request
        days = tuple(
            unit.unit_date
            for unit in await units_of_request(run.conn, row.request_id)
            if unit.state is UnitState.REQUESTED
        )
        planned = PlannedRequest(
            product_of_shape(request.stype_in, request.symbols), request, days
        )
        size = await asyncio.to_thread(run.provider.billable_size, request)
        items.append(PlanItem(planned, row.estimated_cost_usd, size, row.request_id))
    return items


async def _costed(run: TickRun, planned: Sequence[PlannedRequest]) -> list[PlanItem]:
    items = []
    for entry in planned:
        cost = await asyncio.to_thread(run.provider.cost, entry.request)
        size = await asyncio.to_thread(run.provider.billable_size, entry.request)
        items.append(PlanItem(entry, cost, size))
    return items


async def _unheld(run: TickRun, trailing: Sequence[TrailingRow]) -> list[UnheldJob]:
    """Provider jobs from the spend window that no manifest row holds."""
    since = run.clock() - TICK_SPEND_WINDOW
    jobs: tuple[BatchJob, ...] = await asyncio.to_thread(
        run.provider.batch_jobs_since, since
    )
    by_id = {job.job_id: job for job in jobs}
    listed = [ListedJob(j.job_id, j.ts_received, j.cost_usd) for j in jobs]
    unheld = []
    for job in unheld_listed(listed, trailing):
        cost = job.cost
        if cost is None:  # not yet priced: count it at its request's cost
            cost = await asyncio.to_thread(run.provider.cost, by_id[job.job_id].request)
        unheld.append(UnheldJob(job.job_id, job.at, cost))
    return unheld


async def build_plan(
    run: TickRun, window: Window, estimate_only: bool, free: FreeBytes = free_bytes
) -> PurchasePlan:
    """Plan, cost and guard one run. Free calls only."""
    conn = run.conn
    edges, conditions = await planning_state(conn)
    owned = _owned(await owned_tier_days(conn))
    sessions = await _sessions(run, owned, edges, window)
    plan = plan_purchases(
        PlanInputs(
            universe=TICK_UNIVERSE,
            owned=owned,
            covered=await covered_keys(conn),
            sessions=sessions,
            conditions=conditions,
            edge_end=edges,
            window_start=window[0],
            window_end=window[1],
        )
    )
    items = [*await _resubmits(run), *await _costed(run, plan.requests)]
    if not items:
        return PurchasePlan(plan, (), None, None, Decimal(0))
    now = run.clock()
    trailing = await trailing_spend_rows(conn, now - TICK_SPEND_WINDOW)
    unheld = await _unheld(run, trailing)
    spend = evaluate_spend(
        [PlannedCost(i.cost, counted=i.resubmit_of is not None) for i in items],
        trailing,
        unheld,
        run.settings.tick_spend_ceiling_usd,
        run.settings.tick_spend_30d_ceiling_usd,
        now,
        estimate_only,
    )
    space = evaluate_space(sum(i.billable_bytes for i in items), free(run.archive_root))
    return PurchasePlan(
        plan, tuple(items), spend, space, sum((t.cost for t in trailing), Decimal(0))
    )


def _tally(counts: Mapping[tuple[str, Any], int]) -> dict[str, int]:
    return {f"{product}/{schema}": n for (product, schema), n in counts.items()}


def plan_summary(purchase: PurchasePlan) -> dict[str, Any]:
    """The JSON-serializable purchase summary (LLD API Contracts)."""
    plan, spend, space = purchase.plan, purchase.spend, purchase.space
    return {
        "wanted_days": _tally(plan.wanted),
        "pending_days": _tally(plan.pending),
        "missing_days": _tally(plan.missing),
        "requests": [
            {
                "schema": item.planned.schema.value,
                "start": item.planned.request.start.isoformat(),
                "end": item.planned.request.end.isoformat(),
                "days": len(item.planned.days),
                "cost_usd": str(item.cost),
                "resubmit": item.resubmit_of is not None,
            }
            for item in purchase.items
        ],
        "planned_usd": str(spend.planned_total) if spend else "0",
        "trailing_30d_usd": str(purchase.trailing_total),
        "unheld_jobs": [
            {"job_id": job.job_id, "cost_usd": str(job.cost)}
            for job in (spend.unheld_jobs if spend else ())
        ],
        "per_pass_ceiling_usd": _money(spend.per_pass_ceiling if spend else None),
        "cap_30d_usd": _money(spend.cap_30d if spend else None),
        "verdict": spend.status.value if spend else "nothing_to_buy",
        "reasons": [*(spend.reasons if spend else ()), *_space_reason(space)],
    }


def _money(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _space_reason(space: SpaceVerdict | None) -> list[str]:
    return [space.reason] if space is not None and space.reason else []
