"""Unit tests for the health endpoint.

The lifespan hook is bypassed by instantiating ``TestClient`` without
entering it as a context manager (lifespan only runs on context-enter).
``get_db`` is overridden so no DB connection is touched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Generator, Iterator
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.data.maintenance.status_coverage import (
    COVERAGE_VIEWS,
    CoverageFreshness,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

_HEALTH_MODULE = "manta_trading.api_server.routes.health"


def _freshness(*, stale: bool) -> CoverageFreshness:
    return CoverageFreshness(
        verdicts=tuple(
            FreshnessVerdict(
                view_name=view,
                is_fresh=not (stale and index == 0),
                signals=(
                    (StalenessSignal.NOT_SCHEDULED,) if stale and index == 0 else ()
                ),
                lag=timedelta(days=4) if stale and index == 0 else timedelta(0),
                threshold=timedelta(days=1),
                detail="test verdict",
            )
            for index, view in enumerate(COVERAGE_VIEWS)
        )
    )


@contextlib.contextmanager
def _mocked_coverage(*, stale: bool) -> Iterator[MagicMock]:
    """Patch the freshness probe at the health module's import site."""
    with patch(
        f"{_HEALTH_MODULE}.check_coverage_freshness",
        return_value=_freshness(stale=stale),
    ) as probe:
        yield probe


def _ok_db() -> Generator[psycopg.Connection[Any], None, None]:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute = MagicMock(return_value=None)
    yield conn


def _error_db() -> Generator[psycopg.Connection[Any], None, None]:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute = MagicMock(side_effect=psycopg.OperationalError("conn refused"))
    yield conn


@pytest.fixture
def test_app() -> FastAPI:
    """Build a fresh app for each test, with a sentinel pool installed.

    Lifespan is bypassed by never entering ``TestClient`` as a context
    manager; the dependency override on ``get_db`` means the sentinel
    pool is never read.
    """
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    return app


def test_health_ok(test_app: FastAPI) -> None:
    test_app.dependency_overrides[get_db] = _ok_db
    client = TestClient(test_app)
    with _mocked_coverage(stale=False):
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "coverage": "ok"}


def test_health_stale_coverage(test_app: FastAPI) -> None:
    """A stale cagg is reported, not escalated to a failing health check."""
    test_app.dependency_overrides[get_db] = _ok_db
    client = TestClient(test_app)
    with _mocked_coverage(stale=True):
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["db"] == "ok"
    assert body["coverage"] == "stale"


def test_health_coverage_probed_exactly_once(test_app: FastAPI) -> None:
    """Guards against an accidental double-probe on the liveness path."""
    test_app.dependency_overrides[get_db] = _ok_db
    client = TestClient(test_app)
    with _mocked_coverage(stale=False) as probe:
        client.get("/api/v1/health")
    assert probe.call_count == 1


def test_health_db_error(test_app: FastAPI) -> None:
    test_app.dependency_overrides[get_db] = _error_db
    client = TestClient(test_app)
    with _mocked_coverage(stale=False) as probe:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "error"
    # D6: coverage is meaningless noise on top of an unreachable DB.
    assert "coverage" not in body
    assert not probe.called


def test_health_route_registered() -> None:
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/health" in paths
