"""Unit tests for ``GET /api/v1/kalshi/search`` (#63).

A real app with a sentinel connection and ``serve_search.search`` replaced:
what is under test is validation, the pass-through of ``q`` and ``limit``, and
the response shape. Ranking is covered by ``test_kalshi_search.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.routes import kalshi_search as route_module
from manta_trading.data.kalshi.constants import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
)
from manta_trading.data.kalshi.serve_search import (
    EventHit,
    MarketHit,
    SearchHits,
    SeriesHit,
)

TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
URL = "/api/v1/kalshi/search"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    seen: list[tuple[str, int]] = []

    def fake(_conn: Any, query: str, *, limit: int) -> SearchHits:
        seen.append((query, limit))
        return SearchHits(
            series=[SeriesHit("KXBTC", "Bitcoin price", "Crypto")],
            events=[EventHit("KXBTC-1", "KXBTC", "Bitcoin on Dec 1?", None, TS)],
            markets=[
                MarketHit("KXBTC-1-T1", "KXBTC-1", "KXBTC", "Above 1?", "y", "n", TS)
            ],
        )

    monkeypatch.setattr(route_module.serve_search, "search", fake)
    return seen


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    app.dependency_overrides[get_db] = lambda: MagicMock(name="sentinel_conn")
    return TestClient(app)


def test_returns_three_groups_with_parent_tickers(client, calls):
    body = client.get(URL, params={"q": "bitcoin"}).json()
    assert body["query"] == "bitcoin"
    assert body["series"][0]["ticker"] == "KXBTC"
    assert body["events"][0]["series_ticker"] == "KXBTC"
    assert body["markets"][0]["event_ticker"] == "KXBTC-1"
    assert body["markets"][0]["series_ticker"] == "KXBTC"


def test_limit_defaults_and_passes_through(client, calls):
    client.get(URL, params={"q": "a"})
    client.get(URL, params={"q": "a", "limit": 3})
    assert calls == [("a", SEARCH_DEFAULT_LIMIT), ("a", 3)]


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"q": ""},
        {"q": "   "},
        {"q": "x", "limit": 0},
        {"q": "x", "limit": SEARCH_MAX_LIMIT + 1},
    ],
)
def test_bad_input_is_422_and_never_reaches_the_reader(client, calls, params):
    assert client.get(URL, params=params).status_code == 422
    assert calls == []
