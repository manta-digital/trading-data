"""The tick acquisition pass — phase contract and sequencing (224).

A copy of the Kalshi pass contract (``data/kalshi/collection_pass.py``), kept
in this package because ``data/tick`` never imports ``data/kalshi`` (LLD 224
Technical Decision 1). ``test_pass_contract_parity.py`` diffs the copy against
the original, so neither can drift unseen.

One *pass* runs every :class:`PassPhase` in order over one shared
:class:`~manta_trading.data.tick.run_context.TickRun`. A phase reports a
:class:`PhaseReport`; the pass aggregates them into a :class:`PassResult`
whose ``outcome`` the CLI maps to an exit code (``EXIT_BY_OUTCOME``).

Sequencing: a phase that **aborts** (provider or storage) stops the pass and
the remaining phases are reported ``SKIPPED``; a ``partial``, ``refused`` or
``in_flight`` phase does not. The pass outcome is the worst phase outcome.

Declared divergences from Kalshi (TD1): two more outcomes (``refused``,
``in_flight``), no event sink, no ``on_phase`` callback, no historical phase.
An exception that is neither ``ProviderError`` nor ``psycopg.OperationalError``
propagates; there is no catch-all here.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

from manta_trading.logging import get_logger

if TYPE_CHECKING:
    from manta_trading.data.tick.run_context import TickRun

logger = get_logger(__name__)

#: A phase that never ran because an earlier phase aborted. Not a
#: ``TickOutcome``: it is not a way a phase can *end*.
SKIPPED: Literal["skipped"] = "skipped"


class TickOutcome(StrEnum):
    """Run classification; the CLI maps these to exit codes.

    Kalshi's four members carry the same values. ``refused``: a guard refused
    a purchase while there were wants. ``in_flight``: the wait budget ended
    with jobs still processing.
    """

    OK = "ok"
    PARTIAL = "partial"
    PROVIDER_ABORT = "provider_abort"
    STORAGE_ABORT = "storage_abort"
    REFUSED = "refused"
    IN_FLIGHT = "in_flight"


class TickPassPhaseName(StrEnum):
    """The phases of a tick pass, in execution order."""

    RECONCILE = "reconcile"
    AVAILABILITY = "availability"
    PURCHASE = "purchase"
    AWAIT = "await"
    DEFINITIONS = "definitions"


#: Aggregation precedence, worst first; the first match wins.
_OUTCOME_PRECEDENCE = (
    TickOutcome.STORAGE_ABORT,
    TickOutcome.PROVIDER_ABORT,
    TickOutcome.PARTIAL,
    TickOutcome.REFUSED,
    TickOutcome.IN_FLIGHT,
)


@dataclass(frozen=True)
class PhaseReport:
    """What one phase did: its outcome, its own summary, and how long it took."""

    name: TickPassPhaseName
    outcome: TickOutcome | Literal["skipped"]
    summary: dict[str, Any]
    duration_ms: int
    error: str | None = None


class PassPhase(Protocol):
    """One unit of acquisition work run over the pass's shared resources."""

    name: TickPassPhaseName

    async def run(self, run: TickRun) -> PhaseReport: ...


@dataclass(frozen=True)
class PassResult:
    """What one pass did — JSON-serializable through :meth:`to_dict`."""

    run_id: UUID
    started_at: datetime
    reports: tuple[PhaseReport, ...]
    outcome: TickOutcome
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        """The ``--json`` payload; ``exit_code`` is filled in by the CLI."""
        return {
            "run_id": str(self.run_id),
            "started_at": self.started_at.isoformat(),
            "phases": [
                {
                    "name": str(report.name),
                    "outcome": str(report.outcome),
                    "duration_ms": report.duration_ms,
                    "summary": report.summary,
                }
                for report in self.reports
            ],
            "outcome": str(self.outcome),
            "duration_ms": self.duration_ms,
        }


def classify_pass(reports: Sequence[PhaseReport]) -> TickOutcome:
    """The worst outcome any phase reported; skipped phases never influence it."""
    outcomes = {report.outcome for report in reports if report.outcome != SKIPPED}
    for candidate in _OUTCOME_PRECEDENCE:
        if candidate in outcomes:
            return candidate
    return TickOutcome.OK


def _aborted(report: PhaseReport) -> bool:
    return report.outcome in (TickOutcome.PROVIDER_ABORT, TickOutcome.STORAGE_ABORT)


class TickPass:
    """Runs ``phases`` in order over ``run``; see the module docstring."""

    def __init__(self, run: TickRun, phases: Sequence[PassPhase]) -> None:
        self._run = run
        self._phases = tuple(phases)

    async def run(self) -> PassResult:
        started_at = self._run.clock()
        started = time.monotonic()
        logger.info(
            "tick pass started run_id=%s phases=%s",
            self._run.run_id,
            ",".join(str(phase.name) for phase in self._phases),
        )
        reports: list[PhaseReport] = []
        aborted = False
        for phase in self._phases:
            if aborted:
                reports.append(
                    PhaseReport(
                        name=phase.name, outcome=SKIPPED, summary={}, duration_ms=0
                    )
                )
                continue
            report = await phase.run(self._run)
            reports.append(report)
            aborted = _aborted(report)
        outcome = classify_pass(reports)
        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "tick pass finished outcome=%s duration=%d ms phases: %s",
            outcome,
            duration_ms,
            " ".join(f"{r.name}={r.outcome}" for r in reports),
        )
        return PassResult(
            run_id=self._run.run_id,
            started_at=started_at,
            reports=tuple(reports),
            outcome=outcome,
            duration_ms=duration_ms,
        )
