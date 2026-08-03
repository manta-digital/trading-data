"""Slice 912 Task 5.3/5.4 — the D5 stop-when-done wait, per invocation.

GitHub issue #6: a scoped daily run launched at 00:13 UTC exited immediately,
logging that its scope was drained when it had merely met a cadence gate that
had not opened yet. D5's fix is to sleep through that gate — but only while some
configured granularity has never completed a cycle, because without that
qualifier ``mt data daemon run --minute --list X`` would never terminate.

The design's invocation table is the contract, so it is asserted row by row
rather than sampled.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import DAILY_CYCLE_START_OFFSET, CycleGranularity
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.runner import (
    SCOPE_ALL_ACTIVE,
    Runner,
    RunnerConfig,
)
from manta_trading.data.acquisition.quota import QuotaBucket

from ._harness import (
    AdvancingClock,
    FakeAcquisitionState,
    RecordingDailyCycle,
    SimulationEnded,
)

SYMBOLS = ("AAA", "BBB", "CCC")

DAILY = frozenset({CycleGranularity.DAILY})
MINUTE = frozenset({CycleGranularity.MINUTE})
BOTH = frozenset({CycleGranularity.DAILY, CycleGranularity.MINUTE})

_ONE_HOUR = timedelta(hours=1)

WAIT_ANNOUNCEMENT = "no cycle due yet"
"""Emitted only by the D5 wait branch, so its presence is how a test tells a
qualified wait apart from the loop's ordinary between-cycle sleep."""


def _terminate_when_drained(scope: str | tuple[str, ...]) -> bool:
    """The CLI's rule, mirrored: a scope flag implies --stop-when-done.

    ``mt data daemon run`` derives this at ``cli/commands/data.py:1187`` as
    ``symbols_list is not None``. Asserting the table against the derived value
    rather than a hand-written one keeps the table honest about which
    invocations actually reach the D5 branch.
    """
    return scope != SCOPE_ALL_ACTIVE


def _build(
    *,
    clock: AdvancingClock,
    granularities: frozenset[str],
    scope: str | tuple[str, ...],
    daily,
    minute,
    sleep,
    ca_update_done,
) -> Runner:
    return Runner(
        config=RunnerConfig(
            scope=scope,
            granularities=granularities,
            terminate_when_drained=_terminate_when_drained(scope),
        ),
        bucket=QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None),
        conn_factory=ca_update_done(clock),
        run_daily_cycle=daily,
        run_minute_cycle=minute,
        run_ca_update=MagicMock(),
        clock=clock,
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# 5.3 — the #6 sequence: wait, then run, then exit
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
@pytest.mark.parametrize("pass_already_complete", [False, True])
def test_stop_when_done_waits_then_runs_then_exits(
    pass_already_complete, today_at, ca_update_done, caplog
):
    """00:13 UTC, scoped daily run: the full sequence, not just the wait.

    Exiting at 00:13 was issue #6. Waiting but never running would be a
    different bug wearing the same fix, so the cycle must be shown to run once
    the gate opens, and the loop to exit afterwards rather than settle into a
    steady-state poll.

    Which idle reason ends the run depends on what the cycle finds, and both are
    covered here: a scope with work exits once every granularity has run, while
    one already completed for the day exits reporting no actionable work. The
    task's shorthand named only the second.
    """
    clock = AdvancingClock(today_at(0, 13))
    gate_opens = today_at(0, 0) + DAILY_CYCLE_START_OFFSET
    store = FakeAcquisitionState()
    if pass_already_complete:
        store.stamp_all(SYMBOLS, gate_opens)

    slept: list[float] = []
    advance = clock.sleep_until(today_at(2, 0))

    def _sleep(seconds: float) -> None:
        slept.append(seconds)
        advance(seconds)

    cycle = RecordingDailyCycle(store=store, clock=clock)
    runner = _build(
        clock=clock,
        granularities=DAILY,
        scope=SYMBOLS,
        daily=cycle,
        minute=MagicMock(return_value=CycleReport()),
        sleep=_sleep,
        ca_update_done=ca_update_done,
    )

    with caplog.at_level(logging.INFO):
        assert runner.start() == 0, "the runner never exited"

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert slept, "exited at 00:13 instead of waiting for the gate — issue #6"
    assert WAIT_ANNOUNCEMENT in messages
    assert cycle.ran_at, "waited for the gate but never ran the cycle"
    assert cycle.ran_at[0] >= gate_opens
    assert len(cycle.ran_at) == 1, "should exit after the pass, not keep polling"

    if pass_already_complete:
        assert cycle.provider_calls == 0
        assert "no actionable work" in messages
    else:
        assert cycle.provider_calls == 1
        assert store.attempted() == set(SYMBOLS)
        assert "every configured granularity has run" in messages


# ---------------------------------------------------------------------------
# 5.4 — every row of the D5 invocation table
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Invocation:
    """One row of the D5 invocation table."""

    label: str
    granularities: frozenset[str]
    scope: str | tuple[str, ...]
    at_hour: int
    at_minute: int
    expect_terminates: bool
    expect_d5_wait: bool
    expect_daily_cycles: int
    min_minute_cycles: int
    max_minute_cycles: int | None
    """None where the minute cadence legitimately fires repeatedly while the
    loop waits on the daily gate; an exact bound everywhere it does not."""


TABLE = [
    _Invocation(
        label="--minute",
        granularities=MINUTE,
        scope=SCOPE_ALL_ACTIVE,
        at_hour=12,
        at_minute=0,
        # No scope flag, so --stop-when-done is off and the D5 branch is
        # unreachable: this is the steady-state daemon and must never exit.
        expect_terminates=False,
        expect_d5_wait=False,
        expect_daily_cycles=0,
        min_minute_cycles=1,
        max_minute_cycles=None,
    ),
    _Invocation(
        label="--minute --list X",
        granularities=MINUTE,
        scope=SYMBOLS,
        at_hour=12,
        at_minute=0,
        # The row that matters most: the PM's routine invocation, and the one an
        # unqualified D5 would have turned into a hang.
        expect_terminates=True,
        expect_d5_wait=False,
        expect_daily_cycles=0,
        min_minute_cycles=1,
        max_minute_cycles=1,
    ),
    _Invocation(
        label="--daily --list X at 00:13",
        granularities=DAILY,
        scope=SYMBOLS,
        at_hour=0,
        at_minute=13,
        expect_terminates=True,
        expect_d5_wait=True,
        expect_daily_cycles=1,
        min_minute_cycles=0,
        max_minute_cycles=0,
    ),
    _Invocation(
        label="--daily --list X at 14:00",
        granularities=DAILY,
        scope=SYMBOLS,
        at_hour=14,
        at_minute=0,
        expect_terminates=True,
        expect_d5_wait=False,
        expect_daily_cycles=1,
        min_minute_cycles=0,
        max_minute_cycles=0,
    ),
    _Invocation(
        label="--daily --minute --list X at 00:13",
        granularities=BOTH,
        scope=SYMBOLS,
        at_hour=0,
        at_minute=13,
        expect_terminates=True,
        expect_d5_wait=True,
        expect_daily_cycles=1,
        min_minute_cycles=1,
        max_minute_cycles=None,
    ),
]


@pytest.mark.timeout(10)
@pytest.mark.parametrize("row", TABLE, ids=lambda r: r.label)
def test_d5_invocation_table(row, today_at, ca_update_done, caplog):
    """Each tabulated invocation waits, or does not, and exits, or does not."""
    clock = AdvancingClock(today_at(row.at_hour, row.at_minute))
    store = FakeAcquisitionState()
    daily = RecordingDailyCycle(store=store, clock=clock)
    minute = MagicMock(return_value=CycleReport())

    # A horizon well past every gate in the table: rows that terminate do so
    # long before it, and the one that must not terminate reaches it.
    sleep = clock.sleep_until(today_at(row.at_hour, row.at_minute) + _ONE_HOUR)

    runner = _build(
        clock=clock,
        granularities=row.granularities,
        scope=row.scope,
        daily=daily,
        minute=minute,
        sleep=sleep,
        ca_update_done=ca_update_done,
    )

    with caplog.at_level(logging.INFO):
        if row.expect_terminates:
            assert runner.start() == 0, f"{row.label} did not terminate"
        else:
            with pytest.raises(SimulationEnded):
                runner.start()

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert (WAIT_ANNOUNCEMENT in messages) is row.expect_d5_wait
    assert len(daily.ran_at) == row.expect_daily_cycles
    assert minute.call_count >= row.min_minute_cycles
    if row.max_minute_cycles is not None:
        assert minute.call_count <= row.max_minute_cycles


def test_scope_flag_is_what_arms_the_stop_when_done_path():
    """The table's terminate column is derived, not asserted by hand."""
    assert _terminate_when_drained(SCOPE_ALL_ACTIVE) is False
    assert _terminate_when_drained(SYMBOLS) is True
