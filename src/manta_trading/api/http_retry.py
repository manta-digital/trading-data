"""Centralized HTTP retry/timeout policy for all new API clients (slice 141, D11).

Both EodhdSymbolListClient and FinnhubClient use RetryPolicy so retry behavior
cannot drift between providers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx

from manta_trading.logging import get_logger

_logger = get_logger(__name__)


@dataclass
class RetryPolicy:
    """HTTP retry and timeout parameters (D11).

    Attributes:
        connect_timeout: Seconds to wait for TCP connect / DNS.
        read_timeout:    Seconds to wait for the server to start sending data.
        retries:         Maximum number of retry attempts after first failure.
        backoff_seconds: Wait times between retries (length must equal retries).
        retryable_status_codes: HTTP status codes that warrant a retry.
    """

    connect_timeout: float = 10.0
    read_timeout: float = 30.0
    retries: int = 3
    backoff_seconds: list[float] = field(default_factory=lambda: [1.0, 2.0, 4.0])
    retryable_status_codes: frozenset[int] = field(
        default_factory=lambda: frozenset({429, 502, 503, 504})
    )

    def timeout(self) -> httpx.Timeout:
        return httpx.Timeout(connect=self.connect_timeout, read=self.read_timeout, write=10.0, pool=10.0)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    policy: RetryPolicy,
    **kwargs: Any,
) -> httpx.Response:
    """Execute an HTTP request with retry logic from RetryPolicy.

    Retryable conditions (per D11):
    - httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError
    - Response status in policy.retryable_status_codes

    Non-retryable: 400, 401, 403, 404, other 5xx, malformed JSON.
    These are returned/raised immediately without retry.

    Args:
        client: The httpx.AsyncClient to use.
        method: HTTP method string (e.g. 'GET').
        url: Full URL.
        policy: RetryPolicy governing this request.
        **kwargs: Additional kwargs forwarded to client.request().

    Returns:
        httpx.Response on success.

    Raises:
        httpx.HTTPStatusError: On non-retryable HTTP errors (after exhausting retries
            for retryable codes).
        httpx.TransportError: On network errors after exhausting retries.
    """
    last_exc: Exception | None = None

    for attempt in range(policy.retries + 1):
        try:
            resp = await client.request(method, url, timeout=policy.timeout(), **kwargs)

            if resp.status_code in policy.retryable_status_codes:
                if attempt < policy.retries:
                    wait = policy.backoff_seconds[min(attempt, len(policy.backoff_seconds) - 1)]
                    _logger.warning(
                        "HTTP %d from %s; retrying in %.0fs (attempt %d/%d)",
                        resp.status_code, url, wait, attempt + 1, policy.retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                resp.raise_for_status()

            return resp

        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            if attempt < policy.retries:
                wait = policy.backoff_seconds[min(attempt, len(policy.backoff_seconds) - 1)]
                _logger.warning(
                    "%s from %s; retrying in %.0fs (attempt %d/%d)",
                    type(exc).__name__, url, wait, attempt + 1, policy.retries,
                )
                await asyncio.sleep(wait)
            else:
                raise

    # Unreachable, but satisfies type checker
    if last_exc:
        raise last_exc
    raise RuntimeError("request_with_retry: exhausted retries without raising")
