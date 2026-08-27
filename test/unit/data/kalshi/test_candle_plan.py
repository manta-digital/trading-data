"""Planner tests (slice 264, Task 3.3): window arithmetic and packing.

Pure functions, so every case is exact. The caps come from ``constants.py``
— the planner is proven against the numbers the phase will use — and the
randomized invariant test is seeded so a failure reproduces.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from manta_trading.data.kalshi.candle_plan import (
    CandleBatch,
    CandleTarget,
    PendingMarket,
    last_complete_period,
    periods_in,
    plan_batches,
    target_window,
)
from manta_trading.data.kalshi.constants import (
    CANDLE_BATCH_MAX_CANDLES,
    CANDLE_BATCH_MAX_TICKERS,
    CANDLE_FIRST_SIGHT_LOOKBACK,
    COLLECTED_CANDLE_PERIOD,
    CandlePeriod,
)

PERIOD = COLLECTED_CANDLE_PERIOD
MINUTE = timedelta(minutes=1)
PHASE_START = datetime(2026, 8, 27, 14, 20, 11, tzinfo=UTC)
#: ``floor(PHASE_START) − 1 min`` — Decision 3's one-period guard.
LAST_COMPLETE = datetime(2026, 8, 27, 14, 19, tzinfo=UTC)


def market(
    ticker: str = "M",
    *,
    open_time: datetime,
    close_time: datetime,
    watermark_ts: datetime | None = None,
) -> PendingMarket:
    return PendingMarket(ticker, open_time, close_time, watermark_ts)


def window(market: PendingMarket) -> CandleTarget | None:
    return target_window(
        market,
        phase_start=PHASE_START,
        period=PERIOD,
        lookback=CANDLE_FIRST_SIGHT_LOOKBACK,
    )


def pack(targets: list[CandleTarget]) -> list[CandleBatch]:
    return plan_batches(
        targets,
        period=PERIOD,
        max_tickers=CANDLE_BATCH_MAX_TICKERS,
        max_candles=CANDLE_BATCH_MAX_CANDLES,
    )


def target(ticker: str, start: datetime, minutes: int) -> CandleTarget:
    end = start + MINUTE * minutes
    return CandleTarget(ticker, start, end, end)


class TestLastCompletePeriod:
    def test_mid_period_floors_then_steps_back_one(self):
        assert last_complete_period(PHASE_START, PERIOD) == LAST_COMPLETE

    def test_exact_boundary_steps_back_one(self):
        boundary = datetime(2026, 8, 27, 14, 20, tzinfo=UTC)
        assert last_complete_period(boundary, PERIOD) == LAST_COMPLETE

    def test_hourly_period(self):
        assert last_complete_period(PHASE_START, CandlePeriod.HOUR) == datetime(
            2026, 8, 27, 13, tzinfo=UTC
        )


class TestTargetWindow:
    def test_first_seen_young_starts_at_open(self):
        opened = PHASE_START - timedelta(hours=3)
        t = window(market(open_time=opened, close_time=PHASE_START + timedelta(days=1)))
        assert t is not None
        assert t.start == opened
        assert t.end == LAST_COMPLETE

    def test_first_seen_old_starts_at_lookback(self):
        t = window(
            market(
                open_time=PHASE_START - timedelta(days=100),
                close_time=PHASE_START + timedelta(days=1),
            )
        )
        assert t is not None
        assert t.start == PHASE_START - CANDLE_FIRST_SIGHT_LOOKBACK

    def test_past_close_is_clamped_to_close_plus_period(self):
        opened = PHASE_START - timedelta(hours=2)
        closed = PHASE_START - timedelta(hours=1)
        t = window(market(open_time=opened, close_time=closed))
        assert t is not None
        assert t.start == opened
        assert t.end == closed + MINUTE
        assert t.close_end == closed + MINUTE

    def test_finalized_between_passes_uses_close_for_the_lookback(self):
        """A ladder that closed long ago but was never seen: the lookback is
        measured from its close, not from now — so its whole life is fetched."""
        opened = PHASE_START - timedelta(days=3)
        closed = opened + timedelta(minutes=15)
        t = window(market(open_time=opened, close_time=closed))
        assert t is not None
        assert t.start == opened
        assert t.end == closed + MINUTE

    def test_already_complete_returns_none(self):
        closed = PHASE_START - timedelta(hours=1)
        t = window(
            market(
                open_time=closed - timedelta(hours=1),
                close_time=closed,
                watermark_ts=closed + MINUTE,
            )
        )
        assert t is None

    def test_nothing_new_since_watermark_returns_none(self):
        t = window(
            market(
                open_time=PHASE_START - timedelta(days=1),
                close_time=PHASE_START + timedelta(days=1),
                watermark_ts=LAST_COMPLETE,
            )
        )
        assert t is None

    def test_existing_watermark_is_the_start(self):
        mark = PHASE_START - timedelta(hours=1)
        t = window(
            market(
                open_time=PHASE_START - timedelta(days=30),
                close_time=PHASE_START + timedelta(days=1),
                watermark_ts=mark,
            )
        )
        assert t is not None
        assert t.start == mark
        assert t.end == LAST_COMPLETE


class TestPeriodsIn:
    def test_aligned_window_counts_exactly(self):
        start = datetime(2026, 8, 27, tzinfo=UTC)
        assert periods_in(start, start + timedelta(minutes=360), PERIOD) == 360

    def test_unaligned_window_rounds_up(self):
        start = datetime(2026, 8, 27, 0, 0, 30, tzinfo=UTC)
        assert periods_in(start, start + timedelta(minutes=5, seconds=1), PERIOD) == 6


def _within_caps(batch: CandleBatch) -> bool:
    requested = len(batch.tickers) * periods_in(batch.start, batch.end, PERIOD)
    return (
        len(batch.tickers) <= CANDLE_BATCH_MAX_TICKERS
        and requested <= CANDLE_BATCH_MAX_CANDLES
    )


def _covered(t: CandleTarget, batches: list[CandleBatch]) -> bool:
    """Every minute of ``[t.start, t.end)`` lies in a batch naming its ticker."""
    windows = sorted((b.start, b.end) for b in batches if t.ticker in b.tickers)
    cursor = t.start
    for start, end in windows:
        if start > cursor:
            return False
        cursor = max(cursor, end)
        if cursor >= t.end:
            return True
    return cursor >= t.end


class TestPlanBatches:
    def test_steady_state_hour_fits_one_hundred_tickers(self):
        start = LAST_COMPLETE - timedelta(minutes=60)
        targets = [target(f"M{i:03}", start, 60) for i in range(150)]
        batches = pack(targets)
        assert [len(b.tickers) for b in batches] == [100, 50]
        assert all(_within_caps(b) for b in batches)

    def test_first_sight_day_packs_six_per_request(self):
        """1,440 periods → ⌊10,000 / 1,440⌋ = 6 tickers (design Workload)."""
        start = LAST_COMPLETE - timedelta(minutes=1440)
        targets = [target(f"M{i:03}", start, 1440) for i in range(13)]
        batches = pack(targets)
        assert [len(b.tickers) for b in batches] == [6, 6, 1]

    def test_over_long_single_target_splits_into_tiling_windows(self):
        start = datetime(2026, 8, 1, tzinfo=UTC)
        minutes = CANDLE_BATCH_MAX_CANDLES * 2 + 7
        t = target("LONG", start, minutes)
        batches = pack([t])
        assert len(batches) == 3
        assert all(b.tickers == ("LONG",) for b in batches)
        assert batches[0].start == t.start
        assert batches[-1].end == t.end
        for earlier, later in zip(batches, batches[1:], strict=False):
            assert earlier.end == later.start
        assert all(_within_caps(b) for b in batches)
        assert _covered(t, batches)

    def test_distant_starts_do_not_share_a_batch_when_union_breaches_cap(self):
        base = datetime(2026, 8, 20, tzinfo=UTC)
        near = target("NEAR", base, 60)
        far = target("FAR", base + timedelta(days=5), 60)
        batches = pack([far, near])
        assert [b.tickers for b in batches] == [("NEAR",), ("FAR",)]
        assert all(_within_caps(b) for b in batches)

    def test_union_window_is_what_is_requested(self):
        base = datetime(2026, 8, 20, tzinfo=UTC)
        a = target("A", base, 30)
        b = target("B", base + timedelta(minutes=30), 30)
        batches = pack([a, b])
        assert len(batches) == 1
        assert batches[0].start == a.start
        assert batches[0].end == b.end

    def test_deterministic_in_any_input_order(self):
        rng = random.Random(264)
        base = datetime(2026, 8, 20, tzinfo=UTC)
        targets = [
            target(f"T{i:03}", base + timedelta(minutes=rng.randrange(0, 3000)), 60)
            for i in range(40)
        ]
        shuffled = list(targets)
        rng.shuffle(shuffled)
        assert pack(shuffled) == pack(targets)
        assert pack(list(reversed(targets))) == pack(targets)

    def test_empty(self):
        assert pack([]) == []

    def test_over_cap_batch_is_refused_not_sent(self):
        """Decision 7: the guard is explicit, so a packing bug would raise
        here rather than draw a 400 from the endpoint."""
        from manta_trading.data.kalshi import candle_plan

        start = datetime(2026, 8, 20, tzinfo=UTC)
        batch = CandleBatch(("A", "B"), start, start + timedelta(minutes=6000))
        with pytest.raises(AssertionError, match="over-cap"):
            candle_plan._check_caps(  # pyright: ignore[reportPrivateUsage]
                batch, PERIOD, CANDLE_BATCH_MAX_TICKERS, CANDLE_BATCH_MAX_CANDLES
            )


class TestRandomizedInvariant:
    """Criterion 5: for every batch both caps hold, and every target's
    ``[start, end)`` is fully covered by the batches naming its ticker."""

    @pytest.mark.parametrize("seed", [1, 2, 3, 264, 2026])
    def test_caps_hold_and_coverage_is_complete(self, seed: int):
        rng = random.Random(seed)
        base = datetime(2026, 8, 1, tzinfo=UTC)
        targets: list[CandleTarget] = []
        for i in range(rng.randrange(1, 400)):
            start = base + timedelta(minutes=rng.randrange(0, 60 * 24 * 40))
            minutes = rng.choice([1, 15, 60, 61, 1440, 1441, 12_000, 25_000])
            # Unaligned seconds on some starts — an open_time is not aligned.
            start += timedelta(seconds=rng.choice([0, 0, 17, 59]))
            targets.append(target(f"T{i:04}", start, minutes))
        batches = pack(targets)
        assert all(_within_caps(b) for b in batches)
        for t in targets:
            assert _covered(t, batches), t
        assert pack(list(reversed(targets))) == batches
