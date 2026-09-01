"""Shared HTTP retry/classify helpers for slice 128 EODHD endpoints.

Three paths use this module: ``EODHDCorporateActionsProvider`` for
``/splits`` and ``/div``, and the Stage B verifier for ``/eod``. The
classification rules and retry policy are identical (per slice 128
§Error handling), so they live in one place.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

from manta_trading.providers.errors import (
    ProviderPermanentError,
    ProviderTransientError,
)

_TRANSIENT_STATUS = {429, 500, 502, 503, 504}
_DEFAULT_MAX_RETRIES = 3
_BACKOFF_BASE_S = 1.0
_BACKOFF_CAP_S = 60.0

T = TypeVar("T")


def _redact(url: str, secret: str) -> str:
    if not secret:
        return url
    return url.replace(secret, "***")


def _backoff_seconds(attempt: int) -> float:
    """Exponential backoff capped at 60s. ``attempt`` is 0-indexed."""
    return min(_BACKOFF_BASE_S * (2**attempt), _BACKOFF_CAP_S)


async def _sleep(seconds: float) -> None:
    """Indirect ``asyncio.sleep`` so tests can monkeypatch this module."""
    await asyncio.sleep(seconds)


async def fetch_with_retry(
    *,
    client: httpx.AsyncClient,
    url: str,
    api_key: str,
    logger: logging.Logger,
    timeout: float,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> Any:
    """GET ``url`` returning parsed JSON; classify failures per slice 128.

    Transient failures (timeout, 5xx, 429, peer disconnect) retry up to
    ``max_retries`` times with exponential backoff (1s, 2s, 4s, …, capped
    at 60s). On 429 with a ``Retry-After`` header, that hint is honored
    instead of the backoff schedule.

    Raises:
        ProviderPermanentError: 4xx other than 429, malformed JSON.
        ProviderTransientError: retries exhausted on transient failure.
    """
    safe_url = _redact(url, api_key)
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = await client.get(url, timeout=timeout)
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            logger.warning(
                "EODHD transport error (attempt %d/%d) on %s: %s",
                attempt + 1,
                max_retries + 1,
                safe_url,
                exc,
            )
            if attempt < max_retries:
                await _sleep(_backoff_seconds(attempt))
                continue
            raise ProviderTransientError(
                f"transport failure after {max_retries + 1} attempts: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            # Other httpx errors (connection refused, DNS, etc.) — also
            # transient by nature; same retry policy.
            last_exc = exc
            logger.warning(
                "EODHD HTTP error (attempt %d/%d) on %s: %s",
                attempt + 1,
                max_retries + 1,
                safe_url,
                exc,
            )
            if attempt < max_retries:
                await _sleep(_backoff_seconds(attempt))
                continue
            raise ProviderTransientError(
                f"HTTP error after {max_retries + 1} attempts: {exc}"
            ) from exc

        status = response.status_code
        if status == 200:
            try:
                return response.json()
            except ValueError as exc:
                logger.error(
                    "EODHD returned non-JSON 200 on %s: %s",
                    safe_url,
                    response.text[:200],
                )
                raise ProviderPermanentError(
                    f"non-JSON 200 response: {response.text[:200]}"
                ) from exc

        body_preview = response.text[:300]

        if status in _TRANSIENT_STATUS:
            logger.warning(
                "EODHD HTTP %d (transient, attempt %d/%d) on %s: %s",
                status,
                attempt + 1,
                max_retries + 1,
                safe_url,
                body_preview,
            )
            if attempt < max_retries:
                # Honor Retry-After if 429 includes one; else exponential.
                delay = _backoff_seconds(attempt)
                if status == 429:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            delay = min(float(retry_after), _BACKOFF_CAP_S)
                        except ValueError:
                            # Date-form Retry-After not parsed; fall back
                            # to exponential.
                            pass
                await _sleep(delay)
                continue
            raise ProviderTransientError(
                f"HTTP {status} after {max_retries + 1} attempts: {body_preview}"
            )

        # Permanent: any other 4xx (auth, not-found, malformed request).
        logger.error(
            "EODHD HTTP %d (permanent) on %s: %s",
            status,
            safe_url,
            body_preview,
        )
        raise ProviderPermanentError(f"HTTP {status}: {body_preview}")

    # Loop should always either return or raise; this is unreachable.
    raise ProviderTransientError(
        f"unexpected exit from retry loop; last error: {last_exc}"
    )


async def call_with_retry(
    op: Callable[[], Awaitable[T]],
    *,
    logger: logging.Logger,
    description: str,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> T:
    """Wrap an arbitrary awaitable with the same transient/permanent policy.

    Useful when the body of the call is more than a single GET (e.g. the
    daily daemon's per-symbol cycle composite operation).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await op()
        except ProviderPermanentError:
            raise
        except ProviderTransientError as exc:
            last_exc = exc
            logger.warning(
                "%s transient failure (attempt %d/%d): %s",
                description,
                attempt + 1,
                max_retries + 1,
                exc,
            )
            if attempt < max_retries:
                await _sleep(_backoff_seconds(attempt))
                continue
            raise

    raise ProviderTransientError(
        f"{description} failed after {max_retries + 1} attempts; last error: {last_exc}"
    )
