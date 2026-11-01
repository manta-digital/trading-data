"""``refresh_names`` against a real database with EODHD stubbed (#60).

The stub returns rows in the shape EODHD's /exchange-symbol-list sends
(checked 2026-11-01); nothing else about the pipeline is faked.
"""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg
import pytest

from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.universe import names


def _eodhd(code: str, name: str, exchange: str = "NASDAQ") -> dict[str, Any]:
    return {
        "Code": code,
        "Name": name,
        "Country": "USA",
        "Exchange": exchange,
        "Currency": "USD",
        "Type": "Common Stock",
        "Isin": None,
    }


def _seed(canonical_id: str, symbol: str, venue: str) -> dict[str, Any]:
    """An existing registry row with no name yet."""
    return {
        "canonical_id": canonical_id,
        "symbol": symbol,
        "asset_class": "equity",
        "venue": venue,
        "currency": "USD",
        "trading_calendar_id": "NYSE",
        "eodhd_type": "Common Stock",
        "eodhd_exchange": "US",
        "delisted_at_eodhd": False,
        "name": None,
    }


class _StubClient:
    def __init__(self, **_: Any) -> None:
        pass

    async def preflight(self) -> None:
        return None

    async def fetch_active_us(self) -> list[dict[str, Any]]:
        return [_eodhd("AAPL", "Apple Inc.")]

    async def fetch_delisted_us(self) -> list[dict[str, Any]]:
        return [_eodhd("OLD", "Old Co")]

    async def fetch_indx(self) -> list[dict[str, Any]]:
        return []


def test_names_land_on_existing_rows_only(
    migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AAPL (AV-seeded canonical id) is named; OLD is not inserted; the
    unlisted row keeps NULL."""
    registry = InstrumentRegistry(conninfo=migrated_db)
    try:
        registry.upsert_eodhd_universe(
            [_seed("AAPL.NASDAQ", "AAPL", "NASDAQ"), _seed("ZZZ.US", "ZZZ", "US")]
        )
    finally:
        registry.close()
    monkeypatch.setattr(names, "EodhdSymbolListClient", _StubClient)

    summary = asyncio.run(names.refresh_names(migrated_db, "unused-key"))

    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT canonical_id, name FROM instruments ORDER BY 1"
        ).fetchall()
    assert rows == [("AAPL.NASDAQ", "Apple Inc."), ("ZZZ.US", None)]
    assert summary == {
        "eodhd_rows": 2,
        "names_changed": 1,
        "instruments": 2,
        "named": 1,
        "unnamed": 1,
    }
