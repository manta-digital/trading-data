"""The Kalshi collection pass — phase contract and sequencing (slice 263).

One *pass* is what the timer runs: every registered
:class:`PassPhase`, in order, over one shared :class:`KalshiRun` (one
client, one locked connection, one sink, one ``run_id``). A phase reports a
:class:`PhaseReport`; the pass aggregates the reports into a
:class:`PassResult` whose ``outcome`` the CLI maps to an exit code with
262's ``EXIT_BY_OUTCOME`` — no exit-code integer appears here.

Sequencing rules (design 263, Decision 2), both visible below as one loop
and one pure function:

* a phase that **aborts** (provider or storage) stops the pass — the
  remaining phases are reported ``SKIPPED`` and never run, which is what
  enforces "the catalog is current before the time-series surfaces run";
* a **partial** phase (item errors) does not stop the pass;
* the pass outcome is the worst phase outcome: storage > provider >
  partial > ok.

An exception that is neither ``ProviderError`` nor
``psycopg.OperationalError`` propagates out of the pass exactly as it does
out of ``run_sync`` today — there is no catch-all here.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID

import psycopg

from manta_trading.data.kalshi.events import SyncEvent, SyncEventType
from manta_trading.data.kalshi.sync_types import SyncOutcome, classify
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderError

if TYPE_CHECKING:
    from manta_trading.data.kalshi.run_context import KalshiRun

logger = get_logger(__name__)

#: A phase that never ran because an earlier phase aborted. Deliberately not
#: a ``SyncOutcome`` member: it is not a way a phase can *end*, ``classify``
#: must never produce it, and ``EXIT_BY_OUTCOME`` must never map it.
SKIPPED: Literal["skipped"] = "skipped"

#: Aggregation precedence (Decision 2): the first match wins.
_OUTCOME_PRECEDENCE = (
    SyncOutcome.STORAGE_ABORT,
    SyncOutcome.PROVIDER_ABORT,
    SyncOutcome.PARTIAL,
)


class PassPhaseName(StrEnum):
    """The phases a pass can contain, in execution order."""

    CATALOG = "catalog"
    CANDLES = "candles"
    TRADES = "trades"
    HISTORICAL = "historical"


@dataclass(frozen=True)
class PhaseReport:
    """What one phase did: its outcome, its own summary, and how long it took."""

    name: PassPhaseName
    outcome: SyncOutcome | Literal["skipped"]
    summary: dict[str, Any]
    duration_ms: int
    error: str | None = None


class PassPhase(Protocol):
    """One unit of collection work run over the pass's shared resources."""

    name: PassPhaseName

    async def run(self, run: KalshiRun) -> PhaseReport: ...


@dataclass(frozen=True)
class PassResult:
    """What one pass did — JSON-serializable through :meth:`to_dict`."""

    run_id: UUID
    started_at: datetime
    reports: tuple[PhaseReport, ...]
    outcome: SyncOutcome
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


def classify_pass(reports: Sequence[PhaseReport]) -> SyncOutcome:
    """The worst outcome any phase reported; skipped phases never influence it."""
    outcomes = {report.outcome for report in reports if report.outcome != SKIPPED}
    for candidate in _OUTCOME_PRECEDENCE:
        if candidate in outcomes:
            return candidate
    return SyncOutcome.OK


def _aborted(report: PhaseReport) -> bool:
    return report.outcome in (SyncOutcome.PROVIDER_ABORT, SyncOutcome.STORAGE_ABORT)


class CollectionPass:
    """Runs ``phases`` in order over ``run``; see the module docstring."""

    def __init__(self, run: KalshiRun, phases: Sequence[PassPhase]) -> None:
        self._run = run
        self._phases = tuple(phases)

    async def run(self) -> PassResult:
        started_at = self._run.clock()
        started = time.monotonic()
        client = self._run.client
        logger.info(
            "kalshi pass started run_id=%s mode=%s budget=%s/min phases=%s",
            self._run.run_id,
            client.mode,
            client.rate_limit.requests_per_minute,
            ",".join(str(phase.name) for phase in self._phases),
        )
        self._emit(SyncEventType.PASS_STARTED, started_at)

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
        error = next((r.error for r in reports if _aborted(r)), None)
        self._emit(
            SyncEventType.PASS_FINISHED,
            self._run.clock(),
            error=error,
            duration_ms=duration_ms,
        )
        logger.info(
            "kalshi pass finished outcome=%s duration=%d ms phases: %s",
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

    def _emit(
        self,
        event_type: SyncEventType,
        timestamp: datetime,
        *,
        error: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Best-effort emission, as ``CatalogSync.emit`` is: a sink failure is
        logged and never aborts the pass. Synchronous because the pass emits
        exactly twice, outside any phase's I/O."""
        event = SyncEvent(
            run_id=self._run.run_id,
            timestamp=timestamp,
            event_type=event_type,
            error=error,
            duration_ms=duration_ms,
        )
        try:
            self._run.sink.emit(event)
        except Exception:
            logger.exception("event sink failed on %s", event_type)


class CatalogPhase:
    """The catalog phase — 262's :class:`CatalogSync` under the phase contract."""

    name = PassPhaseName.CATALOG

    async def run(self, run: KalshiRun) -> PhaseReport:
        from manta_trading.data.kalshi.repository import CatalogRepository
        from manta_trading.data.kalshi.sync import CatalogSync

        started = time.monotonic()
        sync = CatalogSync(
            run.client, CatalogRepository(run.conn), run.sink, run_id=run.run_id
        )
        failure: ProviderError | psycopg.OperationalError | None = None
        try:
            await sync.run()
        except ProviderError as exc:
            failure = exc
        except psycopg.OperationalError as exc:
            failure = exc
            logger.exception("kalshi catalog phase storage failure")
        return PhaseReport(
            name=self.name,
            outcome=classify(sync.result, failure),
            summary=sync.result.to_dict(),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(failure) if failure is not None else None,
        )


class CandlesPhase:
    """The candle phase — 264's :class:`CandleSync` under the phase contract."""

    name = PassPhaseName.CANDLES

    async def run(self, run: KalshiRun) -> PhaseReport:
        from manta_trading.data.kalshi.candle_repository import CandleRepository
        from manta_trading.data.kalshi.candle_sync import CandleSync
        from manta_trading.data.kalshi.candle_types import classify_candles

        started = time.monotonic()
        rule = run.settings.collection_rule()
        sync = CandleSync(
            run.client,
            CandleRepository(run.conn, rule),
            run.sink,
            rule=rule,
            run_id=run.run_id,
            clock=run.clock,
        )
        failure: ProviderError | psycopg.OperationalError | None = None
        try:
            await sync.run()
        except ProviderError as exc:
            failure = exc
        except psycopg.OperationalError as exc:
            failure = exc
            logger.exception("kalshi candles phase storage failure")
        return PhaseReport(
            name=self.name,
            outcome=classify_candles(sync.result, failure),
            summary=sync.result.to_dict(),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(failure) if failure is not None else None,
        )


class TradesPhase:
    """The trades phase — 265's :class:`TradeSync` under the phase contract.

    ``TradesBehindCutoffError`` is deliberately not caught here: it propagates
    out of the pass (265 Decision 6), and the earlier phases' reports stand.
    """

    name = PassPhaseName.TRADES

    async def run(self, run: KalshiRun) -> PhaseReport:
        from manta_trading.data.kalshi.trade_repository import TradeRepository
        from manta_trading.data.kalshi.trade_sync import TradeSync
        from manta_trading.data.kalshi.trade_types import classify_trades

        started = time.monotonic()
        rule = run.settings.collection_rule()
        sync = TradeSync(
            run.client,
            TradeRepository(
                run.conn,
                rule,
                trades_excluded=run.settings.kalshi_trades_excluded_categories,
            ),
            run.sink,
            rule=rule,
            run_id=run.run_id,
            clock=run.clock,
        )
        failure: ProviderError | psycopg.OperationalError | None = None
        try:
            await sync.run()
        except ProviderError as exc:
            failure = exc
        except psycopg.OperationalError as exc:
            failure = exc
            logger.exception("kalshi trades phase storage failure")
        return PhaseReport(
            name=self.name,
            outcome=classify_trades(sync.result, failure),
            summary=sync.result.to_dict(),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(failure) if failure is not None else None,
        )


class HistoricalPhase:
    """The historical phase — 267's :class:`HistoricalSync` under the phase
    contract. The request cap is computed here from the run's client budget
    (267 Decision 2: ``requests_per_minute × HISTORICAL_PHASE_MINUTES``) and
    logged before the core is built — the core never sees the client.
    """

    name = PassPhaseName.HISTORICAL

    async def run(self, run: KalshiRun) -> PhaseReport:
        from manta_trading.data.kalshi.candle_repository import CandleRepository
        from manta_trading.data.kalshi.constants import (
            HISTORICAL_PHASE_MINUTES,
            Surface,
        )
        from manta_trading.data.kalshi.historical_sync import HistoricalSync
        from manta_trading.data.kalshi.historical_types import classify_historical
        from manta_trading.data.kalshi.repository import CatalogRepository
        from manta_trading.data.kalshi.trade_repository import TradeRepository

        started = time.monotonic()
        rule = run.settings.collection_rule()
        budget = run.client.rate_limit.requests_per_minute
        cap = budget * HISTORICAL_PHASE_MINUTES
        logger.info(
            "kalshi historical cap=%d (%d/min × %d min, mode=%s)",
            cap,
            budget,
            HISTORICAL_PHASE_MINUTES,
            run.client.mode,
        )
        sync = HistoricalSync(
            run.client,
            TradeRepository(
                run.conn,
                rule,
                trades_excluded=run.settings.kalshi_trades_excluded_categories,
                surface=Surface.HISTORICAL,
            ),
            CandleRepository(run.conn, rule),
            CatalogRepository(run.conn),
            run.sink,
            rule=rule,
            run_id=run.run_id,
            cap=cap,
            clock=run.clock,
        )
        failure: ProviderError | psycopg.OperationalError | None = None
        try:
            await sync.run()
        except ProviderError as exc:
            failure = exc
        except psycopg.OperationalError as exc:
            failure = exc
            logger.exception("kalshi historical phase storage failure")
        return PhaseReport(
            name=self.name,
            outcome=classify_historical(sync.result, failure),
            summary=sync.result.to_dict(),
            duration_ms=int((time.monotonic() - started) * 1000),
            error=str(failure) if failure is not None else None,
        )


#: The single registration point for pass phases. Order is execution order —
#: the catalog is current before candles run, and both before the trades tape
#: is classified against the catalog (265 Decision 5); historical is last
#: (267 Decision 1): it sequences the archive work *after* the live phases
#: inside one process, which is what removes the rate-budget contention the
#: retired operator drain waited for, and it self-limits to its own cap.
PASS_PHASES: tuple[PassPhase, ...] = (
    CatalogPhase(),
    CandlesPhase(),
    TradesPhase(),
    HistoricalPhase(),
)
