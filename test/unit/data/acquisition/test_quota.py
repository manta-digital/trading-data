"""Unit tests for ``manta_trading.data.acquisition.quota``."""

from __future__ import annotations

import pytest

from manta_trading.constants import (
    EODHD_BULK_EOD_BASE_COST,
    EODHD_DAILY_QUOTA,
    EODHD_EOD_CALL_COST,
    EODHD_INTRADAY_CALL_COST,
    EODHD_PER_MINUTE_BURST,
)
from manta_trading.data.acquisition.quota import (
    CallType,
    QuotaBucket,
    QuotaWaitAborted,
)


class FakeClock:
    """Drives both ``now`` and ``sleep`` for deterministic time control.

    ``sleep`` advances the virtual clock — no wall time elapses.
    """

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def make_bucket() -> tuple[QuotaBucket, FakeClock]:
    clock = FakeClock()
    return QuotaBucket(now=clock.now, sleep=clock.sleep), clock


def test_cost_for_returns_constants():
    bucket, _ = make_bucket()
    assert bucket.cost_for(CallType.EOD) == EODHD_EOD_CALL_COST
    assert bucket.cost_for(CallType.INTRADAY) == EODHD_INTRADAY_CALL_COST
    assert bucket.cost_for(CallType.BULK_EOD) == EODHD_BULK_EOD_BASE_COST


def test_consume_within_capacity_does_not_sleep():
    bucket, clock = make_bucket()
    bucket.consume(CallType.EOD)
    assert clock.sleeps == []
    assert bucket.spent_today() == EODHD_EOD_CALL_COST


def test_consume_blocks_when_minute_burst_exhausted():
    bucket, clock = make_bucket()
    # Fill the minute burst exactly.
    for _ in range(EODHD_PER_MINUTE_BURST):
        bucket.consume(CallType.EOD)
    assert clock.sleeps == []
    # Next consume must wait — minute window is empty, refill rate 1000/60s.
    bucket.consume(CallType.EOD)
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] > 0.0


def test_consume_blocks_when_daily_quota_exhausted():
    bucket, clock = make_bucket()
    # Drain the day window directly (the rolling-quota refill path is
    # exercised separately via spent_today; here we test that an empty
    # day_window forces a wait even when the minute window is full).
    bucket.day_window.available = 0.0
    bucket.day_window.last_refill = clock.now()
    bucket.consume(CallType.EOD)
    assert clock.sleeps and clock.sleeps[-1] > 0.0
    # The wait must be sized to the day window's refill rate
    # (100k / 86400s ≈ 1.16 credits/s), not the minute window's. For 1
    # credit, that's roughly 0.86s — far larger than a minute-window
    # wait of ~0.06s would be.
    assert clock.sleeps[-1] > 0.5


def test_spent_today_drops_entries_older_than_24h():
    bucket, clock = make_bucket()
    bucket.consume(CallType.EOD)
    bucket.consume(CallType.INTRADAY)
    assert bucket.spent_today() == EODHD_EOD_CALL_COST + EODHD_INTRADAY_CALL_COST
    # Jump past the rolling 24h window.
    clock.t += 86400.0 + 1.0
    assert bucket.spent_today() == 0


def test_clock_jump_backwards_does_not_overgrant():
    bucket, clock = make_bucket()
    # Drain the minute window.
    for _ in range(EODHD_PER_MINUTE_BURST):
        bucket.consume(CallType.EOD)
    # NTP correction: clock jumps backward.
    clock.t -= 30.0
    # Available must not exceed capacity, and the next consume must
    # still need to wait (the negative elapsed must not refill the
    # window).
    bucket.consume(CallType.EOD)
    assert clock.sleeps and clock.sleeps[-1] > 0.0
    assert bucket.minute_window.available <= float(EODHD_PER_MINUTE_BURST)


def test_consume_rejects_oversized_call_type():
    # Cost greater than the daily quota would loop forever; bucket must
    # reject explicitly.
    bucket, _ = make_bucket()
    bucket.day_window.capacity = 1  # force a tiny window for the assertion
    bucket.day_window.available = 0.0
    bucket.day_window.window_seconds = 60.0
    with pytest.raises(ValueError):
        bucket.consume(CallType.BULK_EOD)


# ---------------------------------------------------------------------------
# stop_requested — shutdown must abort a wait, never resume it (20260807)
# ---------------------------------------------------------------------------


def test_stop_requested_aborts_before_sleeping():
    bucket, clock = make_bucket()
    bucket.stop_requested = lambda: True
    bucket.minute_window.available = 0.0
    spent_before = bucket.spent_today()
    with pytest.raises(QuotaWaitAborted):
        bucket.consume(CallType.EOD)
    assert clock.sleeps == []
    assert bucket.spent_today() == spent_before  # nothing deducted


def test_stop_requested_flipping_mid_wait_aborts():
    bucket, clock = make_bucket()
    flag = {"stop": False}
    bucket.stop_requested = lambda: flag["stop"]
    # Empty the day window: a full wait would be ~0.86s per credit — but
    # make it minutes long so only the flag can end the loop early.
    bucket.day_window.available = -500.0
    bucket.day_window.last_refill = clock.now()

    original_sleep = clock.sleep

    def sleep_then_flag(seconds: float) -> None:
        original_sleep(seconds)
        if len(clock.sleeps) == 3:
            flag["stop"] = True

    bucket.sleep = sleep_then_flag
    with pytest.raises(QuotaWaitAborted):
        bucket.consume(CallType.EOD)
    assert len(clock.sleeps) == 3


def test_stop_requested_caps_sleep_slices():
    bucket, clock = make_bucket()
    bucket.stop_requested = lambda: False
    # Force a multi-second wait; slices must be capped at 1s each so the
    # flag is observed promptly, and consume still completes normally.
    bucket.day_window.available = -5.0
    bucket.day_window.last_refill = clock.now()
    bucket.consume(CallType.EOD)
    assert len(clock.sleeps) > 1
    assert all(s <= 1.0 for s in clock.sleeps)
    assert bucket.spent_today() == EODHD_EOD_CALL_COST


def test_stop_requested_unset_sleeps_full_wait():
    # Without a stop hook (CLI one-shot buckets) behavior is unchanged:
    # one full-length sleep, no slicing.
    bucket, clock = make_bucket()
    bucket.day_window.available = -5.0
    bucket.day_window.last_refill = clock.now()
    bucket.consume(CallType.EOD)
    assert len(clock.sleeps) == 1
    assert clock.sleeps[0] > 1.0
