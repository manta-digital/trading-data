"""The tick pool's ``503`` posture (slice 231 TD2; FR8–FR11).

A probe route registered only here depends on ``tick_connection``, so the pool
is tested before any futures route exists.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Annotated, Any

import psycopg
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from kalshi_support.schema import apply_kalshi_track
from tick_support.api import CLOSED_PORT_SECRET, closed_port_url

from manta_trading.api_server.app import create_app
from manta_trading.api_server.tick_access import tick_connection
from manta_trading.data.tick.constants import TICK_API_CHECKOUT_TIMEOUT_SECONDS
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.market.schema.migrations import TRACKS

PROBE = "/test/tick-probe"
NEWEST_TICK_MIGRATION = TRACKS["tick"][-1]["id"]
#: Equities, Kalshi and operations routes that must not notice the tick tier.
UNTOUCHED = (
    "/api/v1/health",
    "/api/v1/bars/AAPL?granularity=1d&start=2024-09-16&end=2024-09-20",
    "/api/v1/symbols",
    "/api/v1/kalshi/categories",
    "/api/v1/overview",
)


def _with_probe(app: FastAPI) -> FastAPI:
    @app.get(PROBE)
    async def probe(
        conn: Annotated[Conn, Depends(tick_connection)],
    ) -> dict[str, Any]:
        cursor = await conn.execute("SELECT 1")
        return {"one": (await cursor.fetchone())[0]}  # type: ignore[index]

    return app


def _client(primary: str, tick: str | None) -> Iterator[TestClient]:
    with TestClient(_with_probe(create_app(db_url=primary, tick_db_url=tick))) as c:
        yield c


@pytest.fixture
def live(session_migrated_db: str, migrated_tick_db: str) -> Iterator[TestClient]:
    yield from _client(session_migrated_db, migrated_tick_db)


@pytest.fixture
def closed(session_migrated_db: str) -> Iterator[TestClient]:
    yield from _client(session_migrated_db, closed_port_url())


@pytest.fixture
def unset(session_migrated_db: str) -> Iterator[TestClient]:
    yield from _client(session_migrated_db, None)


def test_health_answers_200_under_every_fixture(
    tick_client: TestClient,
    closed_tick_client: TestClient,
    unconfigured_tick_client: TestClient,
) -> None:
    for client in (tick_client, closed_tick_client, unconfigured_tick_client):
        assert client.get("/api/v1/health").status_code == 200


def test_the_guard_keeps_the_env_tick_url_out(
    session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MT_TICK_DB_URL", closed_port_url())
    app = create_app(db_url=session_migrated_db)

    with TestClient(app):
        assert app.state.tick_pool is None


def test_a_live_database_serves(live: TestClient) -> None:
    response = live.get(PROBE)

    assert (response.status_code, response.json()) == (200, {"one": 1})


def test_not_configured_is_503(unset: TestClient) -> None:
    response = unset.get(PROBE)

    assert response.status_code == 503
    assert response.json() == {"error": "not configured (MT_TICK_DB_URL unset)"}


def test_a_closed_port_is_503_within_the_checkout_timeout(closed: TestClient) -> None:
    started = time.monotonic()
    response = closed.get(PROBE)
    elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["error"].startswith("unreachable: ")
    assert elapsed < TICK_API_CHECKOUT_TIMEOUT_SECONDS + 1
    assert CLOSED_PORT_SECRET not in response.text


def test_migrations_pending_then_recovery_without_restart(
    live: TestClient, migrated_tick_db: str
) -> None:
    """Deletes a ledger row in the function-scoped throwaway, then restores it."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        row = conn.execute(
            "DELETE FROM schema_migrations WHERE migration_id = %s"
            " RETURNING migration_id, applied_at, description",
            (NEWEST_TICK_MIGRATION,),
        ).fetchone()
        assert row is not None

        pending = live.get(PROBE)
        assert pending.status_code == 503
        assert pending.json() == {
            "error": "migrations pending (mt data migrate apply --track tick)"
        }

        conn.execute(
            "INSERT INTO schema_migrations (migration_id, applied_at, description)"
            " VALUES (%s, %s, %s)",
            row,
        )

    assert live.get(PROBE).status_code == 200


def test_equities_and_kalshi_do_not_notice_the_tick_tier(migrated_db: str) -> None:
    """Each route answers the same with the tick database down as with none.

    The primary carries both the minute chain and the Kalshi track, so every
    route reaches its own data path (empty tables, real schema).
    """
    primary = apply_kalshi_track(migrated_db)
    for path in UNTOUCHED:
        down_app = create_app(db_url=primary, tick_db_url=closed_port_url())
        with TestClient(down_app) as down, TestClient(create_app(primary)) as absent:
            got_down, got_absent = down.get(path), absent.get(path)
        assert got_down.status_code == got_absent.status_code, path
        assert got_down.status_code != 503, path
