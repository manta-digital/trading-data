"""Request-core tests for ``KalshiClient`` (slice 261, Task 4.2).

Drives ``_get_json`` / ``_get_model`` through ``httpx.MockTransport``.
Failure-path tests assert exception *type and cause*, never message text.
"""

# The request core is private by design; these tests exist to drive it.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from manta_trading.data.kalshi import client as client_module
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import KALSHI_MAX_RETRIES
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.util.ratelimiter import RateLimiter

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Replace the backoff sleep with a recorder so retries are instant."""
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(client_module, "_sleep", fake_sleep)
    return sleeps


def make_client(handler: Handler, **kwargs: Any) -> KalshiClient:
    return KalshiClient(transport=httpx.MockTransport(handler), **kwargs)


class Counter:
    """Counts requests and delegates to a per-attempt behaviour."""

    def __init__(self, behaviour: Callable[[int, httpx.Request], httpx.Response]):
        self.calls = 0
        self._behaviour = behaviour

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return self._behaviour(self.calls, request)


def _raise(exc_type: type[httpx.TransportError]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc_type("boom", request=request)

    return handler


class TestTransportFailures:
    @pytest.mark.parametrize(
        "exc_type",
        [
            httpx.ConnectError,
            httpx.ReadTimeout,
            httpx.ReadError,
            httpx.RemoteProtocolError,
            httpx.ConnectTimeout,
            httpx.PoolTimeout,
            httpx.WriteError,
        ],
    )
    async def test_transport_error_is_transient_with_cause(
        self, exc_type: type[httpx.TransportError], no_backoff_sleep: list[float]
    ):
        counter = Counter(lambda _n, req: _raise(exc_type)(req))
        client = make_client(counter)
        with pytest.raises(ProviderTransientError) as info:
            await client._get_json("/historical/cutoff", {})
        assert isinstance(info.value.__cause__, exc_type)
        assert counter.calls == KALSHI_MAX_RETRIES + 1
        assert len(no_backoff_sleep) == KALSHI_MAX_RETRIES
        await client.aclose()

    async def test_transient_that_recovers_returns_normally(self):
        def behaviour(n: int, request: httpx.Request) -> httpx.Response:
            if n == 1:
                raise httpx.ReadError("dropped", request=request)
            return httpx.Response(200, json={"ok": n})

        counter = Counter(behaviour)
        client = make_client(counter)
        assert await client._get_json("/x", {}) == {"ok": 2}
        assert counter.calls == 2

    async def test_backoff_is_exponential(self, no_backoff_sleep: list[float]):
        client = make_client(_raise(httpx.ConnectError))
        with pytest.raises(ProviderTransientError):
            await client._get_json("/x", {})
        assert no_backoff_sleep == [1.0, 2.0, 4.0][:KALSHI_MAX_RETRIES]

    async def test_non_transport_http_error_is_permanent_with_cause(self):
        """``httpx.HTTPError`` members that are not transport failures
        (e.g. ``DecodingError``) are permanent and never retried."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.DecodingError("bad gzip", request=request)

        counter = Counter(lambda _n, req: handler(req))
        client = make_client(counter)
        with pytest.raises(ProviderPermanentError) as info:
            await client._get_json("/x", {})
        assert isinstance(info.value.__cause__, httpx.DecodingError)
        assert counter.calls == 1


class TestStatusClassification:
    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_transient_status_retried_then_raises(self, status: int):
        counter = Counter(lambda _n, _r: httpx.Response(status, text="slow down"))
        client = make_client(counter)
        with pytest.raises(ProviderTransientError):
            await client._get_json("/x", {})
        assert counter.calls == KALSHI_MAX_RETRIES + 1

    async def test_transient_status_then_success(self):
        def behaviour(n: int, _r: httpx.Request) -> httpx.Response:
            if n < 3:
                return httpx.Response(503)
            return httpx.Response(200, json={"n": n})

        counter = Counter(behaviour)
        client = make_client(counter)
        assert await client._get_json("/x", {}) == {"n": 3}
        assert counter.calls == 3

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_other_4xx_permanent_no_retry(self, status: int):
        counter = Counter(
            lambda _n, _r: httpx.Response(status, json={"error": {"code": "x"}})
        )
        client = make_client(counter)
        with pytest.raises(ProviderPermanentError):
            await client._get_json("/x", {})
        assert counter.calls == 1

    async def test_malformed_json_permanent_with_cause(self):
        client = make_client(lambda _r: httpx.Response(200, text="<html>nope"))
        with pytest.raises(ProviderPermanentError) as info:
            await client._get_json("/x", {})
        assert isinstance(info.value.__cause__, ValueError)


class _Model(BaseModel):
    ticker: str


class TestModelValidation:
    async def test_valid_payload_returns_model(self):
        client = make_client(lambda _r: httpx.Response(200, json={"ticker": "A"}))
        result = await client._get_model("/x", _Model)
        assert result.ticker == "A"

    async def test_validation_failure_is_permanent_with_cause(self):
        client = make_client(lambda _r: httpx.Response(200, json={"nope": 1}))
        with pytest.raises(ProviderPermanentError) as info:
            await client._get_model("/x", _Model)
        assert info.value.__cause__ is not None
        assert type(info.value.__cause__).__name__ == "ValidationError"


class TestRequestShape:
    async def test_params_cleaned_and_base_url_applied(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={})

        client = make_client(handler, base_url="https://example.test/trade-api/v2")
        await client._get_json(
            "/markets", {"limit": 5, "status": None, "with_nested_markets": True}
        )
        assert seen[0].url.host == "example.test"
        assert seen[0].url.path == "/trade-api/v2/markets"
        assert dict(seen[0].url.params) == {"limit": "5", "with_nested_markets": "true"}

    async def test_aclose_is_idempotent(self):
        client = make_client(lambda _r: httpx.Response(200, json={}))
        await client._get_json("/x", {})
        await client.aclose()
        await client.aclose()


class TestRateLimiter:
    async def test_budget_enforced_across_calls(self):
        limiter = RateLimiter(max_calls=2, period=1.0)
        client = make_client(
            lambda _r: httpx.Response(200, json={}), rate_limiter=limiter
        )
        start = time.monotonic()
        for _ in range(3):
            await client._get_json("/x", {})
        assert time.monotonic() - start >= 1.0

    async def test_retries_pass_through_limiter(self):
        limiter = RateLimiter(max_calls=2, period=1.0)
        counter = Counter(lambda _n, _r: httpx.Response(503))
        client = make_client(counter, rate_limiter=limiter, max_retries=2)
        start = time.monotonic()
        with pytest.raises(ProviderTransientError):
            await client._get_json("/x", {})
        # 3 attempts through a 2-per-second limiter: the third waits a period.
        assert counter.calls == 3
        assert time.monotonic() - start >= 1.0
