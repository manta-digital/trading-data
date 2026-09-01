"""Populate instruments.delisted_date for delisted symbols via EODHD.

Fetches the last available EOD bar for each symbol where delisted_at_eodhd=true
and delisted_date IS NULL. The date of that bar is written as the delisted_date.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Callable

import httpx
import psycopg

from manta_trading.api.eodhd_sync import eodhd_get
from manta_trading.data.acquisition.quota import CallType
from manta_trading.logging import get_logger

_logger = get_logger(__name__)

_EODHD_BASE = "https://eodhd.com/api"

_SELECT_DELISTED_SQL = """
    SELECT symbol FROM instruments
    WHERE delisted_at_eodhd = true AND delisted_date IS NULL
    ORDER BY symbol ASC
"""

_UPDATE_DELISTED_DATE_SQL = """
    UPDATE instruments SET delisted_date = %s WHERE symbol = %s
"""


@dataclass(frozen=True)
class PopulateDelistedDatesReport:
    """Summary counts from a populate_delisted_dates run."""

    total: int
    updated: int
    skipped_empty: int
    error_count: int


def _normalise_symbol(symbol: str) -> str:
    """Append .US exchange suffix if no exchange is already present."""
    if "." in symbol:
        return symbol
    return f"{symbol}.US"


def populate_delisted_dates(
    conn: psycopg.Connection,  # type: ignore[type-arg]
    http: httpx.Client,
    *,
    api_key: str,
    dry_run: bool = False,
    on_progress: Callable[[int, int, str, date | None], None] | None = None,
) -> PopulateDelistedDatesReport:
    """Fetch the last EOD bar for each un-dated delisted instrument and write delisted_date.

    Args:
        conn: Open psycopg connection. Caller owns lifetime.
        http: Shared httpx.Client. Caller owns lifetime.
        api_key: EODHD API token.
        dry_run: When True, fetch and parse responses but skip DB writes.
        on_progress: Optional callback invoked after each symbol.
            Signature: (processed, total, symbol, last_bar_date | None).

    Returns:
        PopulateDelistedDatesReport with per-category counts.
    """
    with conn.cursor() as cur:
        cur.execute(_SELECT_DELISTED_SQL)
        symbols: list[str] = [row[0] for row in cur.fetchall()]

    total = len(symbols)
    updated = 0
    skipped_empty = 0
    error_count = 0

    for idx, sym in enumerate(symbols, start=1):
        normalised = _normalise_symbol(sym)
        url = (
            f"{_EODHD_BASE}/eod/{normalised}"
            f"?api_token={api_key}&fmt=json&order=d&limit=1"
        )

        last_bar_date: date | None = None

        try:
            resp = eodhd_get(http, url, CallType.EOD)
        except httpx.HTTPStatusError as exc:
            _logger.error("populate_delisted_dates: HTTP %s for %s", exc.response.status_code, sym)
            error_count += 1
            if on_progress is not None:
                on_progress(idx, total, sym, None)
            continue

        try:
            bars = json.loads(resp.text)
            if not bars:
                skipped_empty += 1
                if on_progress is not None:
                    on_progress(idx, total, sym, None)
                continue
            last_bar_date = date.fromisoformat(bars[0]["date"])
        except (KeyError, ValueError) as exc:
            _logger.error("populate_delisted_dates: parse error for %s: %s", sym, exc)
            error_count += 1
            if on_progress is not None:
                on_progress(idx, total, sym, None)
            continue

        if not dry_run:
            with conn.cursor() as cur:
                cur.execute(_UPDATE_DELISTED_DATE_SQL, (last_bar_date, sym))
            conn.commit()
            updated += 1
        if on_progress is not None:
            on_progress(idx, total, sym, last_bar_date)

    return PopulateDelistedDatesReport(
        total=total,
        updated=updated,
        skipped_empty=skipped_empty,
        error_count=error_count,
    )
