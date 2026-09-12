"""Unit tests: the runner writes pass_runs rows (slice 922, Task 2.4/2.5).

The runner is the only writer for the minute and daily cycles, and three
rules decide what it writes:

- **Decision 3** — only an all-active cycle records. A ``--symbols``
  invocation is the operator poking at a few tickers, not the universe walk
  the overview reports on, so the CLI passes no recorder and nothing is
  written.
- **The walk anchor** — set only when the cycle owes a full walk. A minute
  pass on a non-firing day does backfill over whatever it can reach, which is
  not a universe walk and must not reset every symbol's staleness clock. A
  daily pass carries the boundary of the pass it is walking, so both of a
  day's firings share one anchor.
- **Decision 5** — the daily exit code stays 0 whatever the row says.

A cycle that raises must still close its row, or the next pass would sweep it
as abandoned and the operator would see a failure with no cause attached.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

from manta_trading.data.acquisition.daemon.cadence import daily_pass_boundary
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.minute import (
    MINUTE_EXIT_OK,
    MINUTE_EXIT_PASS_INCOMPLETE,
)
from manta_trading.data.acquisition.daemon.runner import (
    DAILY_EXIT_OK,
    SCOPE_ALL_ACTIVE,
    Runner,
    RunnerConfig,
)
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.acquisition.quota import QuotaBucket
from manta_trading.data.acquisition.state import MinutePassOutcome

UTC = UTC

# A Wednesday. With MT_MINUTE_FIRING_DAYS=Sat this is not a firing day.
_WEDNESDAY = datetime(2026, 9, 9, 13, 5, tzinfo=UTC)
# The Saturday after it.
_SATURDAY = datetime(2026, 9, 12, 13, 5, tzinfo=UTC)
_SATURDAY_WEEKDAY = (5,)


class FakeRecorder:
    """Records what the runner asked for, in order."""

    def __init__(self) -> None:
        self.opened: list[tuple[PassKind, datetime | None]] = []
        self.progressed: list[tuple[Any, str | None, int | None, int | None]] = []
        self.closed: list[tuple[Any, PassRunOutcome, int | None, str | None]] = []
        self._next_id = 0

    def open(self, kind: PassKind, *, walk_anchor_at: datetime | None = None):
        self.opened.append((kind, walk_anchor_at))
        self._next_id += 1
        return uuid.UUID(int=self._next_id)

    def progress(self, run_id, *, phase=None, done=None, total=None) -> None:
        self.progressed.append((run_id, phase, done, total))

    def close(self, run_id, *, outcome, exit_code=None, detail=None) -> None:
        self.closed.append((run_id, outcome, exit_code, detail))

    def opened_kinds(self) -> list[PassKind]:
        return [k for k, _ in self.opened]

    def closed_outcomes(self) -> list[PassRunOutcome]:
        return [o for _, o, _, _ in self.closed]


def _conn_factory() -> MagicMock:
    """A connection whose ca_update check reports 'already ran today'."""
    cur = MagicMock()
    cur.fetchone.return_value = (datetime.now(UTC),)
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn = MagicMock()
    conn.cursor.return_value = cur
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=cm)


def _runner(
    *,
    recorder: FakeRecorder | None,
    now: datetime,
    granularities: frozenset[str],
    daily_func: Any = None,
    minute_func: Any = None,
    scope: Any = SCOPE_ALL_ACTIVE,
    firing_days: tuple[int, ...] | None = _SATURDAY_WEEKDAY,
) -> Runner:
    return Runner(
        config=RunnerConfig(
            scope=scope,
            granularities=granularities,
            terminate_when_drained=True,
        ),
        bucket=QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None),
        conn_factory=_conn_factory(),
        run_daily_cycle=daily_func or MagicMock(return_value=CycleReport()),
        run_minute_cycle=minute_func or MagicMock(return_value=CycleReport()),
        run_ca_update=MagicMock(),
        clock=lambda: now,
        pass_run_recorder=recorder,  # type: ignore[arg-type]
        minute_firing_days=firing_days,
    )


def _minute_report(
    outcome: MinutePassOutcome = MinutePassOutcome.COMPLETE,
    *,
    trailing_completed: bool = True,
    trailing_required: bool = True,
    trailing_attempted: int = 15215,
    backfill_attempted: int = 900,
) -> CycleReport:
    return CycleReport(
        minute_pass_outcome=outcome,
        minute_trailing_completed=trailing_completed,
        minute_trailing_required=trailing_required,
        trailing_symbols_attempted=trailing_attempted,
        backfill_symbols_attempted=backfill_attempted,
    )


def _run_minute(runner: Runner) -> None:
    """Drive one minute cycle; the drained-exit contract ends the loop."""
    runner.start()


class TestMinuteWriter:
    def test_a_firing_day_opens_an_anchored_row(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(return_value=_minute_report()),
        )
        _run_minute(runner)
        assert rec.opened_kinds() == [PassKind.MINUTE]
        assert rec.opened[0][1] == _SATURDAY

    def test_a_non_firing_day_opens_an_unanchored_row(self) -> None:
        """Backfill over what it can reach is not a universe walk."""
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_WEDNESDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(return_value=_minute_report(trailing_required=False)),
        )
        _run_minute(runner)
        assert rec.opened_kinds() == [PassKind.MINUTE]
        assert rec.opened[0][1] is None

    def test_a_daily_firing_cadence_always_anchors(self) -> None:
        """With no MT_MINUTE_FIRING_DAYS set, every day is a firing day."""
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_WEDNESDAY,
            granularities=frozenset({"minute"}),
            firing_days=None,
            minute_func=MagicMock(return_value=_minute_report()),
        )
        _run_minute(runner)
        assert rec.opened[0][1] == _WEDNESDAY

    def test_a_complete_pass_closes_complete_with_exit_zero(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(return_value=_minute_report()),
        )
        _run_minute(runner)
        assert len(rec.closed) == 1
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == MINUTE_EXIT_OK
        assert detail is not None
        assert "trailing 15215" in detail
        assert "backfill 900 symbols" in detail

    def test_quota_after_trailing_records_quota_but_exits_zero(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(
                return_value=_minute_report(MinutePassOutcome.QUOTA_EXHAUSTED)
            ),
        )
        _run_minute(runner)
        _, outcome, exit_code, _ = rec.closed[0]
        assert outcome is PassRunOutcome.COMPLETE_QUOTA
        assert exit_code == MINUTE_EXIT_OK

    def test_quota_inside_trailing_records_incomplete(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(
                return_value=_minute_report(
                    MinutePassOutcome.QUOTA_EXHAUSTED, trailing_completed=False
                )
            ),
        )
        _run_minute(runner)
        _, outcome, exit_code, _ = rec.closed[0]
        assert outcome is PassRunOutcome.INCOMPLETE
        assert exit_code == MINUTE_EXIT_PASS_INCOMPLETE

    def test_a_non_firing_day_detail_says_trailing_was_skipped(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_WEDNESDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(
                return_value=_minute_report(
                    trailing_required=False,
                    trailing_completed=False,
                    trailing_attempted=0,
                )
            ),
        )
        _run_minute(runner)
        detail = rec.closed[0][3]
        assert detail is not None and "skipped" in detail

    def test_a_raising_cycle_closes_failed_with_the_exception_name(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(side_effect=ValueError("provider exploded")),
        )
        _run_minute(runner)
        assert len(rec.closed) == 1
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert exit_code == MINUTE_EXIT_PASS_INCOMPLETE
        assert detail == "ValueError"

    def test_the_row_it_closes_is_the_row_it_opened(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=MagicMock(return_value=_minute_report()),
        )
        _run_minute(runner)
        assert rec.closed[0][0] == uuid.UUID(int=1)

    def test_progress_reaches_the_recorder(self) -> None:
        """The cycle's on_progress callback must be wired to the row."""
        captured: dict[str, Any] = {}

        def _cycle(*, symbols=None, should_continue=None, on_progress=None):
            captured["on_progress"] = on_progress
            if on_progress is not None:
                on_progress("trailing", 250, 15215)
            return _minute_report()

        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute"}),
            minute_func=_cycle,
        )
        _run_minute(runner)
        assert captured["on_progress"] is not None
        assert rec.progressed == [(uuid.UUID(int=1), "trailing", 250, 15215)]


class TestDailyWriter:
    def test_it_opens_a_row_anchored_on_the_pass_boundary(self) -> None:
        rec = FakeRecorder()
        now = _SATURDAY
        runner = _runner(
            recorder=rec,
            now=now,
            granularities=frozenset({"daily"}),
            daily_func=MagicMock(return_value=CycleReport(daily_pass_completed=True)),
        )
        runner.start()
        assert rec.opened_kinds() == [PassKind.DAILY]
        assert rec.opened[0][1] == daily_pass_boundary(now)

    def test_a_completed_walk_closes_complete_at_exit_zero(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"daily"}),
            daily_func=MagicMock(
                return_value=CycleReport(daily_pass_completed=True, success_count=1200)
            ),
        )
        runner.start()
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == DAILY_EXIT_OK == 0
        assert detail == "1200 symbols attempted"

    def test_a_pass_that_stopped_early_closes_incomplete(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"daily"}),
            daily_func=MagicMock(return_value=CycleReport(daily_pass_completed=False)),
        )
        runner.start()
        assert rec.closed_outcomes() == [PassRunOutcome.INCOMPLETE]

    def test_nothing_actionable_says_so_in_the_detail(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"daily"}),
            daily_func=MagicMock(
                return_value=CycleReport(
                    nothing_actionable=True, daily_pass_completed=True
                )
            ),
        )
        runner.start()
        assert rec.closed[0][3] == "no actionable work"

    def test_a_raising_cycle_closes_failed_without_changing_the_exit_code(
        self,
    ) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"daily"}),
            daily_func=MagicMock(side_effect=RuntimeError("boom")),
        )
        code = runner.start()
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert detail == "RuntimeError"
        # Decision 5: a daily failure does not colour the process exit code.
        assert exit_code is None
        assert code == 0


class TestExplicitScopeWritesNothing:
    """Decision 3: only an all-active cycle records."""

    def test_no_recorder_means_no_calls_and_no_crash(self) -> None:
        runner = _runner(
            recorder=None,
            now=_SATURDAY,
            granularities=frozenset({"minute", "daily"}),
            scope=("AAPL", "MSFT"),
            minute_func=MagicMock(return_value=_minute_report()),
            daily_func=MagicMock(return_value=CycleReport(daily_pass_completed=True)),
        )
        assert runner.start() == 0

    def test_the_cli_builds_no_recorder_for_an_explicit_scope(self) -> None:
        """The decision lives at the construction site, so assert it there."""
        explicit = RunnerConfig(scope=("AAPL",), granularities=frozenset({"minute"}))
        all_active = RunnerConfig(
            scope=SCOPE_ALL_ACTIVE, granularities=frozenset({"minute"})
        )
        assert explicit.is_explicit_scope() is True
        assert all_active.is_explicit_scope() is False


class TestBothCyclesInOneIteration:
    def test_each_cycle_gets_its_own_row(self) -> None:
        rec = FakeRecorder()
        runner = _runner(
            recorder=rec,
            now=_SATURDAY,
            granularities=frozenset({"minute", "daily"}),
            minute_func=MagicMock(return_value=_minute_report()),
            daily_func=MagicMock(return_value=CycleReport(daily_pass_completed=True)),
        )
        runner.start()
        assert set(rec.opened_kinds()) == {PassKind.MINUTE, PassKind.DAILY}
        assert len(rec.closed) == 2
        assert len({run_id for run_id, _, _, _ in rec.closed}) == 2
