"""The flow behind ``mt data tick run`` (slice 234 TD2, Data Flow).

One recorded run: open a ``TICK`` row, run the acquisition pass over the full
universe, then the ingest pass whatever acquisition did, fold the two
outcomes, close the row. The two passes are injected, so this module holds
the sequencing and the recording and no pass logic.

A preflight refusal of either pass (a setting, a pending migration, a held
lock, the tick database unreachable) is recorded, the other pass still runs,
and the run exits ``EXIT_PREFLIGHT`` with its row ``FAILED``. Recording is
best-effort, as for every pass: a recorder that is absent or whose database
is down loses the row and nothing else. Every recorder call from the event
loop goes through ``asyncio.to_thread`` (the recorder is synchronous psycopg).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING
from uuid import UUID

from manta_trading.cli.commands.tick_exit import (
    EXIT_BY_OUTCOME,
    EXIT_PREFLIGHT,
    PASS_RUN_OUTCOME_BY_TICK_OUTCOME,
    exit_code_for,
    worse_outcome,
)
from manta_trading.cli.commands.tick_heartbeat import IngestHeartbeat
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.tick.pass_contract import (
    PassResult,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    from manta_trading.data.acquisition.daemon.pass_run_recorder import (
        PassRunRecorder,
    )

logger = get_logger(__name__)

#: Awaited with a phase's name as it starts; writes it to the row.
PhaseHook = Callable[[TickPassPhaseName], Awaitable[None]]
#: The acquisition pass, reporting each phase through the hook.
Acquire = Callable[[PhaseHook], Awaitable[PassResult]]
#: The ingest pass, feeding the heartbeat (None when unrecorded).
Ingest = Callable[[IngestHeartbeat | None], Awaitable[PassResult]]

#: The detail text for a pass refused at preflight.
REFUSED_AT_PREFLIGHT = "preflight"


@dataclass(frozen=True)
class PassEnd:
    """How one of the two passes ended: a result, or a preflight refusal."""

    label: str
    result: PassResult | None = None
    refusal: str | None = None

    @property
    def text(self) -> str:
        return REFUSED_AT_PREFLIGHT if self.result is None else str(self.result.outcome)


@dataclass(frozen=True)
class TickRunResult:
    acquire: PassEnd
    ingest: PassEnd
    outcome: PassRunOutcome
    exit_code: int
    detail: str


def conclude(acquire: PassEnd, ingest: PassEnd) -> TickRunResult:
    """Fold the two ends into the row's outcome, exit code and detail."""
    detail = f"acquire={acquire.text} ingest={ingest.text}"
    refusals = [f"{e.label}: {e.refusal}" for e in (acquire, ingest) if e.refusal]
    if refusals:
        detail = f"{detail}; {'; '.join(refusals)}"
        return TickRunResult(
            acquire, ingest, PassRunOutcome.FAILED, EXIT_PREFLIGHT, detail
        )
    assert acquire.result is not None and ingest.result is not None
    combined: TickOutcome = worse_outcome(acquire.result.outcome, ingest.result.outcome)
    return TickRunResult(
        acquire,
        ingest,
        PASS_RUN_OUTCOME_BY_TICK_OUTCOME[combined],
        EXIT_BY_OUTCOME[combined],
        detail,
    )


async def _attempt(label: str, main: Callable[[], Awaitable[PassResult]]) -> PassEnd:
    try:
        return PassEnd(label, result=await main())
    except TickPreflightError as exc:
        # A refusal is an outcome of this run, not a crash: the other pass
        # still runs and the row records why (TD2).
        logger.error("tick run: %s refused at preflight: %s", label, exc)
        return PassEnd(label, refusal=str(exc))


class _Row:
    """The run's ``pass_runs`` row, or nothing when unrecorded."""

    def __init__(self, recorder: PassRunRecorder | None, run_id: UUID | None) -> None:
        self._recorder = recorder
        self._run_id = run_id

    @property
    def recorded(self) -> bool:
        return self._recorder is not None and self._run_id is not None

    async def phase(self, name: TickPassPhaseName) -> None:
        if self._recorder is not None:
            await asyncio.to_thread(
                partial(self._recorder.progress, self._run_id, phase=str(name))
            )

    def heartbeat(self) -> IngestHeartbeat | None:
        """A heartbeat writing the ingest row counts, from worker threads."""
        recorder, run_id = self._recorder, self._run_id
        if recorder is None or run_id is None:
            return None
        return IngestHeartbeat(
            lambda done, total: recorder.progress(
                run_id, phase=str(TickPassPhaseName.INGEST), done=done, total=total
            )
        )

    async def close(
        self, outcome: PassRunOutcome, exit_code: int | None, detail: str
    ) -> None:
        if self._recorder is not None:
            await asyncio.to_thread(
                partial(
                    self._recorder.close,
                    self._run_id,
                    outcome=outcome,
                    exit_code=exit_code,
                    detail=detail,
                )
            )


async def run_tick(
    recorder: PassRunRecorder | None, acquire: Acquire, ingest: Ingest
) -> TickRunResult:
    """Run both passes as one recorded ``TICK`` run; see the module docstring."""
    run_id = (
        await asyncio.to_thread(recorder.open, PassKind.TICK)
        if recorder is not None
        else None
    )
    row = _Row(recorder, run_id)
    try:
        acquired = await _attempt("acquisition", lambda: acquire(row.phase))
        await row.phase(TickPassPhaseName.INGEST)
        beat = row.heartbeat()
        ingested = await _attempt("ingest", lambda: ingest(beat))
        if beat is not None and ingested.result is not None:
            await asyncio.to_thread(beat.flush)
    except Exception as exc:
        # The row must not stay open: the next run would sweep it as
        # abandoned and the operator would see a failure with no cause.
        logger.exception("tick run failed")
        await row.close(PassRunOutcome.FAILED, exit_code_for(exc), type(exc).__name__)
        raise
    result = conclude(acquired, ingested)
    await row.close(result.outcome, result.exit_code, result.detail)
    return result
