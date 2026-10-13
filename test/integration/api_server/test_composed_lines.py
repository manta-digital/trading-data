"""The tick lines in ``/health``, ``/status`` and ``/overview`` (slice 231 TD7;
FR8–FR11, Integration Requirements).

Each surface answers HTTP 200 whatever the tick tier's state, and says the
state in its own field. CLI comparisons run ``mt data accounting --json`` and
``mt data overview --json`` in-process over the same databases.
"""

from __future__ import annotations

import json
from collections.abc import Generator, Iterator
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.api import CLOSED_PORT_SECRET
from tick_support.cli import clear_tick_env, cli_settings
from tick_support.roll_seed import ESZ4, seed_september_roll
from typer.testing import CliRunner

from manta_trading.api_server.deps import get_db
from manta_trading.cli.app import app as cli_app
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.market.schema.migrations import TRACKS

HEALTH, STATUS, OVERVIEW = "/api/v1/health", "/api/v1/status", "/api/v1/overview"
NOT_CONFIGURED = "not configured (MT_TICK_DB_URL unset)"
NEWEST = datetime(2024, 9, 27, 20, 59, 59, 500_000, tzinfo=UTC)


@pytest.fixture
def seeded(migrated_tick_db: str, tick_client: TestClient) -> TestClient:
    seed_september_roll(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_ingest_ledger SET last_event_ns = %s"
            " WHERE session_date = %s AND instrument_id = %s",
            (utc_ns(NEWEST), date(2024, 9, 27), ESZ4.instrument_id),
        )
    return tick_client


@pytest.fixture
def no_minute_scan() -> Iterator[None]:
    """``mt data accounting``'s equities scan is not under test here."""
    with patch(
        "manta_trading.cli.commands.accounting.compute_minute_accounting",
        return_value=[],
    ):
        yield


def _cli_json(
    monkeypatch: pytest.MonkeyPatch, urls: tuple[str, str], *command: str
) -> Any:
    clear_tick_env(monkeypatch)
    with cli_settings(*urls):
        result = CliRunner().invoke(cli_app, ["data", *command, "--json"])
    return json.loads(result.stdout)


def _tick_source(body: dict[str, Any]) -> dict[str, Any]:
    (source,) = [s for s in body["sources"] if s["name"] == "futures ticks"]
    return source


# -- health (7.2) --------------------------------------------------------------


def test_health_reports_ok_without_a_detail(tick_client: TestClient) -> None:
    body = tick_client.get(HEALTH).json()

    assert (body["db"], body["tick_db"]) == ("ok", "ok")
    assert "tick_detail" not in body


def test_health_reports_not_configured(unconfigured_tick_client: TestClient) -> None:
    response = unconfigured_tick_client.get(HEALTH)

    assert response.status_code == 200
    assert (response.json()["tick_db"], response.json()["tick_detail"]) == (
        "not_configured",
        "MT_TICK_DB_URL unset",
    )


def test_health_reports_unreachable_without_the_password(
    closed_tick_client: TestClient,
) -> None:
    response = closed_tick_client.get(HEALTH)

    assert response.status_code == 200
    assert response.json()["tick_db"] == "unreachable"
    assert CLOSED_PORT_SECRET not in response.text


def test_health_reports_migrations_pending(
    tick_client: TestClient, migrated_tick_db: str
) -> None:
    """On the function-scoped throwaway: the newest ledger row is removed."""
    newest = TRACKS["tick"][-1]["id"]
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE migration_id = %s", (newest,))

    response = tick_client.get(HEALTH)

    assert response.status_code == 200
    assert (response.json()["tick_db"], response.json()["tick_detail"]) == (
        "migrations_pending",
        newest,
    )


def test_a_primary_failure_still_reports_db_error(tick_client: TestClient) -> None:
    def failing() -> Generator[psycopg.Connection[Any], None, None]:
        conn = MagicMock(spec=psycopg.Connection)
        conn.execute = MagicMock(side_effect=psycopg.OperationalError("refused"))
        yield conn

    tick_client.app.dependency_overrides[get_db] = failing  # type: ignore[attr-defined]

    body = tick_client.get(HEALTH).json()

    assert (body["db"], body["tick_db"]) == ("error", "ok")


# -- status (7.4) --------------------------------------------------------------


def test_the_status_tick_block_equals_the_cli_accounting(
    seeded: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
    no_minute_scan: None,
) -> None:
    block = seeded.get(STATUS).json()["tick"]
    cli = _cli_json(monkeypatch, (migrated_tick_db, session_migrated_db), "accounting")

    assert block == cli["tick"]
    assert block["state"] == "ok" and block["products"][0]["product"] == "ES"


def test_the_equities_filters_leave_the_block_alone(seeded: TestClient) -> None:
    whole = seeded.get(STATUS).json()["tick"]
    filtered = seeded.get(STATUS, params={"symbol": "AAPL", "health": "OK"})

    assert filtered.status_code == 200
    assert filtered.json()["tick"] == whole


def test_status_with_the_tick_database_down(closed_tick_client: TestClient) -> None:
    response = closed_tick_client.get(STATUS)

    assert response.status_code == 200
    body = response.json()
    assert body["tick"]["state"] == "unreachable" and body["tick"]["products"] == []
    assert "rows" in body and "summary" in body
    assert CLOSED_PORT_SECRET not in response.text


def test_status_not_configured(unconfigured_tick_client: TestClient) -> None:
    response = unconfigured_tick_client.get(STATUS)

    assert response.status_code == 200
    assert response.json()["tick"] == {
        "state": "not_configured",
        "detail": "MT_TICK_DB_URL unset",
        "products": [],
    }


# -- overview (7.6) ------------------------------------------------------------


def test_the_overview_source_equals_the_cli(
    seeded: TestClient,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = seeded.get(OVERVIEW).json()
    cli = _cli_json(monkeypatch, (migrated_tick_db, session_migrated_db), "overview")

    api_source, cli_source = _tick_source(body), _tick_source(cli)
    assert body["sources"][-1]["name"] == "futures ticks"
    assert api_source["unavailable"] is cli_source["unavailable"] is None
    assert datetime.fromisoformat(api_source["newest"]) == NEWEST
    assert datetime.fromisoformat(cli_source["newest"]) == NEWEST
    assert all(s["unavailable"] is None for s in body["sources"])


def test_overview_not_configured(unconfigured_tick_client: TestClient) -> None:
    response = unconfigured_tick_client.get(OVERVIEW)

    assert response.status_code == 200
    assert _tick_source(response.json()) == {
        "name": "futures ticks",
        "newest": None,
        "unavailable": NOT_CONFIGURED,
    }


def test_overview_unreachable(closed_tick_client: TestClient) -> None:
    response = closed_tick_client.get(OVERVIEW)

    assert response.status_code == 200
    *others, source = response.json()["sources"]
    assert source["name"] == "futures ticks" and source["newest"] is None
    assert source["unavailable"].startswith("unreachable:")
    assert all(s["unavailable"] is None for s in others)
