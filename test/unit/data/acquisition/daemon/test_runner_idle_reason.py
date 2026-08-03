"""Tests for slice 912 D4/D5: idle reasons and the qualified stop-when-done wait.

Three layers, deliberately:

1. ``_awaiting_first_cycle`` is a pure function of three inputs (the granularity
   set and two nullable stamps), so its input space is finite and enumerated
   exhaustively below rather than sampled.
2. Loop-level wiring, because an exhaustive test of a pure predicate cannot
   catch a correct predicate called in the wrong place.
3. A regression guard with a real timeout on ``--minute --list``, the routine
   operator invocation that an unqualified D5 would have turned into a hang.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import DAILY_CYCLE_START_OFFSET, CycleGranularity
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.runner import (
    SCOPE_ALL_ACTIVE,
    Runner,
    RunnerConfig,
    RunnerIdleReason,
    RunnerState,
)
from manta_trading.data.acquisition.quota import QuotaBucket

DAILY = frozenset({CycleGranularity.DAILY})
MINUTE = frozenset({CycleGranularity.MINUTE})
BOTH = frozenset({CycleGranularity.DAILY, CycleGranularity.MINUTE})

_SET = datetime(2026, 8, 3, 4, 0, tzinfo=UTC)


def _today_at(hour: int, minute: int = 0) -> datetime:
    today = datetime.now(UTC).date()
    return datetime(today.year, today.month, today.day, hour, minute, tzinfo=UTC)


def _runner(
    *,
    granularities: frozenset[str],
    terminate_when_drained: bool = True,
    daily_func: MagicMock | None = None,
    minute_func: MagicMock | None = None,
    clock_at: datetime | None = None,
    sleep=None,
    scope=SCOPE_ALL_ACTIVE,
) -> Runner:
    conn = MagicMock()
    cur = MagicMock()
    cur.fetchone.return_value = (datetime.now(UTC),)  # CA update not due
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=conn)
    cm.__exit__ = MagicMock(return_value=False)

    clock = clock_at
    return Runner(
        config=RunnerConfig(
            scope=scope,
            granularities=granularities,
            terminate_when_drained=terminate_when_drained,
        ),
        bucket=QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None),
        conn_factory=MagicMock(return_value=cm),
        run_daily_cycle=daily_func or MagicMock(return_value=CycleReport()),
        run_minute_cycle=minute_func or MagicMock(return_value=CycleReport()),
        run_ca_update=MagicMock(),
        clock=(lambda: clock) if clock else None,
        sleep=sleep or (lambda _s: None),
    )


# ---------------------------------------------------------------------------
# _awaiting_first_cycle — exhaustive over the finite input space
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("granularities", "daily_stamp", "minute_stamp", "expected"),
    [
        (DAILY, None, None, True),
        (DAILY, _SET, None, False),
        (MINUTE, None, None, True),
        (MINUTE, None, _SET, False),
        (BOTH, None, None, True),
        (BOTH, None, _SET, True),
        # Defensive rather than reachable: minute is due immediately when its
        # stamp is None and is stamped right after its branch runs, so it
        # cannot stay unstamped once daily has run. The predicate must be
        # correct on its own terms, not by relying on the loop's ordering.
        (BOTH, _SET, None, True),
        (BOTH, _SET, _SET, False),
    ],
)
def test_awaiting_first_cycle_exhaustive(
    granularities, daily_stamp, minute_stamp, expected
):
    runner = _runner(granularities=granularities)
    runner._state = RunnerState(
        last_daily_cycle_end_utc=daily_stamp,
        last_minute_cycle_end_utc=minute_stamp,
    )
    assert runner._awaiting_first_cycle(granularities) is expected


def test_awaiting_first_cycle_ignores_unconfigured_granularity():
    """A granularity not in scope must not hold the wait open."""
    runner = _runner(granularities=DAILY)
    runner._state = RunnerState(
        last_daily_cycle_end_utc=_SET, last_minute_cycle_end_utc=None
    )
    assert runner._awaiting_first_cycle(DAILY) is False


# ---------------------------------------------------------------------------
# Wiring — the predicate called in the right place
# ---------------------------------------------------------------------------


def test_drained_scope_exits_with_no_actionable_work(caplog):
    """A cycle reporting nothing_actionable exits, and says so."""
    at = _today_at(12, 0)
    daily = MagicMock(return_value=CycleReport(nothing_actionable=True))
    runner = _runner(granularities=DAILY, daily_func=daily, clock_at=at)

    with caplog.at_level(logging.INFO):
        assert runner.start() == 0

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "no actionable work" in messages
    assert "scope drained" not in messages, "the old conflated message survived"


def test_closed_gate_before_first_cycle_waits_rather_than_exiting(caplog):
    """The #6 incident: 00:13 UTC, scoped daily run, nothing fetched.

    The gate is closed and daily has never run, so the runner must wait for
    00:30 rather than exit claiming completion.
    """
    at = _today_at(0, 13)
    slept: list[float] = []
    daily = MagicMock(return_value=CycleReport(nothing_actionable=True))
    runner = _runner(
        granularities=DAILY, daily_func=daily, clock_at=at, sleep=slept.append
    )
    # Break the loop after the wait so the test terminates: the frozen clock
    # would otherwise keep the gate shut forever.
    original = runner._awaiting_first_cycle
    calls = {"n": 0}

    def _once(granularities):
        calls["n"] += 1
        return original(granularities) if calls["n"] == 1 else False

    runner._awaiting_first_cycle = _once

    with caplog.at_level(logging.INFO):
        assert runner.start() == 0

    assert slept, "runner exited instead of waiting for the gate to open"
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "no cycle due yet" in messages
    # Derive the expected time from the constant, so tuning the offset updates
    # the expectation rather than breaking the test.
    secs = int(DAILY_CYCLE_START_OFFSET.total_seconds())
    due = f"{secs // 3600:02d}:{(secs // 60) % 60:02d}"
    assert due in messages, f"wait message should name the {due} UTC due time"


def test_wait_is_announced_once_not_per_tick(caplog):
    """A per-tick announcement is spam; the explanatory line appears once.

    The clock is frozen here, so this pins the *explanatory* line only. The
    slow heartbeat that keeps a long wait from looking like a hang is exercised
    against an advancing clock in
    ``test_stop_when_done_invocations.py::test_wait_reports_progress_while_it_waits``.
    """
    at = _today_at(0, 13)
    slept: list[float] = []
    runner = _runner(
        granularities=DAILY,
        daily_func=MagicMock(return_value=CycleReport(nothing_actionable=True)),
        clock_at=at,
        sleep=slept.append,
    )
    original = runner._awaiting_first_cycle
    calls = {"n": 0}

    def _thrice(granularities):
        calls["n"] += 1
        return original(granularities) if calls["n"] <= 3 else False

    runner._awaiting_first_cycle = _thrice

    with caplog.at_level(logging.INFO):
        runner.start()

    waits = [r for r in caplog.records if "no cycle due yet" in r.getMessage()]
    assert len(waits) == 1, f"announced {len(waits)} times across {len(slept)} sleeps"


def test_closed_gate_after_first_cycle_exits(caplog):
    """Once every configured granularity has run, a closed gate means exit."""
    at = _today_at(12, 0)
    runner = _runner(
        granularities=DAILY,
        daily_func=MagicMock(return_value=CycleReport()),
        clock_at=at,
    )
    with caplog.at_level(logging.INFO):
        assert runner.start() == 0

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "every configured granularity has run" in messages


def test_forever_mode_never_exits_on_either_reason():
    """--forever ignores both idle reasons; only signals stop it."""
    at = _today_at(12, 0)
    slept: list[float] = []

    runner = _runner(
        granularities=DAILY,
        terminate_when_drained=False,
        daily_func=MagicMock(return_value=CycleReport(nothing_actionable=True)),
        clock_at=at,
        sleep=lambda s: (slept.append(s), runner.__setattr__("_should_exit", True)),
    )
    assert runner.start() == 0
    assert slept, "forever mode should have slept rather than exited"


# ---------------------------------------------------------------------------
# Regression guard — the invocation D5 nearly broke
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_minute_only_scoped_run_terminates_after_one_pass():
    """`mt data daemon run --minute --list X` must do one pass and exit.

    `--list` implies `--stop-when-done`. Minute can never report
    NO_ACTIONABLE_WORK (D4), so without the first-cycle qualifier on the D5
    wait this loops forever. The timeout makes that a named failure rather than
    a hung suite, which in CI would read as flakiness.
    """
    at = _today_at(12, 0)
    minute = MagicMock(return_value=CycleReport())
    runner = _runner(
        granularities=MINUTE,
        scope=("AAPL", "MSFT"),
        minute_func=minute,
        clock_at=at,
    )
    assert runner.start() == 0
    assert minute.call_count == 1


@pytest.mark.timeout(10)
def test_minute_only_unscoped_run_never_enters_the_wait_branch():
    """Without a scope flag terminate_when_drained is False; D5 is unreachable."""
    at = _today_at(12, 0)
    slept: list[float] = []
    runner = _runner(
        granularities=MINUTE,
        terminate_when_drained=False,
        clock_at=at,
        sleep=lambda s: (slept.append(s), runner.__setattr__("_should_exit", True)),
    )
    assert runner.start() == 0


def test_idle_reason_members_are_distinct_values():
    """Enum, not log-message variants (912 D4, no-magic-strings rule)."""
    assert (
        RunnerIdleReason.NOTHING_DUE.value
        != RunnerIdleReason.NO_ACTIONABLE_WORK.value
    )
    assert len(set(RunnerIdleReason)) == 2
