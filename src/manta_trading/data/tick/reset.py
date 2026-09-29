"""``mt data tick reset``: the manual reopen, at minute parity (223).

LLD 224 Technical Decision 8 (reset). This module owns the classification
and calls ``manifest_repo``'s per-unit compare-and-set primitives:

- ``RETRY_EXHAUSTED`` and not reopened → ``UNKNOWN``, no attempts, no reason:
  retried where it stands (units 225's ingest exhausts included);
- ``PROVIDER_HOLE`` and not reopened → ``reopened_at = now``: the planner
  wants the day again;
- anything else → unchanged, and listed as such; an unknown id → not found.

With ``ALL`` the classification runs over every unit, and only the units it
changes are listed.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.manifest_reads import UnitRow, all_unit_ids, units_by_id
from manta_trading.data.tick.manifest_repo import reopen_hole, reset_exhausted
from manta_trading.data.tick.run_context import TickRun

ALL: Literal["all"] = "all"


class ResetAction(StrEnum):
    RESET = "reset"
    REOPENED = "reopened"
    UNCHANGED = "unchanged"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class ResetChange:
    """One unit before and after; ``before``/``after`` are ``None`` if not found."""

    unit_id: int
    action: ResetAction
    before: UnitRow | None
    after: UnitRow | None


def classify(unit: UnitRow) -> ResetAction:
    if unit.reopened_at is not None:
        return ResetAction.UNCHANGED
    if unit.fetch_status is FetchStatus.RETRY_EXHAUSTED:
        return ResetAction.RESET
    if unit.fetch_status is FetchStatus.PROVIDER_HOLE:
        return ResetAction.REOPENED
    return ResetAction.UNCHANGED


async def _apply(run: TickRun, unit: UnitRow, action: ResetAction) -> None:
    if action is ResetAction.RESET:
        await reset_exhausted(run.conn, unit.unit_id)
    elif action is ResetAction.REOPENED:
        await reopen_hole(run.conn, unit.unit_id, run.clock())


async def reset_units(
    run: TickRun, unit_ids: Sequence[int] | Literal["all"]
) -> list[ResetChange]:
    """Reset or reopen the named units (or every eligible unit for ``ALL``)."""
    if isinstance(unit_ids, str):
        every, wanted = True, await all_unit_ids(run.conn)
    else:
        every, wanted = False, list(unit_ids)
    found = {unit.unit_id: unit for unit in await units_by_id(run.conn, wanted)}
    changes = []
    for unit_id in wanted:
        unit = found.get(unit_id)
        if unit is None:
            changes.append(ResetChange(unit_id, ResetAction.NOT_FOUND, None, None))
            continue
        action = classify(unit)
        if every and action is ResetAction.UNCHANGED:
            continue
        await _apply(run, unit, action)
        changes.append(ResetChange(unit_id, action, unit, None))
    after = {u.unit_id: u for u in await units_by_id(run.conn, list(found))}
    return [
        ResetChange(c.unit_id, c.action, c.before, after.get(c.unit_id))
        for c in changes
    ]
