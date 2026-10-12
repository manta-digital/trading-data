"""The ``futures ticks`` line in ``mt data overview`` end to end (slice 230;
FR13, FR14).

The September 2024 roll is seeded (``roll_seed``) and one ledger row's
``last_event_ns`` is moved to a known instant, the newest. The primary
database is the scratch one; no EODHD key, so no network.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import ESZ4, seed_september_roll
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.tick.ingest_records import utc_ns

runner = CliRunner(env={"COLUMNS": "200"})
NEWEST = datetime(2024, 9, 27, 20, 59, 59, 500_000, tzinfo=UTC)


@pytest.fixture
def seeded(migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    clear_tick_env(monkeypatch)
    seed_september_roll(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_ingest_ledger SET last_event_ns = %s"
            " WHERE session_date = %s AND instrument_id = %s",
            (utc_ns(NEWEST), date(2024, 9, 27), ESZ4.instrument_id),
        )


def _overview(tick_url: str | None, primary_url: str) -> tuple[Any, dict[str, Any]]:
    with cli_settings(tick_url, primary_url):
        text = runner.invoke(app, ["data", "overview"])
        body = json.loads(runner.invoke(app, ["data", "overview", "--json"]).stdout)
    return text, body


def _tick(body: dict[str, Any]) -> dict[str, Any]:
    (source,) = [s for s in body["sources"] if s["name"] == "futures ticks"]
    return source


def test_the_line_shows_the_newest_ingested_event(
    seeded: None, migrated_tick_db: str, session_migrated_db: str
) -> None:
    text, body = _overview(migrated_tick_db, session_migrated_db)

    assert text.exit_code == 0, text.output
    assert "futures ticks    2024-09-27 20:59 UTC" in text.stdout
    assert _tick(body) == {
        "name": "futures ticks",
        "newest": NEWEST.isoformat(),
        "unavailable": None,
    }
    assert [s["name"] for s in body["sources"]][-1] == "futures ticks"


@pytest.mark.parametrize("case", ["unset", "unreachable", "migrations_pending"])
def test_each_degraded_state_keeps_the_screen_and_the_exit(
    case: str,
    ephemeral_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_tick_env(monkeypatch)
    url = {
        "unset": None,
        "unreachable": f"postgresql://nobody@127.0.0.1:{closed_port()}/none",
        "migrations_pending": ephemeral_tick_db,
    }[case]
    expected = {
        "unset": "not configured (MT_TICK_DB_URL unset)",
        "unreachable": "unreachable: ",
        "migrations_pending": "migrations pending (mt data migrate apply --track tick)",
    }[case]

    text, body = _overview(url, session_migrated_db)

    assert text.exit_code == 0, text.output  # as without the tick line
    assert f"futures ticks    {expected}" in text.stdout
    assert "minute bars" in text.stdout and "EODHD credits" in text.stdout
    assert _tick(body)["unavailable"].startswith(expected)
    assert _tick(body)["newest"] is None
