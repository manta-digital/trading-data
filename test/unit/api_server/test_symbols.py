"""Unit tests for symbol endpoints and supporting models."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    AvailableRange,
    SymbolDetail,
    SymbolSummary,
    SymbolsResponse,
)


# ---------------------------------------------------------------------------
# Model tests (T3)
# ---------------------------------------------------------------------------


def test_symbol_summary_nullable_fields() -> None:
    summary = SymbolSummary(
        symbol="XYZ",
        exchange=None,
        type=None,
        asset_class=None,
        active=True,
    )
    data = summary.model_dump()
    assert data["exchange"] is None
    assert data["type"] is None
    assert data["asset_class"] is None


def test_symbols_response_count() -> None:
    items = [
        SymbolSummary(symbol="AAA", exchange="US", type="cs", asset_class="equity", active=True),
        SymbolSummary(symbol="BBB", exchange=None, type=None, asset_class=None, active=False),
    ]
    resp = SymbolsResponse(symbols=items, count=2)
    assert resp.count == 2
    assert len(resp.symbols) == 2


def test_symbol_detail_available_empty() -> None:
    detail = SymbolDetail(
        symbol="AAPL",
        exchange="US",
        type="cs",
        asset_class="equity",
        active=True,
        available={},
    )
    assert detail.available == {}


def test_available_range_fields() -> None:
    ar = AvailableRange(start=date(2024, 1, 1), end=date(2024, 12, 31))
    assert isinstance(ar.start, date)
    assert isinstance(ar.end, date)


# ---------------------------------------------------------------------------
# Fixtures (T6 / T7)
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app() -> FastAPI:
    """Fresh app with DB pool mocked; lifespan is not entered."""
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    return app


def _mock_conn_for_list(rows: list[Any]) -> MagicMock:
    """Return a mock psycopg connection whose execute().fetchall() yields ``rows``."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    return conn


def _mock_conn_for_detail(fetchone_returns: list[Any]) -> MagicMock:
    """Return a mock psycopg connection whose execute().fetchone() consumes ``fetchone_returns``."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_returns
    conn.execute.return_value = cursor
    return conn


# ---------------------------------------------------------------------------
# List endpoint tests (T6)
# ---------------------------------------------------------------------------


def test_list_symbols_no_filter(test_app: FastAPI) -> None:
    rows = [
        ("AAPL", "US", "cs", "equity", True),
        ("MSFT", "US", "cs", "equity", True),
    ]
    mock_conn = _mock_conn_for_list(rows)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    assert len(body["symbols"]) == 2


def test_list_symbols_search_filter(test_app: FastAPI) -> None:
    rows = [("SPY", "US", "etf", "equity", True)]
    mock_conn = _mock_conn_for_list(rows)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols?search=SP")
    assert response.status_code == 200
    # Verify the execute call received the search prefix pattern
    call_args = mock_conn.execute.call_args
    params = call_args[0][1]  # positional: (sql, params)
    assert params[0] == "SP%"


def test_list_symbols_empty(test_app: FastAPI) -> None:
    mock_conn = _mock_conn_for_list([])
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 0
    assert body["symbols"] == []


# ---------------------------------------------------------------------------
# Detail endpoint tests (T7)
# ---------------------------------------------------------------------------


def test_symbol_detail_with_both_ranges(test_app: FastAPI) -> None:
    instrument_row = ("AAPL", "US", "cs", "equity", True)
    minute_row = (date(2024, 1, 1), date(2026, 1, 1))
    daily_row = (date(2000, 1, 1), date(2026, 1, 1))
    mock_conn = _mock_conn_for_detail([instrument_row, minute_row, daily_row])
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/AAPL")
    assert response.status_code == 200
    body = response.json()
    assert "1m" in body["available"]
    assert "1d" in body["available"]


def test_symbol_detail_daily_only(test_app: FastAPI) -> None:
    instrument_row = ("AAPL", "US", "cs", "equity", True)
    minute_row = (None, None)
    daily_row = (date(2000, 1, 1), date(2026, 1, 1))
    mock_conn = _mock_conn_for_detail([instrument_row, minute_row, daily_row])
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/AAPL")
    assert response.status_code == 200
    body = response.json()
    assert "1d" in body["available"]
    assert "1m" not in body["available"]


def test_symbol_detail_not_found(test_app: FastAPI) -> None:
    mock_conn = _mock_conn_for_detail([None])
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/FAKESYMBOL")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "detail" not in body
