"""``mt data tick debug`` end to end (slice 230, TD6, FR11).

Filter coverage lives in ``test_tick_debug_reads``; this checks one text and
one JSON run per verb on the seeded September roll, the enum rejections, the
spreads default, ``--as-of`` against the calendar, and the exits.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.roll_seed import SPREAD_ID, SeededRoll, seed_september_roll
from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner(env={"COLUMNS": "250"})
DEBUG = ["data", "tick", "debug"]


@pytest.fixture
def seeded(
    migrated_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[SeededRoll]:
    clear_tick_env(monkeypatch)
    roll = seed_september_roll(migrated_tick_db)
    with cli_settings(migrated_tick_db, session_migrated_db):
        yield roll


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*DEBUG, *args])


def _json(*args: str) -> list[dict[str, Any]]:
    result = _invoke(*args, "--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["exit_code"] == 0
    rows: list[dict[str, Any]] = body["rows"]
    return rows


@pytest.mark.parametrize("verb", ["requests", "units", "definitions"])
def test_each_verb_prints_a_table(seeded: SeededRoll, verb: str) -> None:
    result = _invoke(verb, "--product", "ES")

    assert result.exit_code == 0, result.output
    assert f"{verb}: " in result.stdout and "row(s)" in result.stdout


def test_requests_json(seeded: SeededRoll) -> None:
    rows = _json("requests")

    assert len(rows) == 2  # the definition request and the trades request
    assert rows[0]["symbols"] == "ES.FUT"
    assert rows[0]["estimated_cost_usd"] == "0.01"


def test_units_json_filters_by_state(seeded: SeededRoll) -> None:
    every = _json("units", "--product", "ES")
    verified = _json("units", "--state", "verified")

    assert len(every) == 1 + len(seeded.units)
    assert {r["state"] for r in every} == {"ingested"}
    assert verified == []


def test_definitions_hide_spreads_unless_asked(seeded: SeededRoll) -> None:
    outrights = _json("definitions", "--product", "ES")
    everything = _json("definitions", "--product", "ES", "--spreads")

    assert [r["raw_symbol"] for r in outrights] == ["ESU4", "ESZ4", "ESH5"]
    assert SPREAD_ID in {r["instrument_id"] for r in everything}
    assert outrights[0]["expiration_ns"] == "2024-09-20T13:30:00.000000000Z"


def test_definitions_as_of_a_session(seeded: SeededRoll) -> None:
    # Session 2024-09-23 opens after ESU4's expiry (09-20 13:30 UTC).
    rows = _json("definitions", "--product", "ES", "--as-of", "2024-09-23")

    assert [r["raw_symbol"] for r in rows] == ["ESZ4", "ESH5"]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["units", "--state", "bogus"], "--state 'bogus' is not one of"),
        (
            ["units", "--fetch-status", "failed"],
            "--fetch-status 'failed' is not one of",
        ),
        (["requests", "--product", "ZZ"], "--product ZZ is not in the universe"),
        (["definitions", "--as-of", "2024-09-23"], "--as-of needs --product"),
    ],
)
def test_refusals_exit_one(seeded: SeededRoll, args: list[str], message: str) -> None:
    result = _invoke(*args)

    assert result.exit_code == 1, result.output
    assert message in " ".join(result.output.split())


def test_a_pending_tick_migration_exits_one(
    ephemeral_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_tick_env(monkeypatch)
    with cli_settings(ephemeral_tick_db, session_migrated_db):
        result = _invoke("requests")

    assert result.exit_code == 1, result.output


def test_an_unreachable_tick_database_exits_four(
    session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_tick_env(monkeypatch)
    dead = f"postgresql://nobody@127.0.0.1:{closed_port()}/none"
    with cli_settings(dead, session_migrated_db):
        result = _invoke("units")

    assert result.exit_code == 4, result.output
