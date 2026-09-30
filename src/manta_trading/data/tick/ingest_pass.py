"""The ingest pass: select, plan, load on worker threads, record (slice 225).

LLD 225 TD2, TD4, TD8. :class:`IngestPhase` is the one phase of a
``TickPass[TickStore]``. For each selected unit, earliest day first, the loop
builds its plan (one at a time; the calendar call runs in a thread) and hands
it to :func:`~manta_trading.data.tick.ingest_worker.ingest_unit` on a thread,
at most ``workers`` at once. A unit whose check fails is recorded on the run
connection as ``RETRY_EXHAUSTED`` (deterministic: the file, definitions and
calendar are fixed inputs), and the outcome is ``partial``.

**Abort.** A plan or worker that raises (a lost or hung database, an
unreachable calendar, a defect) stops new units starting. Every in-flight
worker is awaited (``gather(..., return_exceptions=True)``); units that
committed meanwhile are in the summary. Only then does the first exception
reach ``run_phase``. Nothing is recorded for a unit during an abort: a unit
that did not commit is still *verified* and open, and the next run retries it.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from manta_trading.data.tick.constants import UnitState
from manta_trading.data.tick.ingest_plan import Calendars, UnitIngestPlan, build_plan
from manta_trading.data.tick.ingest_records import UnitCheckFailed
from manta_trading.data.tick.ingest_select import SkipKind, select_units
from manta_trading.data.tick.ingest_worker import (
    UnitOutcome,
    UnitResult,
    WorkerConnectionSettings,
    ingest_unit,
)
from manta_trading.data.tick.manifest_reads import UnitRow
from manta_trading.data.tick.manifest_repo import record_failure
from manta_trading.data.tick.manifest_transitions import ManifestTransitionError
from manta_trading.data.tick.pass_contract import (
    PhaseReport,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.phase_support import run_phase
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.store_context import TickStore
from manta_trading.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestInputs:
    """Everything a run needs beyond the store; built by the CLI from constants."""

    reader: ITickFileReader
    worker_settings: WorkerConnectionSettings
    calendar_url: str
    workers: int
    unit_ids: tuple[int, ...] = ()


class _Tally:
    """The phase summary, filled as units finish (LLD API Contracts)."""

    def __init__(self, summary: dict[str, Any]) -> None:
        self.summary = summary
        summary.update(ingested=0, failed=0, records=0, superseded=[], units=[])
        summary["skipped"] = {kind.value: 0 for kind in SkipKind}

    def unit(self, unit: UnitRow, outcome: str, **fields: Any) -> None:
        entry = {
            "unit_id": unit.unit_id,
            "unit_date": unit.unit_date.isoformat(),
            "schema": str(unit.schema),
            "outcome": outcome,
            "records": fields.pop("records", 0),
            "reason": fields.pop("reason", None),
        }
        self.summary["units"].append(entry | fields)

    def skip(self, kind: SkipKind, unit_id: int, detail: str) -> None:
        self.summary["skipped"][kind.value] += 1
        self.summary["units"].append(
            {"unit_id": unit_id, "outcome": kind.value, "reason": detail}
        )

    def ingested(self, plan: UnitIngestPlan, outcome: UnitOutcome) -> None:
        self.summary["ingested"] += 1
        self.summary["records"] += outcome.decoded
        self.summary["superseded"] += [old.unit_id for old in plan.superseded]
        self.unit(
            plan.unit,
            UnitResult.INGESTED.value,
            records=outcome.decoded,
            duration_seconds=round(outcome.duration_seconds, 3),
            decode_seconds=round(outcome.decode_seconds, 3),
            write_seconds=round(outcome.write_seconds, 3),
        )


async def _fail(store: TickStore, tally: _Tally, unit: UnitRow, reason: str) -> None:
    try:
        await record_failure(
            store.conn,
            unit.unit_id,
            UnitState.VERIFIED,
            reason,
            store.clock(),
            deterministic=True,
        )
    except ManifestTransitionError:
        # Reset or reopened by an operator after the worker returned.
        tally.skip(
            SkipKind.CHANGED_DURING_INGEST, unit.unit_id, "changed during ingest"
        )
        return
    tally.summary["failed"] += 1
    tally.unit(unit, UnitResult.FAILED.value, reason=reason)


async def _finish(
    store: TickStore, tally: _Tally, plan: UnitIngestPlan, outcome: UnitOutcome
) -> None:
    if outcome.result is UnitResult.INGESTED:
        tally.ingested(plan, outcome)
    elif outcome.result is UnitResult.CHANGED_DURING_INGEST:
        tally.skip(
            SkipKind.CHANGED_DURING_INGEST, plan.unit.unit_id, "changed during ingest"
        )
    else:
        assert outcome.reason is not None  # a failed outcome names its check
        await _fail(store, tally, plan.unit, outcome.reason)


async def _settle(
    tally: _Tally, running: dict[asyncio.Task[UnitOutcome], UnitIngestPlan]
) -> None:
    """Await every in-flight unit during an abort; report the committed ones."""
    results = await asyncio.gather(*running, return_exceptions=True)
    for plan, result in zip(running.values(), results, strict=True):
        if isinstance(result, UnitOutcome) and result.result is UnitResult.INGESTED:
            tally.ingested(plan, result)


async def run_ingest(
    store: TickStore, inputs: IngestInputs, summary: dict[str, Any]
) -> TickOutcome:
    """Ingest every selectable unit (or the named ones) and fill ``summary``."""
    tally = _Tally(summary)
    selection = await select_units(store.conn, inputs.unit_ids)
    for skipped in selection.skipped:
        tally.skip(skipped.kind, skipped.unit_id, skipped.detail)
    pending = list(selection.selected)
    running: dict[asyncio.Task[UnitOutcome], UnitIngestPlan] = {}
    calendars = Calendars(inputs.calendar_url)
    try:
        while pending or running:
            while pending and len(running) < inputs.workers:
                unit = pending.pop(0)
                try:
                    plan = await build_plan(store.conn, calendars, unit)
                except UnitCheckFailed as failure:
                    await _fail(store, tally, unit, failure.reason)
                    continue
                task = asyncio.create_task(
                    asyncio.to_thread(
                        ingest_unit,
                        plan,
                        store.archive_root,
                        inputs.worker_settings,
                        inputs.reader,
                        store.clock,
                    )
                )
                running[task] = plan
            if not running:
                continue
            done, _ = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for finished in done:
                plan = running.pop(finished)
                await _finish(store, tally, plan, finished.result())
    finally:
        if running:
            logger.error(
                "tick ingest aborting; settling %d in-flight units", len(running)
            )
            await _settle(tally, running)
        await asyncio.to_thread(calendars.close)
    return TickOutcome.PARTIAL if tally.summary["failed"] else TickOutcome.OK


class IngestPhase:
    """The ingest pass's one phase (a ``PassPhase[TickStore]``)."""

    name = TickPassPhaseName.INGEST

    def __init__(self, inputs: IngestInputs) -> None:
        self._inputs = inputs

    async def run(self, run: TickStore) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            return await run_ingest(run, self._inputs, summary)

        return await run_phase(self.name, body)

