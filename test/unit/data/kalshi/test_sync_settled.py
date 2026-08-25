"""Sync core: the settled stream in windows (slice 262, Task 5.6)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast

import pytest

from manta_trading.data.kalshi.constants import (
    KALSHI_MVE_FILTER,
    SETTLED_WINDOW,
    WINDOW_OVERLAP,
    Surface,
)
from manta_trading.data.kalshi.sync import SyncPhase, epoch
from manta_trading.providers.errors import ProviderTransientError

from ._sync_harness import NOW, Harness


@pytest.fixture
def h() -> Harness:
    harness = Harness()
    harness.seed_parents()
    return harness


def window_queries(h: Harness) -> list[tuple[int, int]]:
    return [
        (cast(int, q["min_settled_ts"]), cast(int, q["max_settled_ts"]))
        for q in h.source.markets_queries
        if q.get("min_settled_ts") is not None and q.get("cursor") is None
    ]


def expected_windows(floor: datetime, end: datetime) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    a = floor
    while a < end:
        b = min(a + SETTLED_WINDOW, end)
        out.append((epoch(a - WINDOW_OVERLAP), epoch(b)))
        a = b
    return out


class TestFloor:
    async def test_settled_since_wins(self, h: Harness):
        async with h.repo.transaction():
            await h.repo.set_watermark(Surface.CATALOG, NOW - timedelta(hours=3))
        since = NOW - timedelta(hours=2)
        await h.core.run(settled_since=since)
        assert window_queries(h)[0] == (epoch(since - WINDOW_OVERLAP), epoch(NOW))
        assert "get_historical_cutoff" not in h.source.calls

    async def test_watermark_when_no_override(self, h: Harness):
        mark = NOW - timedelta(hours=3)
        async with h.repo.transaction():
            await h.repo.set_watermark(Surface.CATALOG, mark)
        await h.core.run()
        assert window_queries(h)[0][0] == epoch(mark - WINDOW_OVERLAP)
        assert "get_historical_cutoff" not in h.source.calls

    async def test_historical_cutoff_on_first_run(self, h: Harness):
        cutoff = h.source.cutoff.market_settled_ts
        await h.core.run()
        assert "get_historical_cutoff" in h.source.calls
        assert window_queries(h) == expected_windows(cutoff, NOW)


class TestWindows:
    async def test_boundaries_overlap_mve_and_watermark_per_window(self, h: Harness):
        floor = NOW - timedelta(hours=20)
        h.settled_market("A", NOW - timedelta(hours=19))
        h.settled_market("B", NOW - timedelta(hours=1))
        result = await h.core.run(settled_since=floor)
        assert window_queries(h) == expected_windows(floor, NOW)
        assert len(window_queries(h)) == 4
        settled_q = [
            q for q in h.source.markets_queries if q.get("min_settled_ts") is not None
        ]
        assert all(q["mve_filter"] == KALSHI_MVE_FILTER for q in settled_q)
        marks = [n for m, n in h.repo.writes if m == "set_watermark"]
        assert len(marks) == 4
        assert result.windows_completed == 4
        assert result.settled_captured == 2 and result.watermark_ts == NOW
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None and state.watermark_ts == NOW

    async def test_sub_window_drain_is_one_clamped_window(self, h: Harness):
        since = NOW - timedelta(hours=2)
        h.settled_market("A", NOW - timedelta(minutes=30))
        result = await h.core.run(settled_since=since)
        assert window_queries(h) == [(epoch(since - WINDOW_OVERLAP), epoch(NOW))]
        assert result.windows_completed == 1
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None and state.watermark_ts == NOW
        assert set(h.repo.markets) == {"A"}

    async def test_abort_in_window_three_keeps_window_two_and_resumes(self, h: Harness):
        floor = NOW - timedelta(hours=20)
        windows = expected_windows(floor, NOW)
        h.settled_market("W1", floor + timedelta(hours=1))
        h.settled_market("W3", floor + timedelta(hours=13))
        h.source.raise_on(
            "get_markets",
            ProviderTransientError("503"),
            when=lambda q: q.get("min_settled_ts") == windows[2][0],
        )
        with pytest.raises(ProviderTransientError):
            await h.core.run(settled_since=floor)
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None
        assert state.watermark_ts == floor + 2 * SETTLED_WINDOW
        assert state.last_full_sync_at is None
        assert h.sink.events[-1].error == "ProviderTransientError: 503"

        h.source._failures.clear()
        h.new_core()
        await h.core.run()
        resumed = window_queries(h)[len(windows) - 1 :]
        assert resumed == windows[2:]
        assert set(h.repo.markets) == {"W1", "W3"}

    async def test_overlap_duplicates_cost_zero_writes(self, h: Harness):
        floor = NOW - timedelta(hours=12)
        boundary = floor + SETTLED_WINDOW
        h.settled_market("EDGE", boundary - timedelta(milliseconds=500))
        result = await h.core.run(settled_since=floor)
        settled = result.phases[SyncPhase.SETTLED]
        assert settled.fetched == 2, (
            "served by both windows (strict bounds + 1 s overlap)"
        )
        assert settled.written == 1 and settled.unchanged == 1
        assert result.settled_captured == 1

    async def test_settled_since_never_moves_watermark_backwards(self, h: Harness):
        mark = NOW - timedelta(minutes=10)
        async with h.repo.transaction():
            await h.repo.set_watermark(Surface.CATALOG, mark)
        h.repo.writes.clear()
        await h.core.run(settled_since=NOW - timedelta(hours=20))
        marks = [n for m, n in h.repo.writes if m == "set_watermark"]
        assert marks == [1], "only the clamped last window is ahead of the watermark"
        state = await h.repo.get_sync_state(Surface.CATALOG)
        assert state is not None and state.watermark_ts == NOW
