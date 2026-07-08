"""Unit tests for ``GET /api/v1/gaps/{symbol}`` endpoint and gap models."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import GapRecord, GapsResponse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS1 = datetime(2024, 1, 15, tzinfo=timezone.utc)
_TS2 = datetime(2024, 1, 16, tzinfo=timezone.utc)


def _gap_row(
    gap_start: datetime = _TS1,
    gap_end: datetime = _TS2,
    granularity: str = "minute",
    fetch_status: str = "UNKNOWN",
    attempt_count: int = 0,
    last_attempt_ts: datetime | None = None,
) -> tuple[Any, ...]:
    return (gap_start, gap_end, granularity, fetch_status, attempt_count, last_attempt_ts)


def _mock_conn(rows: list[Any]) -> MagicMock:
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    return conn


@pytest.fixture
def test_app() -> FastAPI:
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    return app


# ---------------------------------------------------------------------------
# T3 — Model tests
# ---------------------------------------------------------------------------


def test_gap_record_nullable_last_attempt() -> None:
    record = GapRecord(
        gap_start=_TS1,
        gap_end=_TS2,
        granularity="minute",
        fetch_status="UNKNOWN",
        attempt_count=0,
        last_attempt_ts=None,
    )
    assert record.model_dump()["last_attempt_ts"] is None


def test_gaps_response_empty() -> None:
    resp = GapsResponse(symbol="X", count=0, gaps=[])
    assert resp.count == 0
    assert resp.gaps == []


def test_gaps_response_count_matches() -> None:
    rows = [
        GapRecord(
            gap_start=_TS1,
            gap_end=_TS2,
            granularity="minute",
            fetch_status="UNKNOWN",
            attempt_count=0,
            last_attempt_ts=None,
        ),
        GapRecord(
            gap_start=datetime(2024, 2, 1, tzinfo=timezone.utc),
            gap_end=datetime(2024, 2, 2, tzinfo=timezone.utc),
            granularity="daily",
            fetch_status="PENDING",
            attempt_count=1,
            last_attempt_ts=_TS1,
        ),
    ]
    resp = GapsResponse(symbol="SPY", count=2, gaps=rows)
    assert len(resp.gaps) == resp.count


# ---------------------------------------------------------------------------
# T6 — Route handler tests
# ---------------------------------------------------------------------------


def test_gaps_no_filter(test_app: FastAPI) -> None:
    rows = [_gap_row(), _gap_row(granularity="daily")]
    mock_conn = _mock_conn(rows)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/gaps/SPY")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "SPY"
    assert body["count"] == 2
    assert len(body["gaps"]) == 2


def test_gaps_granularity_filter(test_app: FastAPI) -> None:
    rows = [_gap_row(granularity="minute")]
    mock_conn = _mock_conn(rows)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/gaps/SPY?granularity=1m")
    assert response.status_code == 200

    # Verify the SQL and params used included the granularity filter
    call_args = mock_conn.execute.call_args
    sql: str = call_args[0][0]
    params: tuple[Any, ...] = call_args[0][1]
    assert "granularity = %s" in sql
    assert "minute" in params


def test_gaps_window_filter(test_app: FastAPI) -> None:
    rows = [_gap_row()]
    mock_conn = _mock_conn(rows)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/gaps/SPY?start=2024-01-01&end=2024-01-31")
    assert response.status_code == 200

    call_args = mock_conn.execute.call_args
    sql: str = call_args[0][0]
    assert "gap_start <" in sql
    assert "gap_end   >" in sql


def test_gaps_unknown_symbol_returns_200(test_app: FastAPI) -> None:
    mock_conn = _mock_conn([])
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/gaps/FAKE")
    assert response.status_code == 200
    body = response.json()
    assert body == {"symbol": "FAKE", "count": 0, "gaps": []}


def test_gaps_invalid_granularity(test_app: FastAPI) -> None:
    client = TestClient(test_app)
    response = client.get("/api/v1/gaps/SPY?granularity=bad")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# T8 — Global 500 handler test
# ---------------------------------------------------------------------------


def test_500_handler_sanitizes_body(test_app: FastAPI) -> None:
    """A route that raises RuntimeError must return 500 with sanitized body."""
    from fastapi.routing import APIRoute

    @test_app.get("/api/v1/_test_error")
    async def _boom() -> None:
        raise RuntimeError("secret sql detail: SELECT * FROM passwords")

    client = TestClient(test_app, raise_server_exceptions=False)
    response = client.get("/api/v1/_test_error")
    assert response.status_code == 500
    body = response.json()
    assert body == {"error": "internal server error"}
    assert "secret" not in response.text
    assert "sql" not in response.text.lower()
