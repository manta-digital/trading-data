"""Unit tests for RateLimiter — verifies fixed concurrent behavior where lock is
released during sleep so concurrent callers are not serialized on the lock."""

from __future__ import annotations

import asyncio
import time

import pytest

from manta_trading.util.ratelimiter import RateLimiter


@pytest.mark.asyncio
async def test_single_call_records_timestamp() -> None:
    """One acquire under the rate limit completes immediately and records a call."""
    rl = RateLimiter(max_calls=5, period=10.0)
    async with rl:
        pass
    assert len(rl.calls) == 1


@pytest.mark.asyncio
async def test_n_calls_at_limit_forces_wait() -> None:
    """Third rapid acquire when max_calls=2 must wait approximately one period."""
    rl = RateLimiter(max_calls=2, period=1.0)
    async with rl:
        pass
    async with rl:
        pass

    start = time.monotonic()
    async with rl:
        pass
    elapsed = time.monotonic() - start

    assert elapsed > 0.9, f"Expected wait > 0.9s but got {elapsed:.3f}s"
    assert elapsed < 1.5, f"Expected wait < 1.5s but got {elapsed:.3f}s"


@pytest.mark.asyncio
async def test_concurrent_acquires_do_not_serialize_on_lock() -> None:
    """Two coroutines both waiting on rate limit should not be serialized on the lock.

    Both should start their rate-limit waits within 50 ms of each other — not
    staggered by the full sleep duration of the first.
    """
    rl = RateLimiter(max_calls=2, period=2.0)

    # Fill the bucket so both coroutines will need to wait
    async with rl:
        pass
    async with rl:
        pass

    start_times: list[float] = []

    async def enter_rl() -> None:
        t = time.monotonic()
        start_times.append(t)
        async with rl:
            pass

    t1 = asyncio.create_task(enter_rl())
    # Give the first task a moment to begin its wait
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(enter_rl())

    await asyncio.gather(t1, t2)

    assert len(start_times) == 2
    gap = abs(start_times[1] - start_times[0])
    assert gap < 0.05, (
        f"Second coroutine started {gap:.3f}s after first — "
        "suggests lock serialization rather than independent rate-limit waits"
    )


@pytest.mark.asyncio
async def test_release_is_noop() -> None:
    """release() after acquire() produces no error and does not change state."""
    rl = RateLimiter(max_calls=5, period=10.0)
    async with rl:
        pass
    calls_before = list(rl.calls)
    await rl.release()
    assert rl.calls == calls_before


@pytest.mark.asyncio
async def test_exit_context_manager_is_noop() -> None:
    """Entering and exiting as context manager adds exactly one call entry."""
    rl = RateLimiter(max_calls=5, period=10.0)
    assert len(rl.calls) == 0
    async with rl:
        pass
    assert len(rl.calls) == 1
    # Exiting again should not change state
    async with rl:
        pass
    assert len(rl.calls) == 2
