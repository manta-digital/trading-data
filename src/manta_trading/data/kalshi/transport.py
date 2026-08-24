"""``KalshiTransport`` — the request core under ``KalshiClient``.

Rate-limits, fetches, retries bounded, classifies failures, validates, and
raises. ``client.py`` composes one of these and adds the endpoint surface;
keeping the two apart holds each module near the project's size guideline
and lets the core be tested on its own.

Error classification is complete over ``httpx.HTTPError`` (design 261,
client contract): every ``httpx.TransportError`` (DNS, refused, TLS, all
four timeout phases, peer disconnect mid-response, protocol errors) and HTTP
429/5xx → ``ProviderTransientError`` after bounded retry; every other
``httpx.HTTPError``, every other non-2xx status, non-JSON bodies, and
Pydantic validation failures → ``ProviderPermanentError``. Raised errors
carry their cause. Nothing else is caught.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from manta_trading.data.kalshi.constants import (
    KALSHI_BACKOFF_BASE_SECONDS,
    KALSHI_BACKOFF_CAP_SECONDS,
    KALSHI_BASE_URL,
    KALSHI_MAX_RETRIES,
    KALSHI_PUBLIC_RATE_LIMIT,
    KALSHI_REQUEST_TIMEOUT,
    KALSHI_TRANSIENT_STATUSES,
    RATE_LIMIT_PERIOD_SECONDS,
)
from manta_trading.logging import get_logger
from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.types import RateLimit
from manta_trading.util.ratelimiter import RateLimiter

_logger = get_logger(__name__)

ModelT = TypeVar("ModelT", bound=BaseModel)

#: Query-parameter value types the client accepts; ``None`` means "omit".
ParamValue = str | int | bool | None


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff for 0-indexed ``attempt``, capped."""
    return min(KALSHI_BACKOFF_BASE_SECONDS * (2**attempt), KALSHI_BACKOFF_CAP_SECONDS)


async def _sleep(seconds: float) -> None:
    """Indirect ``asyncio.sleep`` so tests can monkeypatch this module."""
    await asyncio.sleep(seconds)


def _clean_params(params: Mapping[str, ParamValue]) -> dict[str, str]:
    """Drop ``None`` values and render the rest the way Kalshi expects."""
    cleaned: dict[str, str] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            cleaned[key] = "true" if value else "false"
        else:
            cleaned[key] = str(value)
    return cleaned


class KalshiTransport:
    """Rate-limited, retrying GET-and-validate core (public mode).

    Owns one lazily created, reused ``httpx.AsyncClient`` (close it with
    :meth:`aclose`) and one :class:`RateLimiter` — the shared budget every
    surface (catalog, candlesticks, trades) draws from. Every request,
    including each retry, passes through the limiter.

    Args:
        base_url: API root; defaults to the documented primary host.
        rate_limit: Request budget; defaults to the public-tier constant.
        rate_limiter: An explicit limiter instance to share with other
            callers (overrides ``rate_limit``).
        transport: Optional ``httpx`` transport — tests inject
            ``httpx.MockTransport`` here.
        max_retries: Retries after the first attempt on transient failures.
    """

    def __init__(
        self,
        *,
        base_url: str = KALSHI_BASE_URL,
        rate_limit: RateLimit | None = None,
        rate_limiter: RateLimiter | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = KALSHI_MAX_RETRIES,
    ) -> None:
        self._base_url = base_url
        budget = rate_limit or KALSHI_PUBLIC_RATE_LIMIT
        self._limiter = rate_limiter or RateLimiter(
            max_calls=budget.requests_per_minute, period=RATE_LIMIT_PERIOD_SECONDS
        )
        self._transport = transport
        self._max_retries = max_retries
        self._http: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=KALSHI_REQUEST_TIMEOUT,
                transport=self._transport,
            )
        return self._http

    async def aclose(self) -> None:
        """Close the underlying HTTP client (idempotent)."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Request core
    # ------------------------------------------------------------------

    def request_headers(self, method: str, path: str) -> dict[str, str]:
        """Per-request headers. Public mode sends none (signing: Section 7)."""
        return {}

    async def get_json(self, path: str, params: Mapping[str, ParamValue]) -> Any:
        """GET ``path`` (relative to the base URL) and return the JSON body.

        Bounded retry with exponential backoff on transient failures; each
        attempt acquires the rate limiter first.
        """
        http = self._ensure_http()
        query = _clean_params(params)
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            async with self._limiter:
                try:
                    response = await http.get(
                        path, params=query, headers=self.request_headers("GET", path)
                    )
                except httpx.TransportError as exc:
                    # Connection-level failure (DNS, refused, TLS, any timeout
                    # phase, peer disconnect, protocol error): transient.
                    _logger.warning(
                        "kalshi transport error (attempt %d/%d) on %s: %s",
                        attempt + 1,
                        attempts,
                        path,
                        exc,
                    )
                    if attempt < self._max_retries:
                        await _sleep(_backoff_seconds(attempt))
                        continue
                    raise ProviderTransientError(
                        f"transport failure after {attempts} attempts on {path}: {exc}"
                    ) from exc
                except httpx.HTTPError as exc:
                    # Everything else httpx raises that is not a transport
                    # failure (invalid URL, stream misuse): not retriable.
                    _logger.error("kalshi HTTP error on %s: %s", path, exc)
                    raise ProviderPermanentError(
                        f"HTTP error on {path}: {exc}"
                    ) from exc

            status = response.status_code
            if status in KALSHI_TRANSIENT_STATUSES:
                _logger.warning(
                    "kalshi HTTP %d (transient, attempt %d/%d) on %s: %s",
                    status,
                    attempt + 1,
                    attempts,
                    path,
                    response.text[:300],
                )
                if attempt < self._max_retries:
                    await _sleep(_backoff_seconds(attempt))
                    continue
                raise ProviderTransientError(
                    f"HTTP {status} after {attempts} attempts on {path}: "
                    f"{response.text[:300]}"
                )
            return self._decode(response, path)

        # The loop always returns or raises; make that visible if it ever
        # stops being true rather than returning None silently.
        raise ProviderTransientError(f"retry loop exited without result on {path}")

    @staticmethod
    def _decode(response: httpx.Response, path: str) -> Any:
        """Turn a non-transient response into JSON or a permanent error."""
        if not response.is_success:
            _logger.error(
                "kalshi HTTP %d (permanent) on %s: %s",
                response.status_code,
                path,
                response.text[:300],
            )
            raise ProviderPermanentError(
                f"HTTP {response.status_code} on {path}: {response.text[:300]}"
            )
        try:
            return response.json()
        except ValueError as exc:
            _logger.error("kalshi non-JSON body on %s: %s", path, response.text[:200])
            raise ProviderPermanentError(
                f"non-JSON response on {path}: {response.text[:200]}"
            ) from exc

    async def get_model(
        self,
        path: str,
        model_type: type[ModelT],
        params: Mapping[str, ParamValue] | None = None,
    ) -> ModelT:
        """GET ``path`` and validate the body as ``model_type``."""
        body = await self.get_json(path, params or {})
        try:
            return model_type.model_validate(body)
        except ValidationError as exc:
            _logger.error("kalshi payload failed validation on %s: %s", path, exc)
            raise ProviderPermanentError(
                f"payload on {path} failed {model_type.__name__} validation: {exc}"
            ) from exc
