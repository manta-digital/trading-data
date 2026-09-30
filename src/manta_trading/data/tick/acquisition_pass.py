"""The five phases of ``mt data tick pass`` and how they are wired (224).

LLD 224 *The pass* data flow:

1. **reconcile** — resolve unknown submits, sweep expiry, one ``advance``;
2. **availability** — the dataset edge and day conditions;
3. **purchase** — plan, guard, submit (``purchase_phase``);
4. **await** — ``advance`` every poll interval until nothing is in flight or
   the wait budget ends, then report what is still in flight with deadlines;
5. **definitions** — project verified definition units.

Reconcile, availability, await and definitions need no calendar (TD6); only
the purchase phase does. Unit failures (a failed verify, a refused job state,
an exhausted unknown submit, a rejected definition) make a phase ``partial``;
run-level failures abort it (``phase_support.run_phase``). The wait budget
bounds waiting for the provider, not downloading: it counts the time slept
between polls, and is never checked inside ``advance()`` (TD9).

:func:`pass_phases` builds the phases for one run, because the purchase phase
hands the await phase the jobs it submitted (``PassState``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from manta_trading.data.tick.adopt_files import FreeBytes, free_bytes
from manta_trading.data.tick.availability import capture_dataset
from manta_trading.data.tick.constants import (
    CME_DATASET,
    TICK_POLL_INTERVAL_SECONDS,
    TICK_WAIT_BUDGET_SECONDS,
)
from manta_trading.data.tick.definitions import project_definitions
from manta_trading.data.tick.in_flight import (
    AdvanceTally,
    advance,
    log_listing_lag,
    resolve_unsubmitted,
    sweep_expired,
)
from manta_trading.data.tick.manifest_reads import holed_days, owned_tier_days
from manta_trading.data.tick.pass_contract import (
    PassPhase,
    PassResult,
    PhaseReport,
    TickOutcome,
    TickPass,
    TickPassPhaseName,
)
from manta_trading.data.tick.phase_support import run_phase
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.purchase_phase import PassState, PurchasePhase
from manta_trading.data.tick.run_context import TickRun
from manta_trading.data.tick.universe import TICK_UNIVERSE

Window = tuple[date | None, date | None]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class AwaitTiming:
    """The await phase's wait budget and poll interval, in seconds."""

    budget_seconds: float = TICK_WAIT_BUDGET_SECONDS
    interval_seconds: float = TICK_POLL_INTERVAL_SECONDS


#: The shipped timing; slice 226 re-sets the two constants it reads.
DEFAULT_TIMING = AwaitTiming()


def _unit_failures(tally: AdvanceTally) -> bool:
    return bool(tally.failed or tally.refused)


class ReconcilePhase:
    """Resolve unknown submits, sweep expiry, advance in-flight work once."""

    name = TickPassPhaseName.RECONCILE

    def __init__(self, reader: ITickFileReader) -> None:
        self._reader = reader

    async def run(self, run: TickRun) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            resolved = await resolve_unsubmitted(run)
            summary["unknown_submits"] = {
                "matched": resolved.matched,
                "retryable": resolved.retryable,
                "exhausted": resolved.exhausted,
            }
            summary["swept_expired"] = await sweep_expired(run)
            tally = await advance(run, self._reader)
            summary.update(tally.to_dict())
            partial = resolved.exhausted or _unit_failures(tally)
            return TickOutcome.PARTIAL if partial else TickOutcome.OK

        return await run_phase(self.name, body)


class AvailabilityPhase:
    """Capture the dataset edge and day conditions; reopen changed holes."""

    name = TickPassPhaseName.AVAILABILITY

    def __init__(self, window: Window) -> None:
        self._window = window

    async def run(self, run: TickRun) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            owned = await owned_tier_days(run.conn)
            holed = await holed_days(run.conn)
            datasets = sorted(
                {CME_DATASET, *(k.dataset for k in owned), *(d for d, _ in holed)}
            )
            for dataset in datasets:
                captured = await capture_dataset(
                    run, dataset, TICK_UNIVERSE, owned, holed, self._window
                )
                summary[dataset] = {
                    "edge_end": captured.edge.end.isoformat(),
                    "span": None
                    if captured.span is None
                    else [
                        captured.span.start.isoformat(),
                        captured.span.end.isoformat(),
                    ],
                    "conditions": {
                        str(c): n for c, n in sorted(captured.conditions.items())
                    },
                    "written": captured.written,
                    "reopened": captured.reopened,
                }
            return TickOutcome.OK

        return await run_phase(self.name, body)


class AwaitPhase:
    """Poll in-flight jobs until none remain or the wait budget ends."""

    name = TickPassPhaseName.AWAIT

    def __init__(
        self,
        state: PassState,
        reader: ITickFileReader,
        timing: AwaitTiming,
        estimate_only: bool,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._state = state
        self._reader = reader
        self._timing = timing
        self._estimate_only = estimate_only
        self._sleep = sleep

    async def run(self, run: TickRun) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            if self._estimate_only:  # nothing was bought; no reason to wait
                summary["skipped"] = "estimate-only"
                return TickOutcome.OK
            waited = 0.0
            total = await advance(run, self._reader)
            await log_listing_lag(
                run, [(j.job_id, j.requested_at) for j in self._state.submitted]
            )
            while total.in_flight and waited < self._timing.budget_seconds:
                await self._sleep(self._timing.interval_seconds)
                waited += self._timing.interval_seconds
                total.add(await advance(run, self._reader))
            summary.update(total.to_dict())
            summary["waited_seconds"] = waited
            if _unit_failures(total):
                return TickOutcome.PARTIAL
            return TickOutcome.IN_FLIGHT if total.in_flight else TickOutcome.OK

        return await run_phase(self.name, body)


class DefinitionsPhase:
    """Project verified definition units into ``tick_definition``."""

    name = TickPassPhaseName.DEFINITIONS

    def __init__(self, reader: ITickFileReader) -> None:
        self._reader = reader

    async def run(self, run: TickRun) -> PhaseReport:
        async def body(summary: dict[str, Any]) -> TickOutcome:
            tally = await project_definitions(run, self._reader)
            summary.update(tally.to_dict())
            return TickOutcome.PARTIAL if tally.failed else TickOutcome.OK

        return await run_phase(self.name, body)


def pass_phases(
    window: Window,
    estimate_only: bool,
    reader: ITickFileReader,
    *,
    timing: AwaitTiming = DEFAULT_TIMING,
    sleep: Sleep = asyncio.sleep,
    free: FreeBytes = free_bytes,
) -> Sequence[PassPhase[TickRun]]:
    """The five phases for one run, in execution order."""
    state = PassState()
    return (
        ReconcilePhase(reader),
        AvailabilityPhase(window),
        PurchasePhase(window, estimate_only, state, free),
        AwaitPhase(state, reader, timing, estimate_only, sleep),
        DefinitionsPhase(reader),
    )


async def run_pass(
    run: TickRun,
    window: Window,
    estimate_only: bool,
    reader: ITickFileReader,
    *,
    timing: AwaitTiming = DEFAULT_TIMING,
    sleep: Sleep = asyncio.sleep,
    free: FreeBytes = free_bytes,
) -> PassResult:
    """Run the whole pass over ``run``."""
    phases = pass_phases(
        window, estimate_only, reader, timing=timing, sleep=sleep, free=free
    )
    return await TickPass[TickRun](run, phases).run()
