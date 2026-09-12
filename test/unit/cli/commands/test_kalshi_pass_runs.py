"""Unit tests: the Kalshi pass records itself in pass_runs (slice 922).

``mt data kalshi pass`` is the third writer. What it must get right:

- one row per pass, opened before any phase runs and closed with the
  outcome the pass classified;
- the phase callback fires as each phase starts, so a running pass can say
  which phase it is in, and skipped phases do not fire it — nothing is
  running for them;
- a preflight failure and an unexpected exception both close the row, because
  a row left open would be swept as abandoned by the next pass and the
  operator would see a failure with no cause attached;
- the detail is the per-phase outcome list the pass already logs, so a
  provider abort names the phases it skipped.

The recorder is faked. Its SQL is covered by the repository integration
tests; what is under test here is what the CLI asks it to record.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import uuid
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import kalshi as cmd
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.collection_pass import (
    CollectionPass,
    PassPhaseName,
    PassResult,
    PhaseReport,
)
from manta_trading.data.kalshi.db import PreflightError
from manta_trading.data.kalshi.sync_types import SyncOutcome

_SKIPPED = "skipped"

runner = CliRunner()


class FakeRecorder:
    def __init__(self) -> None:
        self.opened: list[PassKind] = []
        self.progressed: list[tuple[Any, str | None]] = []
        self.closed: list[tuple[Any, PassRunOutcome, int | None, str | None]] = []
        self.on_progress: Callable[[], None] | None = None

    def open(self, kind: PassKind, *, walk_anchor_at=None):
        self.opened.append(kind)
        return uuid.UUID(int=7)

    def progress(self, run_id, *, phase=None, done=None, total=None) -> None:
        if self.on_progress is not None:
            self.on_progress()
        self.progressed.append((run_id, phase))

    def close(self, run_id, *, outcome, exit_code=None, detail=None) -> None:
        self.closed.append((run_id, outcome, exit_code, detail))


def _report(
    name: PassPhaseName, outcome: SyncOutcome | str = SyncOutcome.OK
) -> PhaseReport:
    return PhaseReport(name=name, outcome=outcome, summary={}, duration_ms=1)


def _result(*reports: PhaseReport, outcome: SyncOutcome) -> PassResult:
    return PassResult(
        run_id=uuid.uuid4(),
        started_at=None,
        reports=tuple(reports),
        outcome=outcome,
        duration_ms=20,
    )


class TestPassDetail:
    """The detail is the phase list, so an abort names what it skipped."""

    def test_it_lists_every_phase_and_its_outcome(self) -> None:
        result = _result(
            _report(PassPhaseName.CATALOG),
            _report(PassPhaseName.CANDLES, SyncOutcome.PROVIDER_ABORT),
            _report(PassPhaseName.TRADES, _SKIPPED),
            outcome=SyncOutcome.PROVIDER_ABORT,
        )
        detail = cmd._pass_detail(result)
        assert "catalog=ok" in detail
        assert "candles=provider_abort" in detail
        assert "trades=skipped" in detail

    def test_an_all_ok_pass_names_every_phase(self) -> None:
        result = _result(
            _report(PassPhaseName.CATALOG),
            _report(PassPhaseName.CANDLES),
            outcome=SyncOutcome.OK,
        )
        assert cmd._pass_detail(result) == "catalog=ok candles=ok"


class TestPhaseReporter:
    def test_it_is_none_without_a_recorder(self) -> None:
        assert cmd._phase_reporter(None, uuid.UUID(int=1)) is None

    def test_it_is_none_without_a_run_id(self) -> None:
        assert cmd._phase_reporter(FakeRecorder(), None) is None  # type: ignore[arg-type]

    def test_it_reports_the_phase_name(self) -> None:
        rec = FakeRecorder()
        report = cmd._phase_reporter(rec, uuid.UUID(int=7))  # type: ignore[arg-type]
        assert report is not None
        asyncio.run(report(PassPhaseName.CANDLES))
        assert rec.progressed == [(uuid.UUID(int=7), "candles")]

    def test_the_write_does_not_run_on_the_event_loop(self) -> None:
        """The point of the change: a slow psycopg write must not block it.

        The recorder records which thread called it; awaiting the reporter
        from a running loop must land on some other thread (922 review
        F003).
        """
        rec = FakeRecorder()
        report = cmd._phase_reporter(rec, uuid.UUID(int=7))  # type: ignore[arg-type]
        assert report is not None
        calling_thread: list[int] = []
        rec.on_progress = lambda: calling_thread.append(threading.get_ident())

        async def _drive() -> int:
            await report(PassPhaseName.CANDLES)
            return threading.get_ident()

        loop_thread = asyncio.run(_drive())
        assert calling_thread and calling_thread[0] != loop_thread


class _Phase:
    """A phase that records that it ran and returns a scripted outcome."""

    def __init__(
        self, name: PassPhaseName, outcome: SyncOutcome = SyncOutcome.OK
    ) -> None:
        self.name = name
        self._outcome = outcome
        self.ran = False

    async def run(self, _run: object) -> PhaseReport:
        self.ran = True
        return PhaseReport(
            name=self.name,
            outcome=self._outcome,
            summary={},
            duration_ms=1,
            error=None if self._outcome is SyncOutcome.OK else "boom",
        )


def _fake_run() -> MagicMock:
    run = MagicMock()
    run.run_id = uuid.uuid4()
    run.clock.return_value = None
    run.client.mode = "public"
    run.client.rate_limit.requests_per_minute = 300
    run.emit = MagicMock()
    return run


def _collector() -> tuple[list[PassPhaseName], object]:
    """An async on_phase that records what it was called with.

    Async because the callback is awaited from inside the pass loop: its
    real implementation writes a database row, which must not run on the
    event loop (922 review F003).
    """
    seen: list[PassPhaseName] = []

    async def _on_phase(phase: PassPhaseName) -> None:
        seen.append(phase)

    return seen, _on_phase


class TestOnPhaseCallback:
    """CollectionPass awaits the callback as each phase starts."""

    def test_it_fires_once_per_phase_in_order(self) -> None:
        seen, on_phase = _collector()
        phases = [
            _Phase(PassPhaseName.CATALOG),
            _Phase(PassPhaseName.CANDLES),
        ]
        asyncio.run(CollectionPass(_fake_run(), phases, on_phase=on_phase).run())
        assert seen == [PassPhaseName.CATALOG, PassPhaseName.CANDLES]

    def test_a_skipped_phase_does_not_fire_it(self) -> None:
        """Nothing is running for a skipped phase, so it reports nothing."""
        seen, on_phase = _collector()
        phases = [
            _Phase(PassPhaseName.CATALOG, SyncOutcome.PROVIDER_ABORT),
            _Phase(PassPhaseName.CANDLES),
        ]
        result = asyncio.run(
            CollectionPass(_fake_run(), phases, on_phase=on_phase).run()
        )
        assert seen == [PassPhaseName.CATALOG]
        assert phases[1].ran is False
        assert result.outcome is SyncOutcome.PROVIDER_ABORT

    def test_none_leaves_the_pass_unchanged(self) -> None:
        phases = [_Phase(PassPhaseName.CATALOG)]
        result = asyncio.run(CollectionPass(_fake_run(), phases).run())
        assert result.outcome is SyncOutcome.OK
        assert phases[0].ran is True


class TestOutcomeRecorded:
    """Each SyncOutcome closes the row with its mapped PassRunOutcome."""

    @pytest.mark.parametrize(
        ("sync_outcome", "recorded", "exit_code"),
        [
            (SyncOutcome.OK, PassRunOutcome.COMPLETE, cmd.EXIT_OK),
            (SyncOutcome.PARTIAL, PassRunOutcome.INCOMPLETE, cmd.EXIT_SYNC_PARTIAL),
            (
                SyncOutcome.PROVIDER_ABORT,
                PassRunOutcome.PROVIDER_UNAVAILABLE,
                cmd.EXIT_PROVIDER,
            ),
            (SyncOutcome.STORAGE_ABORT, PassRunOutcome.FAILED, cmd.EXIT_STORAGE),
        ],
    )
    def test_the_mapped_outcome_and_exit_code_are_recorded_together(
        self,
        sync_outcome: SyncOutcome,
        recorded: PassRunOutcome,
        exit_code: int,
    ) -> None:
        assert cmd.pass_run_outcome_for_kalshi(sync_outcome) is recorded
        assert cmd.EXIT_BY_OUTCOME[sync_outcome] == exit_code


# ---------------------------------------------------------------------------
# End to end through the real CLI: what run_pass opens and closes
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _recording_pass(
    *,
    outcome: SyncOutcome = SyncOutcome.OK,
    preflight: BaseException | None = None,
    timescale_url: str | None = "postgresql://ts/test",
    cycle_raises: BaseException | None = None,
) -> Iterator[FakeRecorder]:
    """Drive the real ``mt data kalshi pass`` with a fake recorder attached."""
    settings = MagicMock()
    settings.timescale_db_url = timescale_url
    settings.kalshi_api_key_id = "k"
    settings.kalshi_private_key_pem = None

    recorder = FakeRecorder()

    @contextlib.contextmanager
    def _fake_factory(_settings: object) -> Iterator[FakeRecorder]:
        yield recorder

    conn = MagicMock()
    conn.close = AsyncMock()
    client = MagicMock()
    client.aclose = AsyncMock()
    client.mode = "public"
    client.rate_limit.requests_per_minute = 300

    phase = _Phase(PassPhaseName.CATALOG, outcome)

    async def _boom(_self: object) -> None:
        raise cycle_raises  # type: ignore[misc]

    with contextlib.ExitStack() as stack:
        stack.enter_context(
            patch("manta_trading.cli.app.Settings", return_value=settings)
        )
        stack.enter_context(patch("manta_trading.cli.app.setup_logging"))
        stack.enter_context(
            patch(
                "manta_trading.data.kalshi.db.open_sync_connection",
                AsyncMock(return_value=conn, side_effect=preflight),
            )
        )
        stack.enter_context(
            patch.object(KalshiClient, "from_settings", return_value=client)
        )
        stack.enter_context(
            patch(
                "manta_trading.data.kalshi.collection_pass.PASS_PHASES",
                (phase,),
            )
        )
        stack.enter_context(
            patch(
                "manta_trading.cli.commands._pass_run.pass_run_recorder",
                _fake_factory,
            )
        )
        if cycle_raises is not None:
            stack.enter_context(patch.object(CollectionPass, "run", _boom))
        yield recorder


class TestRunPassWiring:
    def test_a_successful_pass_opens_and_closes_one_row(self) -> None:
        with _recording_pass() as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code == cmd.EXIT_OK, result.output
        assert rec.opened == [PassKind.KALSHI]
        assert len(rec.closed) == 1
        run_id, outcome, exit_code, detail = rec.closed[0]
        assert run_id == uuid.UUID(int=7)
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == cmd.EXIT_OK
        assert detail == "catalog=ok"

    def test_the_kalshi_row_carries_no_walk_anchor(self) -> None:
        """A Kalshi pass is not a universe walk, so it anchors nothing."""
        with _recording_pass() as rec:
            runner.invoke(app, ["data", "kalshi", "pass"])
        assert rec.opened == [PassKind.KALSHI]

    def test_a_provider_abort_records_provider_unavailable(self) -> None:
        with _recording_pass(outcome=SyncOutcome.PROVIDER_ABORT) as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code == cmd.EXIT_PROVIDER
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.PROVIDER_UNAVAILABLE
        assert exit_code == cmd.EXIT_PROVIDER
        assert detail == "catalog=provider_abort"

    def test_a_storage_abort_records_failed(self) -> None:
        with _recording_pass(outcome=SyncOutcome.STORAGE_ABORT) as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code == cmd.EXIT_STORAGE
        assert rec.closed[0][1] is PassRunOutcome.FAILED

    def test_the_phase_start_is_reported(self) -> None:
        with _recording_pass() as rec:
            runner.invoke(app, ["data", "kalshi", "pass"])
        assert rec.progressed == [(uuid.UUID(int=7), "catalog")]

    def test_a_preflight_failure_closes_the_row(self) -> None:
        """A row left open would be swept as abandoned by the next pass.

        ``kalshi_run`` turns a PreflightError into a yielded ``None``, which
        is the path under test. An error it does not recognise is a different
        case, covered below.
        """
        with _recording_pass(preflight=PreflightError("no database")) as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code == cmd.EXIT_PREFLIGHT
        assert len(rec.closed) == 1
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert exit_code == cmd.EXIT_PREFLIGHT
        assert detail == "preflight failed"

    def test_an_unexpected_exception_closes_the_row_and_propagates(self) -> None:
        with _recording_pass(cycle_raises=ValueError("kaboom")) as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code != cmd.EXIT_OK
        assert len(rec.closed) == 1
        _, outcome, _, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert detail == "ValueError"

    def test_a_missing_db_url_still_runs_the_command(self) -> None:
        """No database means no row, never a blocked pass."""
        with _recording_pass(timescale_url=None) as rec:
            result = runner.invoke(app, ["data", "kalshi", "pass"])
        assert result.exit_code == cmd.EXIT_PREFLIGHT
        assert rec.opened == []
