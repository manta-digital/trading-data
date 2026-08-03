"""Slice 912 Task 5.1/5.2/5.5 — resume, cadence, and termination end to end.

These drive the real ``Runner`` loop over simulated time against durable
per-symbol state, so they assert the property the slice exists for rather than
any single function's contract: **an interrupted daily pass resumes at exactly
the symbols it never reached, within the same UTC day.**

Before slice 912 that was false in two independent ways — the runner recorded a
cycle *start* before running it, and gated the next run on the UTC day — and
the second defect hid the first, because every restart re-ran the whole pass.
See ``project-documents/user/notes/912-main-behavior-proof.md`` for the
execution of these scenarios against the pre-912 tree.
"""

from __future__ import annotations

import logging
import math
from unittest.mock import MagicMock

import pytest

from manta_trading.constants import (
    DAILY_CYCLE_RETRY_INTERVAL,
    DAILY_CYCLE_START_OFFSET,
    CycleGranularity,
)
from manta_trading.data.acquisition.daemon.daily import CycleReport
from manta_trading.data.acquisition.daemon.runner import Runner, RunnerConfig
from manta_trading.data.acquisition.quota import QuotaBucket

from ._harness import (
    AdvancingClock,
    FakeAcquisitionState,
    Interrupt,
    RecordingDailyCycle,
    SimulationEnded,
)

SYMBOLS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")

DAILY = frozenset({CycleGranularity.DAILY})


def _runner(
    *,
    clock: AdvancingClock,
    daily: RecordingDailyCycle,
    conn_factory,
    sleep,
    terminate_when_drained: bool,
    scope: tuple[str, ...] = SYMBOLS,
) -> Runner:
    return Runner(
        config=RunnerConfig(
            scope=scope,
            granularities=DAILY,
            terminate_when_drained=terminate_when_drained,
        ),
        bucket=QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None),
        conn_factory=conn_factory,
        run_daily_cycle=daily,
        run_minute_cycle=MagicMock(return_value=CycleReport()),
        run_ca_update=MagicMock(),
        clock=clock,
        sleep=sleep,
    )


# ---------------------------------------------------------------------------
# 5.1 — an interrupted pass resumes at the unreached symbols
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_interrupted_pass_resumes_at_unreached_symbols(today_at, ca_update_done):
    """A pass that dies partway is retried the same day, minus what it reached.

    Success criterion 1. The first cycle reaches two of six symbols and then
    crashes; the retry one cadence interval later must be handed the other four,
    in order, with no re-fetch of the two already attempted.
    """
    clock = AdvancingClock(today_at(12, 0))
    store = FakeAcquisitionState()
    cycle = RecordingDailyCycle(
        store=store,
        clock=clock,
        stop_after_symbols=2,
        interrupt=Interrupt.RAISE,
    )
    runner = _runner(
        clock=clock,
        daily=cycle,
        conn_factory=ca_update_done(clock),
        sleep=clock.sleep_until(today_at(13, 0)),
        terminate_when_drained=False,
    )

    with pytest.raises(SimulationEnded):
        runner.start()

    assert len(cycle.pending_seen) >= 2, "the pass was never retried"
    assert cycle.pending_seen[0] == list(SYMBOLS)
    assert cycle.pending_seen[1] == list(SYMBOLS[2:]), (
        "the retry must resume at the unreached symbols, in order"
    )


@pytest.mark.timeout(10)
def test_restart_resumes_at_unreached_symbols(today_at, ca_update_done):
    """The observed production incident, as a test.

    A pass interrupted by SIGTERM was recovered by an operator restart, not by
    the daemon. The restart must now pick up exactly where the previous process
    stopped — and immediately, without waiting out a cadence interval it has no
    memory of, because remaining work is derived rather than tracked.
    """
    clock = AdvancingClock(today_at(12, 0))
    store = FakeAcquisitionState()
    slept: list[float] = []

    interrupted = RecordingDailyCycle(
        store=store,
        clock=clock,
        stop_after_symbols=2,
        interrupt=Interrupt.SIGNAL,
    )
    first = _runner(
        clock=clock,
        daily=interrupted,
        conn_factory=ca_update_done(clock),
        sleep=slept.append,
        terminate_when_drained=False,
    )
    interrupted.runner = first

    assert first.start() == 0
    assert store.attempted() == set(SYMBOLS[:2])
    assert not slept, "the interrupted process should have exited, not waited"

    # A restart within the same UTC day, sooner than the retry interval: fresh
    # in-memory state must not be what decides whether work remains.
    clock.advance(5 * 60)
    assert clock.now - today_at(12, 0) < DAILY_CYCLE_RETRY_INTERVAL

    resumed = RecordingDailyCycle(store=store, clock=clock)
    second = _runner(
        clock=clock,
        daily=resumed,
        conn_factory=ca_update_done(clock),
        sleep=slept.append,
        terminate_when_drained=True,
    )

    assert second.start() == 0
    assert resumed.pending_seen[0] == list(SYMBOLS[2:])
    assert resumed.provider_calls == 1
    assert store.attempted() == set(SYMBOLS)


@pytest.mark.timeout(10)
def test_completed_pass_makes_no_further_provider_call_that_day(
    today_at, ca_update_done, caplog
):
    """The other half of D1: the derived work list has to terminate.

    Every symbol already attempted after today's pass boundary means a cycle
    that runs must derive nothing and cost nothing — otherwise each cadence tick
    would re-issue the billable bulk EOD call for the rest of the day.
    """
    clock = AdvancingClock(today_at(14, 0))
    store = FakeAcquisitionState()
    boundary = today_at(0, 0) + DAILY_CYCLE_START_OFFSET
    store.stamp_all(SYMBOLS, boundary)

    cycle = RecordingDailyCycle(store=store, clock=clock)
    runner = _runner(
        clock=clock,
        daily=cycle,
        conn_factory=ca_update_done(clock),
        sleep=lambda _s: pytest.fail("a drained scope should not have waited"),
        terminate_when_drained=True,
    )

    with caplog.at_level(logging.INFO):
        assert runner.start() == 0

    assert cycle.pending_seen == [[]]
    assert cycle.provider_calls == 0
    assert "no actionable work" in " ".join(r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 5.2 — cadence holds when there is nothing to do
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_no_busy_poll_when_nothing_is_actionable(today_at, ca_update_done):
    """Deriving work per tick must not become a per-tick database read.

    The cadence gate is the only thing standing between "work is derived, not
    tracked" and a hot loop against ``acquisition_state``. Across four simulated
    hours with nothing actionable, the work list may be derived at most once per
    ``DAILY_CYCLE_RETRY_INTERVAL``, and the provider must never be called.
    """
    start = today_at(12, 0)
    horizon = today_at(16, 0)
    clock = AdvancingClock(start)
    store = FakeAcquisitionState()
    store.stamp_all(SYMBOLS, today_at(0, 0) + DAILY_CYCLE_START_OFFSET)

    advance = clock.sleep_until(horizon)
    sleeps = {"n": 0}
    # Sleeps are capped at 60s for signal latency, so four hours costs ~240 of
    # them. A busy-poll trips this bound long before the clock reaches the
    # horizon, which turns a hang into a failed assertion below.
    max_sleeps = int((horizon - start).total_seconds() // 60) + 20

    def _sleep(seconds: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] > max_sleeps:
            raise SimulationEnded("sleep budget exhausted — suspected busy-poll")
        advance(seconds)

    cycle = RecordingDailyCycle(store=store, clock=clock)
    runner = _runner(
        clock=clock,
        daily=cycle,
        conn_factory=ca_update_done(clock),
        sleep=_sleep,
        terminate_when_drained=False,
    )

    with pytest.raises(SimulationEnded):
        runner.start()

    assert clock.now >= horizon, (
        f"only reached {clock.now:%H:%M} in {sleeps['n']} sleeps — "
        "the loop was spinning rather than waiting"
    )
    elapsed = (clock.now - start).total_seconds()
    max_cycles = math.ceil(elapsed / DAILY_CYCLE_RETRY_INTERVAL.total_seconds()) + 1
    assert 2 <= len(cycle.pending_seen) <= max_cycles
    assert cycle.provider_calls == 0


# ---------------------------------------------------------------------------
# 5.5 — a scope with nothing actionable in it terminates
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
def test_scope_of_only_calendar_less_symbols_terminates(
    today_at, ca_update_done, caplog
):
    """Issue #4's symbols must be reported and dropped, never retried forever.

    Leaving calendar-less symbols in the work list would make it non-terminating
    — they can never be stamped, so every cadence tick would find them pending.
    """
    clock = AdvancingClock(today_at(12, 0))
    store = FakeAcquisitionState(no_calendar=SYMBOLS)
    cycle = RecordingDailyCycle(store=store, clock=clock)
    runner = _runner(
        clock=clock,
        daily=cycle,
        conn_factory=ca_update_done(clock),
        sleep=lambda _s: pytest.fail("an unactionable scope should not have waited"),
        terminate_when_drained=True,
    )

    with caplog.at_level(logging.INFO):
        assert runner.start() == 0

    assert cycle.pending_seen == [[]]
    assert cycle.unactionable_seen == [len(SYMBOLS)]
    assert cycle.provider_calls == 0
    assert store.attempted() == set()
    assert "no actionable work" in " ".join(r.getMessage() for r in caplog.records)
