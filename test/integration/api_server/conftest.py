"""Fixtures for the futures API against throwaway databases (slice 231).

Every client is built with ``create_app(db_url=…, tick_db_url=…)``: the primary
is the session-scoped migrated throwaway (it holds the CME calendar), and the
tick URL is a function-scoped migrated throwaway, a closed port, or unset. The
guard keeps the developer's ``MT_TICK_DB_URL`` out of the unset case.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from tick_support.api import no_dev_tick_settings  # noqa: F401  (autouse guard)
from tick_support.cli import closed_port

from manta_trading.api_server.app import create_app

#: A password only the closed-port URL carries, for the no-secret checks (FR11).
CLOSED_PORT_SECRET = "t1ck-s3cret"


def client_for(primary_url: str, tick_url: str | None) -> Iterator[TestClient]:
    """A client over the real ``lifespan``, closed (pools and all) on exit."""
    with TestClient(create_app(db_url=primary_url, tick_db_url=tick_url)) as client:
        yield client


def closed_port_url() -> str:
    return f"postgresql://tick:{CLOSED_PORT_SECRET}@127.0.0.1:{closed_port()}/tick"


@pytest.fixture
def tick_client(
    session_migrated_db: str, migrated_tick_db: str
) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, migrated_tick_db)


@pytest.fixture
def closed_tick_client(session_migrated_db: str) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, closed_port_url())


@pytest.fixture
def unconfigured_tick_client(session_migrated_db: str) -> Iterator[TestClient]:
    yield from client_for(session_migrated_db, None)
