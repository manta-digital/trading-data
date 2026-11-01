"""``GET /api/v1/symbols?search=`` by ticker or name on a real database (#60).

The rows go in through ``upsert_eodhd_universe``, the path the universe
rebuild uses, with names as EODHD's symbol list spells them (2026-11-01).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tick_support.api import client_for

from manta_trading.data.base.instrument_registry import InstrumentRegistry

_ROWS = [  # (symbol, EODHD name)
    ("AAPL", "Apple Inc."),
    ("APLE", "Apple Hospitality REIT Inc"),
    ("AP", "Ampco-Pittsburgh Corporation"),
    ("APP", "Applovin Corp"),
    ("SPY", "State Street® SPDR® S&P 500® ETF Trust"),
    ("B_F", None),
]


def _row(symbol: str, name: str | None) -> dict[str, object]:
    return {
        "canonical_id": f"{symbol}.US",
        "symbol": symbol,
        "asset_class": "equity",
        "venue": "US",
        "currency": "USD",
        "trading_calendar_id": "NYSE",
        "eodhd_type": "Common Stock",
        "eodhd_exchange": "NASDAQ",
        "delisted_at_eodhd": False,
        "name": name,
    }


@pytest.fixture
def client(migrated_db: str) -> Iterator[TestClient]:
    registry = InstrumentRegistry(conninfo=migrated_db)
    try:
        registry.upsert_eodhd_universe([_row(*pair) for pair in _ROWS])
    finally:
        registry.close()
    yield from client_for(migrated_db, None)


def _search(client: TestClient, term: str) -> list[str]:
    response = client.get("/api/v1/symbols", params={"search": term})
    assert response.status_code == 200
    return [row["symbol"] for row in response.json()["symbols"]]


def test_company_name_finds_the_ticker(client: TestClient) -> None:
    """Searching apple matches by name; no ticker starts with it."""
    assert _search(client, "apple") == ["AAPL", "APLE"]


def test_exact_ticker_then_prefix_then_name(client: TestClient) -> None:
    """Searching ap: exact AP first, then ticker prefixes, then name-only AAPL."""
    assert _search(client, "ap") == ["AP", "APLE", "APP", "AAPL"]


def test_name_is_returned(client: TestClient) -> None:
    body = client.get("/api/v1/symbols", params={"search": "SPY"}).json()
    assert body["symbols"][0]["name"] == "State Street® SPDR® S&P 500® ETF Trust"
    detail = client.get("/api/v1/symbols/AAPL").json()
    assert detail["name"] == "Apple Inc."


def test_underscore_matches_literally(client: TestClient) -> None:
    """``_`` is not a one-character wildcard: "b_" finds B_F only."""
    assert _search(client, "b_") == ["B_F"]
