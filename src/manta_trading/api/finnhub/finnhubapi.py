"""Finnhub API client for stock profile data (slice 141, D8/D11).

Rate limiting is **server-authoritative** via Finnhub's response headers:
- `x-ratelimit-limit`     — calls per minute on this token (60 on free tier).
- `x-ratelimit-remaining` — calls left in the current window.
- `x-ratelimit-reset`     — epoch seconds when the window rolls over.

Two layers of pacing keep the client below the limit:

1. Client-side token bucket at 60/min: cheap pre-emptive throttle so we
   don't fire bursts.
2. Header-aware corrective sleep: when a 200 response shows
   `remaining=0`, we sleep until `reset` BEFORE returning so the next
   caller never sends inside the exhausted window. When a 429 arrives
   (e.g., recovering from a prior run that exhausted the window before
   this process started), we read `reset`, sleep until it, retry once.

Finnhub failures (e.g., 5xx, network errors after retries) return None
rather than raising — Finnhub is best-effort enrichment per D6/D9.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from manta_trading.api.http_retry import RetryPolicy, request_with_retry
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"
_RATE_LIMIT_PER_MINUTE = 60

# Buffer added to the server-reported reset timestamp when we sleep.
# Avoids edge cases where we wake up exactly at reset-time and the
# server's clock is a hair behind ours.
_RESET_BUFFER_SECONDS = 1.0

# Fallback sleep when a 429 lacks the x-ratelimit-reset header (defensive).
_DEGENERATE_429_SLEEP_SECONDS = 65.0


class FinnhubAccessError(Exception):
    """Raised when Finnhub returns 403 Forbidden (credentials / plan issue)."""


class _TokenBucket:
    """Drip-based token bucket at a fixed rate (tokens/sec).

    Fills the bucket up front to allow initial burst, then refills at
    rate tokens/second. acquire() blocks until a token is available.
    """

    def __init__(self, rate_per_minute: int) -> None:
        self._rate = rate_per_minute / 60.0  # tokens per second
        # Start empty rather than full so the first acquire() waits one
        # token-period (~1s at 60/min) instead of firing immediately.
        # Pre-filling to capacity caused a 60-request burst at startup
        # that tripped Finnhub's burst protection and produced sustained
        # 429s (observed 2026-05-01 against Finnhub free tier).
        self._tokens = 0.0
        self._capacity = float(rate_per_minute)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        # Hold the lock for the full operation including any sleep, so a
        # serial caller observes strict 1/rate pacing (no "free" tokens
        # leaking from sleeping outside the lock).
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                # After sleeping we have exactly 1 fresh token available.
                self._tokens = 1.0
                self._last_refill = time.monotonic()

            self._tokens -= 1.0


def _seconds_until(reset_epoch: float) -> float:
    """Return seconds from now until reset_epoch, plus buffer; never negative."""
    delta = reset_epoch - time.time() + _RESET_BUFFER_SECONDS
    return max(0.0, delta)


def _parse_reset_header(resp: httpx.Response) -> float | None:
    """Parse x-ratelimit-reset header (epoch seconds) or return None."""
    raw = resp.headers.get("x-ratelimit-reset")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


class FinnhubClient:
    """Thin async client for Finnhub /stock/profile2 endpoint.

    Combines a 60/min token bucket (pre-emptive pacing) with header-aware
    corrective sleep on rate-limit responses. The retry policy passed to
    the underlying transport explicitly EXCLUDES 429 — we handle it here
    using the server's own ``x-ratelimit-reset`` header rather than
    burning attempts against an exhausted window.

    Args:
        api_key: Finnhub API token.
        http_policy: RetryPolicy for connect/read errors (default: same as
            base policy, but with 429 stripped from retryable_status_codes).
    """

    def __init__(self, api_key: str, http_policy: RetryPolicy | None = None) -> None:
        self._api_key = api_key
        # Strip 429 from the transport-level retry set: we handle it
        # ourselves using x-ratelimit-reset, NOT by retrying against an
        # exhausted window (which would burn quota for nothing).
        base = http_policy or RetryPolicy()
        self._policy = RetryPolicy(
            connect_timeout=base.connect_timeout,
            read_timeout=base.read_timeout,
            retries=base.retries,
            backoff_seconds=base.backoff_seconds,
            retryable_status_codes=frozenset(
                c for c in base.retryable_status_codes if c != 429
            ),
        )
        self._bucket = _TokenBucket(_RATE_LIMIT_PER_MINUTE)

    async def fetch_profile(self, symbol: str) -> dict[str, Any] | None:
        """Fetch stock profile for a symbol from Finnhub.

        Returns the profile dict if the ``ipo`` field is present and non-empty,
        otherwise returns None.

        On 429: read ``x-ratelimit-reset``, sleep until that time, retry once.
        On a 200 with ``x-ratelimit-remaining=0``: sleep until reset BEFORE
        returning, so the next caller doesn't fire inside the exhausted window.

        Args:
            symbol: Ticker symbol (e.g. 'AAPL').

        Returns:
            Profile dict, or None if Finnhub has no IPO data, the request
            failed transiently, or rate-limit recovery still didn't yield a
            successful response.

        Raises:
            FinnhubAccessError: On 403 Forbidden (credentials / plan issue).
        """
        url = f"{_BASE_URL}/stock/profile2"
        params = {"symbol": symbol, "token": self._api_key}

        # Two attempts: first call, then one optional retry after a 429-aware sleep.
        for attempt in (1, 2):
            await self._bucket.acquire()

            try:
                async with httpx.AsyncClient() as client:
                    resp = await request_with_retry(
                        client, "GET", url, self._policy, params=params
                    )
            # Catches httpx transport errors (connect/read timeout, peer reset,
            # DNS) and any HTTPStatusError raised by the policy. JSON decode
            # errors don't apply here yet — we haven't called .json().
            except httpx.HTTPError as exc:
                _logger.warning(
                    "Finnhub fetch_profile %s: transport error — %s", symbol, exc
                )
                return None

            if resp.status_code == 403:
                raise FinnhubAccessError(
                    f"Finnhub 403 for {symbol}; check MT_FINNHUB_API_KEY"
                )

            if resp.status_code == 429:
                if attempt == 2:
                    _logger.warning(
                        "Finnhub fetch_profile %s: 429 after rate-limit recovery sleep; giving up",
                        symbol,
                    )
                    return None
                reset = _parse_reset_header(resp)
                wait = (
                    _seconds_until(reset)
                    if reset is not None
                    else _DEGENERATE_429_SLEEP_SECONDS
                )
                _logger.info(
                    "Finnhub 429 (remaining=0); sleeping %.1fs until window reset",
                    wait,
                )
                await asyncio.sleep(wait)
                continue

            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                _logger.warning(
                    "Finnhub fetch_profile %s: HTTP %d — %s",
                    symbol,
                    resp.status_code,
                    exc,
                )
                return None

            try:
                data: dict[str, Any] = resp.json()
            except ValueError as exc:
                _logger.warning(
                    "Finnhub fetch_profile %s: malformed JSON — %s", symbol, exc
                )
                return None

            # Proactive header-aware backoff: if this successful response says
            # the window is now exhausted, sleep until reset before returning.
            # The next caller's bucket.acquire() takes ~1s; that's fine — the
            # sleep happens here so the next request will hit a fresh window.
            remaining_raw = resp.headers.get("x-ratelimit-remaining")
            if remaining_raw == "0":
                reset = _parse_reset_header(resp)
                if reset is not None:
                    wait = _seconds_until(reset)
                    if wait > 0:
                        _logger.info(
                            "Finnhub remaining=0; sleeping %.1fs until window reset",
                            wait,
                        )
                        await asyncio.sleep(wait)

            ipo = data.get("ipo", "")
            if not ipo:
                return None
            return data

        return None  # unreachable, satisfies type checker
