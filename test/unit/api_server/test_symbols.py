"""Unit tests for symbol endpoints and supporting models."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    AvailableRange,
    SymbolDetail,
    SymbolsResponse,
    SymbolSummary,
)
from manta_trading.api_server.queries import UniverseEdgeCache
from manta_trading.api_server.routes import symbols as symbols_module
from manta_trading.constants import CycleGranularity

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
    # Unused while every test below overrides get_db, but deliberately kept
    # (review F004): get_db *does* read app.state.db_pool, so without this a
    # future test that skips the override fails with a bare AttributeError from
    # starlette's State rather than on its own assertion.
    app.state.db_pool = MagicMock(name="sentinel_pool")
    # Lifespan is what normally puts this on app.state (187 D3). A real cache
    # rather than a mock: the route's merge depends on the edges it returns, so
    # a MagicMock would silently produce MagicMock bounds.
    app.state.universe_edges = UniverseEdgeCache()
    return app


_MINUTE = CycleGranularity.MINUTE
_DAILY = CycleGranularity.DAILY
_EDGE = date(2026, 6, 12)
"""Universe coverage edge the mocked head probe is bounded by."""

_INSTRUMENT = ("AAPL", "US", "cs", "equity", True)


def _mock_conn_for_list(rows: list[Any]) -> MagicMock:
    """Return a mock psycopg connection whose execute().fetchall() yields ``rows``."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchall.return_value = rows
    conn.execute.return_value = cursor
    return conn


def _mock_conn_for_detail(
    instrument_row: Any,
    *,
    minute: tuple[date | None, date | None] = (None, None),
    daily: tuple[date | None, date | None] = (None, None),
    minute_head: tuple[date | None, date | None] | None = None,
    daily_head: tuple[date | None, date | None] | None = None,
) -> MagicMock:
    """Mock connection for the D2 read path: one fetchone, three fetchalls.

    The route issues the instrument seek (``fetchone``) then, inside a single
    executor call, the universe-edge, coverage and head statements (each
    ``fetchall``). Ranges default to the coverage values so a test that cares
    only about the merged result need not restate the head probe.
    """
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = instrument_row
    cursor.fetchall.side_effect = [
        # Universe edges: both families have a bound, so the head probe runs
        # as a single UNION statement.
        [(_MINUTE.value, _EDGE), (_DAILY.value, _EDGE)],
        # Per-symbol coverage.
        [(_MINUTE.value, *minute), (_DAILY.value, *daily)],
        # Head probe past the edge.
        [
            (_MINUTE.value, *(minute_head if minute_head else (None, None))),
            (_DAILY.value, *(daily_head if daily_head else (None, None))),
        ],
    ]
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
    mock_conn = _mock_conn_for_detail(
        _INSTRUMENT,
        minute=(date(2024, 1, 1), date(2026, 1, 1)),
        daily=(date(2000, 1, 1), date(2026, 1, 1)),
    )
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/AAPL")
    assert response.status_code == 200
    body = response.json()
    assert "1m" in body["available"]
    assert "1d" in body["available"]


def test_symbol_detail_daily_only(test_app: FastAPI) -> None:
    mock_conn = _mock_conn_for_detail(
        _INSTRUMENT, daily=(date(2000, 1, 1), date(2026, 1, 1))
    )
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/AAPL")
    assert response.status_code == 200
    body = response.json()
    assert "1d" in body["available"]
    assert "1m" not in body["available"]


def test_symbol_detail_not_found(test_app: FastAPI) -> None:
    mock_conn = _mock_conn_for_detail(None)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    client = TestClient(test_app)
    response = client.get("/api/v1/symbols/FAKESYMBOL")
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "detail" not in body


# ---------------------------------------------------------------------------
# D2 read path (slice 187, task 9)
# ---------------------------------------------------------------------------


def test_head_probe_supplies_the_leading_edge_over_coverage(
    test_app: FastAPI,
) -> None:
    """The point of the whole slice: coverage is 52 days stale on prod, and the
    advertised end must still be the real one (D2/D5)."""
    mock_conn = _mock_conn_for_detail(
        _INSTRUMENT,
        daily=(date(1993, 1, 29), date(2026, 6, 12)),
        daily_head=(date(2026, 6, 15), date(2026, 8, 3)),
    )
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    body = TestClient(test_app).get("/api/v1/symbols/AAPL").json()
    assert body["available"]["1d"] == {"start": "1993-01-29", "end": "2026-08-03"}


def test_symbol_absent_everywhere_returns_empty_available(
    test_app: FastAPI,
) -> None:
    """A known instrument with no bars in either family: ``available: {}``,
    not a 500 and not a 404 — the pre-187 contract."""
    mock_conn = _mock_conn_for_detail(_INSTRUMENT)
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    response = TestClient(test_app).get("/api/v1/symbols/AAPL")
    assert response.status_code == 200
    assert response.json()["available"] == {}


def test_ranges_are_read_in_one_executor_dispatch(test_app: FastAPI) -> None:
    """Regression guard for D7: no ``asyncio.gather`` over a pooled connection.

    psycopg serializes on the connection lock, so the pre-187 gather bought no
    parallelism and paid the sum of both queries (~2.7-4.1 s). Asserted by
    counting dispatches rather than by reading the source, so reintroducing the
    gather in any form fails.
    """
    mock_conn = _mock_conn_for_detail(
        _INSTRUMENT, daily=(date(2000, 1, 1), date(2026, 1, 1))
    )
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    dispatches: list[str] = []
    real_get_loop = asyncio.get_running_loop

    class _CountingLoop:
        """Proxy that records each executor dispatch and delegates everything
        else to the real running loop.

        **Why a proxy and not a substitute loop.** ``TestClient`` creates and
        owns the event loop the handler runs on, so the future returned by
        ``run_in_executor`` must come from *that* loop — a loop constructed here
        would never be the one awaiting, and the request hangs until the
        pytest-timeout kills it (observed while writing this test). Delegating
        every other attribute keeps the object a real loop in all respects
        except the one call this test counts.

        ``__getattr__`` fires only for attributes missing on the proxy, so a
        genuinely absent loop attribute still raises ``AttributeError`` from the
        underlying loop, unchanged (review F007).
        """

        def __init__(self, loop: Any) -> None:
            self._loop = loop

        def run_in_executor(self, executor: Any, func: Any, *args: Any) -> Any:
            dispatches.append(getattr(func, "__name__", repr(func)))
            return self._loop.run_in_executor(executor, func, *args)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._loop, name)

    with patch.object(
        symbols_module.asyncio,
        "get_running_loop",
        lambda: _CountingLoop(real_get_loop()),
    ):
        assert TestClient(test_app).get("/api/v1/symbols/AAPL").status_code == 200

    # One dispatch for the instrument seek, one for all three range statements.
    assert len(dispatches) == 2, (
        f"expected 2 executor dispatches (instrument + ranges), got "
        f"{len(dispatches)}: {dispatches}"
    )


def test_no_range_statement_lacks_a_time_bound(test_app: FastAPI) -> None:
    """Success criterion 1 at the route level: every statement the endpoint
    issues against a raw hypertable carries a prunable bound (D1)."""
    mock_conn = _mock_conn_for_detail(
        _INSTRUMENT, daily=(date(2000, 1, 1), date(2026, 1, 1))
    )
    test_app.dependency_overrides[get_db] = lambda: mock_conn

    TestClient(test_app).get("/api/v1/symbols/AAPL")

    for call in mock_conn.execute.call_args_list:
        sql = call.args[0]
        if "daily_ohlcv" in sql or "minute_5min_ohlcv" in sql:
            assert "time > %s" in sql or "time_bucket > %s" in sql, (
                f"unbounded aggregate reached the read path: {sql}"
            )
