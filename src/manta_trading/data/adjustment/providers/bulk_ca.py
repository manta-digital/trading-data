"""Bulk corporate-actions helpers for EODHD /eod-bulk-last-day (slice 146).

Covers the two CA types returned by the bulk endpoint:
  * ``?type=splits`` — split records for a whole exchange on one date.
  * ``?type=dividends`` — dividend records for a whole exchange on one date.

Each function accepts a *sync* ``httpx.Client`` (the daemon uses sync I/O)
and a pre-resolved ``QuotaBucket``.  The caller is responsible for calling
``bucket.consume(CallType.BULK_EOD)`` — these helpers do NOT consume the
bucket themselves; they call through ``eodhd_get`` which already calls
``bucket.consume`` internally.

Bulk endpoint reference:
  ``GET /eod-bulk-last-day/{EXCHANGE}?type={splits|dividends}&date={YYYY-MM-DD}&api_token=...&fmt=json``
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from manta_trading.data.adjustment.k_factor import Dividend, Split
from manta_trading.data.acquisition.quota import CallType
from manta_trading.logging import get_logger
from manta_trading.providers.errors import ProviderPermanentError

_logger = get_logger(__name__)

_BASE_URL = "https://eodhd.com/api"
_US_EXCHANGE = "US"

# EODHD bulk split format uses "split" field with "/" delimiter.
_SPLIT_DELIMITER = "/"


def _build_bulk_url(api_key: str, exchange: str, ca_type: str, target_date: date) -> str:
    date_str = target_date.isoformat()
    return (
        f"{_BASE_URL}/eod-bulk-last-day/{exchange}"
        f"?type={ca_type}&date={date_str}&api_token={api_key}&fmt=json"
    )


def _parse_split_ratio(raw: str, ticker: str) -> tuple[Decimal, Decimal]:
    """Parse ``"4.000000/1.000000"`` into ``(ratio_to, ratio_from)``."""
    parts = raw.split(_SPLIT_DELIMITER)
    if len(parts) != 2:
        raise ProviderPermanentError(
            f"unexpected split ratio format for {ticker}: {raw!r}"
        )
    try:
        return Decimal(parts[0].strip()), Decimal(parts[1].strip())
    except InvalidOperation as exc:
        raise ProviderPermanentError(
            f"non-numeric split ratio for {ticker}: {raw!r}"
        ) from exc


def _parse_date(raw: str, ticker: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ProviderPermanentError(
            f"malformed date for {ticker}: {raw!r}"
        ) from exc


def fetch_bulk_splits(
    client: httpx.Client,
    target_date: date,
    *,
    api_key: str,
    exchange: str = _US_EXCHANGE,
) -> list[Split]:
    """Fetch all splits for ``exchange`` on ``target_date`` via EODHD bulk endpoint.

    Args:
        client: Shared sync ``httpx.Client`` (already in scope from the daemon).
        target_date: The ex-date to query (EODHD returns CAs *effective* on this date).
        api_key: EODHD API key.
        exchange: Exchange code (default: ``"US"``).

    Returns:
        List of :class:`Split` records parsed from the bulk response.
        Empty list if EODHD returns no records for that date.

    Raises:
        :class:`ProviderPermanentError`: on malformed payload.
        :class:`~manta_trading.api.eodhd_sync.QuotaBucketUnsetError`: when no
            bucket is in scope (consumed inside ``eodhd_get``).
    """
    from manta_trading.api.eodhd_sync import eodhd_get

    url = _build_bulk_url(api_key, exchange, "splits", target_date)
    resp = eodhd_get(client, url, CallType.BULK_EOD)

    try:
        raw_list: Any = resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderPermanentError(
            f"bulk splits for {exchange}/{target_date}: JSON decode failed"
        ) from exc

    if not isinstance(raw_list, list):
        raise ProviderPermanentError(
            f"bulk splits for {exchange}/{target_date}: expected list, "
            f"got {type(raw_list).__name__}"
        )

    results: list[Split] = []
    for entry in raw_list:
        ticker = entry.get("code", "<unknown>")
        # Strip exchange suffix — store bare symbol in DB (matches per-symbol path).
        symbol = ticker.split(".")[0] if "." in ticker else ticker
        try:
            ex_date = _parse_date(entry["date"], ticker)
            ratio_to, ratio_from = _parse_split_ratio(entry["split"], ticker)
        except (KeyError, ProviderPermanentError) as exc:
            _logger.warning(
                "fetch_bulk_splits: skipping malformed entry %r — %s",
                entry,
                exc,
            )
            continue
        results.append(Split(symbol=symbol, ex_date=ex_date, ratio_to=ratio_to, ratio_from=ratio_from))

    _logger.info(
        "fetch_bulk_splits(%s, %s): %d records", exchange, target_date, len(results)
    )
    return results


def fetch_bulk_dividends(
    client: httpx.Client,
    target_date: date,
    *,
    api_key: str,
    exchange: str = _US_EXCHANGE,
) -> list[Dividend]:
    """Fetch all dividends for ``exchange`` on ``target_date`` via EODHD bulk endpoint.

    Args:
        client: Shared sync ``httpx.Client``.
        target_date: The ex-date to query.
        api_key: EODHD API key.
        exchange: Exchange code (default: ``"US"``).

    Returns:
        List of :class:`Dividend` records. Empty list if no records for that date.

    Raises:
        :class:`ProviderPermanentError`: on malformed payload.
    """
    from manta_trading.api.eodhd_sync import eodhd_get

    url = _build_bulk_url(api_key, exchange, "dividends", target_date)
    resp = eodhd_get(client, url, CallType.BULK_EOD)

    try:
        raw_list: Any = resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderPermanentError(
            f"bulk dividends for {exchange}/{target_date}: JSON decode failed"
        ) from exc

    if not isinstance(raw_list, list):
        raise ProviderPermanentError(
            f"bulk dividends for {exchange}/{target_date}: expected list, "
            f"got {type(raw_list).__name__}"
        )

    results: list[Dividend] = []
    for entry in raw_list:
        ticker = entry.get("code", "<unknown>")
        symbol = ticker.split(".")[0] if "." in ticker else ticker
        try:
            ex_date = _parse_date(entry["date"], ticker)
            raw_amount = entry.get("dividend") or entry.get("value") or entry.get("unadjustedValue")
            if raw_amount is None:
                raise ProviderPermanentError(
                    f"no dividend amount field in entry for {ticker}"
                )
            amount = Decimal(str(raw_amount))
            currency = entry.get("currency") or "USD"
        except (KeyError, ProviderPermanentError, InvalidOperation) as exc:
            _logger.warning(
                "fetch_bulk_dividends: skipping malformed entry %r — %s",
                entry,
                exc,
            )
            continue
        results.append(Dividend(symbol=symbol, ex_date=ex_date, amount=amount, currency=currency))

    _logger.info(
        "fetch_bulk_dividends(%s, %s): %d records", exchange, target_date, len(results)
    )
    return results


__all__ = ["fetch_bulk_splits", "fetch_bulk_dividends"]
