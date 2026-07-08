"""EODHD implementation of :class:`ICorporateActionsProvider`.

Extracted from :mod:`manta_trading.data.adjustment.ingest` during slice
128: the I/O layer (fetch + parse) lives here behind the protocol; the
persistence layer (UPSERT into the splits/dividends tables) stays in
``ingest.py`` so any future CA provider can share the same persister.

EODHD endpoints used:
  * ``GET /splits/{ticker}`` — full history of stock splits.
  * ``GET /div/{ticker}`` — full history of cash dividends.

Symbol normalisation matches the slice-127 minute provider: bare
``AAPL`` is auto-suffixed to ``AAPL.US``; an explicit suffix
(``BMW.XETRA``) passes through unchanged.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from manta_trading.data.adjustment.providers import Dividend, Split
from manta_trading.data.adjustment.providers._http import fetch_with_retry
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderPermanentError

_logger = get_logger(__name__)

_BASE_URL = "https://eodhd.com/api"
_DEFAULT_US_SUFFIX = "US"
_REQUEST_TIMEOUT_S = 30.0


def _normalise_symbol(symbol: str) -> str:
    """Append ``.US`` when no exchange suffix is present."""
    if "." in symbol:
        return symbol
    return f"{symbol}.{_DEFAULT_US_SUFFIX}"


def _parse_iso_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse_split_ratio(raw: str) -> tuple[Decimal, Decimal]:
    """Parse EODHD's ``"4.000000/1.000000"`` shape into ``(to, from)``.

    The numerator is the post-split share count (``ratio_to``); the
    denominator is the pre-split share count (``ratio_from``).
    """
    parts = raw.split("/")
    if len(parts) != 2:
        raise ValueError(f"unexpected split ratio format: {raw!r}")
    to_str, from_str = parts[0].strip(), parts[1].strip()
    return Decimal(to_str), Decimal(from_str)


class EODHDCorporateActionsProvider:
    """Concrete CA provider hitting EODHD's ``/splits`` and ``/div``.

    Lifecycle: instances may be reused across many symbol fetches; the
    underlying ``httpx.AsyncClient`` is constructed lazily and reused. No
    explicit ``aclose`` is required for short-lived CLI processes; the
    daily-daemon path holds an instance for the lifetime of the daemon.
    """

    def __init__(self, *, api_key: str) -> None:
        if not api_key:
            raise ValueError("EODHDCorporateActionsProvider requires an api_key")
        self._api_key = api_key
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S)
        return self._client

    def _build_url(self, path: str) -> str:
        return f"{_BASE_URL}{path}?api_token={self._api_key}&fmt=json"

    async def _fetch(self, path: str) -> Any:
        client = await self._get_client()
        url = self._build_url(path)
        return await fetch_with_retry(
            client=client,
            url=url,
            api_key=self._api_key,
            logger=_logger,
            timeout=_REQUEST_TIMEOUT_S,
        )

    async def fetch_splits(self, symbol: str) -> list[Split]:
        ticker = _normalise_symbol(symbol)
        db_symbol = symbol.split(".")[0]
        raw = await self._fetch(f"/splits/{ticker}")
        if not isinstance(raw, list):
            raise ProviderPermanentError(
                f"unexpected /splits payload for {ticker}: "
                f"{type(raw).__name__}"
            )
        out: list[Split] = []
        for entry in raw:
            try:
                ex = _parse_iso_date(entry["date"])
                ratio_to, ratio_from = _parse_split_ratio(entry["split"])
            except (KeyError, ValueError) as exc:
                raise ProviderPermanentError(
                    f"malformed split entry for {ticker}: {entry!r}"
                ) from exc
            out.append(
                Split(
                    symbol=db_symbol,
                    ex_date=ex,
                    ratio_to=ratio_to,
                    ratio_from=ratio_from,
                )
            )
        return out

    async def fetch_dividends(self, symbol: str) -> list[Dividend]:
        ticker = _normalise_symbol(symbol)
        db_symbol = symbol.split(".")[0]
        raw = await self._fetch(f"/div/{ticker}")
        if not isinstance(raw, list):
            raise ProviderPermanentError(
                f"unexpected /div payload for {ticker}: "
                f"{type(raw).__name__}"
            )
        out: list[Dividend] = []
        for entry in raw:
            try:
                ex = _parse_iso_date(entry["date"])
                amount = Decimal(str(entry["unadjustedValue"]))
                currency = entry.get("currency") or "USD"
            except (KeyError, ValueError) as exc:
                raise ProviderPermanentError(
                    f"malformed dividend entry for {ticker}: {entry!r}"
                ) from exc
            out.append(
                Dividend(
                    symbol=db_symbol,
                    ex_date=ex,
                    amount=amount,
                    currency=currency,
                )
            )
        return out

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


__all__ = ["EODHDCorporateActionsProvider"]
