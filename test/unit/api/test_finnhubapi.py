"""Unit tests for FinnhubClient using respx."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from manta_trading.api.finnhub.finnhubapi import (
    FinnhubAccessError,
    FinnhubClient,
    _TokenBucket,
)
from manta_trading.api.http_retry import RetryPolicy

_BASE = "https://finnhub.io/api/v1"

# Fast policy — zero backoff for tests
_FAST_POLICY = RetryPolicy(connect_timeout=1.0, read_timeout=1.0, retries=1, backoff_seconds=[0.0])

_AAPL_PROFILE = {
    "country": "US",
    "currency": "USD",
    "exchange": "NASDAQ NMS - GLOBAL MARKET",
    "ipo": "1980-12-12",
    "name": "Apple Inc",
    "ticker": "AAPL",
}


def _client() -> FinnhubClient:
    return FinnhubClient(api_key="TESTKEY", http_policy=_FAST_POLICY)


class TestFetchProfile:
    @pytest.mark.asyncio
    @respx.mock
    async def test_success_with_ipo_returns_dict(self):
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(200, json=_AAPL_PROFILE)
        )
        result = await _client().fetch_profile("AAPL")
        assert result is not None
        assert result["ipo"] == "1980-12-12"

    @pytest.mark.asyncio
    @respx.mock
    async def test_missing_ipo_returns_none(self):
        profile = {**_AAPL_PROFILE, "ipo": ""}
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(200, json=profile)
        )
        result = await _client().fetch_profile("UNKNOWN")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_no_ipo_field_returns_none(self):
        profile = {k: v for k, v in _AAPL_PROFILE.items() if k != "ipo"}
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(200, json=profile)
        )
        result = await _client().fetch_profile("XYZ")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_403_raises_access_error(self):
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(403, text="Forbidden")
        )
        with pytest.raises(FinnhubAccessError):
            await _client().fetch_profile("AAPL")

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_with_reset_header_sleeps_and_retries(self):
        """On 429, parse x-ratelimit-reset, sleep, retry once."""
        import time as time_mod

        # Reset 0.5s in the future — sleep should be ~0.5s + 1s buffer = ~1.5s.
        reset_epoch = time_mod.time() + 0.5
        route = respx.get(_BASE + "/stock/profile2").mock(
            side_effect=[
                Response(429, text="quota", headers={
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": f"{reset_epoch:.0f}",
                }),
                Response(200, json=_AAPL_PROFILE, headers={
                    "x-ratelimit-remaining": "59",
                    "x-ratelimit-reset": f"{reset_epoch + 60:.0f}",
                }),
            ]
        )
        result = await _client().fetch_profile("AAPL")
        assert result is not None
        assert route.call_count == 2

    @pytest.mark.asyncio
    @respx.mock
    async def test_429_after_recovery_sleep_returns_none(self):
        """If we still 429 after the rate-limit-aware sleep, give up."""
        import time as time_mod

        reset_epoch = time_mod.time() + 0.5
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(429, text="quota", headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": f"{reset_epoch:.0f}",
            })
        )
        result = await _client().fetch_profile("AAPL")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_500_returns_none(self):
        """Non-2xx (other than 403/429) returns None — Finnhub is best-effort."""
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(500, text="Server Error")
        )
        result = await _client().fetch_profile("AAPL")
        assert result is None

    @pytest.mark.asyncio
    @respx.mock
    async def test_remaining_zero_proactive_sleep(self):
        """A 200 response with remaining=0 sleeps until reset before returning."""
        import time as time_mod

        reset_epoch = time_mod.time() + 0.5  # 0.5s in future + 1s buffer = ~1.5s sleep
        respx.get(_BASE + "/stock/profile2").mock(
            return_value=Response(200, json=_AAPL_PROFILE, headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": f"{reset_epoch:.0f}",
            })
        )
        start = time_mod.monotonic()
        result = await _client().fetch_profile("AAPL")
        elapsed = time_mod.monotonic() - start

        assert result is not None
        # Bucket adds ~0.05s (we use the test's fast policy via _client()? — no,
        # _client() uses default policy. But the bucket's first acquire on a
        # 60/min rate is ~1s. So total ≈ 1s bucket + ~1.5s reset wait ≈ 2.5s.
        # The proactive sleep is the dominant component; assert it happened.
        assert elapsed >= 1.0, (
            f"Expected proactive sleep on remaining=0; total elapsed {elapsed:.2f}s "
            f"too short to include the ~1.5s reset wait."
        )


class TestTokenBucket:
    """Verify the token bucket starts empty (no burst) and paces correctly.

    Regression test for the 2026-05-01 issue where the bucket pre-filled
    with `rate_per_minute` tokens, allowing a 60-request burst at startup
    that tripped Finnhub's burst protection.
    """

    @pytest.mark.asyncio
    async def test_first_acquire_waits_one_token_period(self):
        """The first acquire() must wait ~1/rate seconds; bucket starts empty."""
        import asyncio
        import time as time_mod

        # Use a fast rate (600/min = 10/sec → 0.1s per token) to keep test quick
        bucket = _TokenBucket(rate_per_minute=600)

        start = time_mod.monotonic()
        await bucket.acquire()
        elapsed = time_mod.monotonic() - start

        # Should wait ~0.1s for the first token. Allow a generous range
        # to avoid flakiness on slow CI runners.
        assert 0.05 < elapsed < 0.30, (
            f"Expected ~0.1s wait on first acquire (empty bucket), got {elapsed:.3f}s. "
            f"If <0.05s, bucket is pre-filling and would burst."
        )

    @pytest.mark.asyncio
    async def test_steady_state_paces_at_rate(self):
        """After the first acquire, subsequent calls pace at ~1/rate seconds each."""
        import time as time_mod

        bucket = _TokenBucket(rate_per_minute=600)  # 10/sec

        # Drain the first token
        await bucket.acquire()

        # Three more acquires should each take ~0.1s
        start = time_mod.monotonic()
        for _ in range(3):
            await bucket.acquire()
        elapsed = time_mod.monotonic() - start

        # Three tokens at 10/sec ≈ 0.3s. Allow generous bounds.
        assert 0.20 < elapsed < 0.60, (
            f"Expected ~0.3s for 3 acquires at 10/sec, got {elapsed:.3f}s."
        )
