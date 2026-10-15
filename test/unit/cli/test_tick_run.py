"""``mt data tick run``'s flow with fake passes and a fake recorder (234, 3.9)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from manta_trading.cli.commands.tick_exit import (
    EXIT_BY_OUTCOME,
    EXIT_PREFLIGHT,
    PASS_RUN_OUTCOME_BY_TICK_OUTCOME,
    worse_outcome,
)
from manta_trading.cli.tick_run import run_tick
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.tick.pass_contract import (
    ACQUISITION_PHASE_NAMES,
    PassResult,
    TickOutcome,
    TickPassPhaseName,
)
from manta_trading.data.tick.store_context import TickPreflightError

RUN_ID = uuid.UUID(int=7)


class FakeRecorder:
    def __init__(self, run_id: uuid.UUID | None = RUN_ID) -> None:
        self.run_id = run_id
        self.opened: list[PassKind] = []
        self.progress_calls: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []

    def open(self, kind: PassKind) -> uuid.UUID | None:
        self.opened.append(kind)
        return self.run_id

    def progress(self, run_id: uuid.UUID | None, **fields: Any) -> None:
        if run_id is not None:
            self.progress_calls.append(fields)

    def close(self, run_id: uuid.UUID | None, **fields: Any) -> None:
        if run_id is not None:
            self.closed.append(fields)


def _result(outcome: TickOutcome) -> PassResult:
    return PassResult(
        run_id=uuid.uuid4(),
        started_at=datetime(2026, 10, 14, tzinfo=UTC),
        reports=(),
        outcome=outcome,
        duration_ms=1,
    )


class FakePasses:
    """Records what ran and in which order; each pass returns or raises."""

    def __init__(self, acquire: Any, ingest: Any, batches: tuple[int, ...] = ()):
        self._acquire, self._ingest, self._batches = acquire, ingest, batches
        self.ran: list[str] = []

    async def acquire(self, hook: Any) -> PassResult:
        self.ran.append("acquire")
        for name in ACQUISITION_PHASE_NAMES:
            await hook(name)
        if isinstance(self._acquire, BaseException):
            raise self._acquire
        return _result(self._acquire)

    async def ingest(self, heartbeat: Any) -> PassResult:
        self.ran.append("ingest")
        if isinstance(self._ingest, BaseException):
            raise self._ingest
        for rows in self._batches if heartbeat is not None else ():
            heartbeat.add(rows)
        return _result(self._ingest)


def _run(recorder: Any, passes: FakePasses) -> Any:
    return asyncio.run(run_tick(recorder, passes.acquire, passes.ingest))


@pytest.mark.parametrize(
    "acquired",
    [TickOutcome.REFUSED, TickOutcome.PROVIDER_ABORT, TickOutcome.IN_FLIGHT],
)
def test_ingest_runs_after_a_failed_acquisition(acquired: TickOutcome) -> None:
    passes = FakePasses(acquired, TickOutcome.OK)
    _run(FakeRecorder(), passes)
    assert passes.ran == ["acquire", "ingest"]


@pytest.mark.parametrize("acquired", list(TickOutcome))
@pytest.mark.parametrize("ingested", list(TickOutcome))
def test_exit_row_and_detail_follow_the_fold(
    acquired: TickOutcome, ingested: TickOutcome
) -> None:
    recorder = FakeRecorder()
    result = _run(recorder, FakePasses(acquired, ingested))
    combined = worse_outcome(acquired, ingested)
    assert result.exit_code == EXIT_BY_OUTCOME[combined]
    assert recorder.opened == [PassKind.TICK]
    assert recorder.closed == [
        {
            "outcome": PASS_RUN_OUTCOME_BY_TICK_OUTCOME[combined],
            "exit_code": EXIT_BY_OUTCOME[combined],
            "detail": f"acquire={acquired} ingest={ingested}",
        }
    ]


@pytest.mark.parametrize("refused", ["acquire", "ingest"])
def test_a_preflight_refusal_exits_1_and_still_runs_the_other(refused: str) -> None:
    refusal = TickPreflightError("another tick acquisition run holds the lock")
    acquired: Any = refusal if refused == "acquire" else TickOutcome.OK
    ingested: Any = refusal if refused == "ingest" else TickOutcome.OK
    passes = FakePasses(acquired, ingested)
    recorder = FakeRecorder()
    result = _run(recorder, passes)
    assert passes.ran == ["acquire", "ingest"]
    assert result.exit_code == EXIT_PREFLIGHT
    (closed,) = recorder.closed
    assert closed["outcome"] is PassRunOutcome.FAILED
    assert closed["exit_code"] == EXIT_PREFLIGHT
    assert "holds the lock" in closed["detail"]
    assert "preflight" in closed["detail"]


def test_an_unexpected_exception_closes_failed_and_reraises() -> None:
    recorder = FakeRecorder()
    with pytest.raises(ZeroDivisionError):
        _run(recorder, FakePasses(TickOutcome.OK, ZeroDivisionError("boom")))
    (closed,) = recorder.closed
    assert closed["outcome"] is PassRunOutcome.FAILED
    assert closed["detail"] == "ZeroDivisionError"
    assert closed["exit_code"] is None  # no mapped code for this class


def test_a_down_primary_changes_nothing_but_the_row() -> None:
    passes = FakePasses(TickOutcome.OK, TickOutcome.PARTIAL, batches=(5,))
    result = _run(FakeRecorder(run_id=None), passes)
    assert result.exit_code == EXIT_BY_OUTCOME[TickOutcome.PARTIAL]
    no_recorder = _run(None, FakePasses(TickOutcome.OK, TickOutcome.PARTIAL))
    assert no_recorder.exit_code == result.exit_code


def test_phases_and_ingest_counts_reach_the_row() -> None:
    recorder = FakeRecorder()
    _run(recorder, FakePasses(TickOutcome.OK, TickOutcome.OK, batches=(3, 4)))
    phases = [c["phase"] for c in recorder.progress_calls]
    expected = [str(n) for n in ACQUISITION_PHASE_NAMES]
    assert phases[: len(expected)] == expected
    assert phases[len(expected)] == str(TickPassPhaseName.INGEST)
    # The final flush carries every batch, with no expected total set.
    assert recorder.progress_calls[-1] == {
        "phase": str(TickPassPhaseName.INGEST),
        "done": 7,
        "total": None,
    }
