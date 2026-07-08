"""Unit tests for the health endpoint.

The lifespan hook is bypassed by instantiating ``TestClient`` without
entering it as a context manager (lifespan only runs on context-enter).
``get_db`` is overridden so no DB connection is touched.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import MagicMock

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db


def _ok_db() -> Generator[psycopg.Connection[Any], None, None]:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute = MagicMock(return_value=None)
    yield conn


def _error_db() -> Generator[psycopg.Connection[Any], None, None]:
    conn = MagicMock(spec=psycopg.Connection)
    conn.execute = MagicMock(
        side_effect=psycopg.OperationalError("conn refused")
    )
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
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_db_error(test_app: FastAPI) -> None:
    test_app.dependency_overrides[get_db] = _error_db
    client = TestClient(test_app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "error"


def test_health_route_registered() -> None:
    app = create_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/api/v1/health" in paths
