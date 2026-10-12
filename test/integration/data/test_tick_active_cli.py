"""``mt data tick active`` end to end (slice 229, FR6, FR8, FR8b; TD7 exits).

No Databento key and no archive directory. The September 2024 roll is seeded
(``roll_seed``); the calendar is the scratch production database's.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.roll_seed import seed_september_roll
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick import active_contract
from manta_trading.data.tick.constants import TICK_ENV_PREFIX
from manta_trading.data.tick.universe import TICK_UNIVERSE

runner = CliRunner(env={"COLUMNS": "200"})
ACTIVE = ["data", "tick", "active"]
SEPTEMBER = ["--product", "ES", "--start", "2024-09-05", "--end", "2024-09-28"]


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _settings(tick_url: str, calendar_url: str) -> Iterator[None]:
    """The settings, and no logging setup (see ``test_tick_status_cli``)."""
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=tick_url,
        timescale_db_url=calendar_url,
    )
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


@pytest.fixture
def seeded(
    migrated_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)
    seed_september_roll(migrated_tick_db)
    with _settings(migrated_tick_db, session_migrated_db):
        yield


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*ACTIVE, *args])


def test_resolved_range_exits_zero_with_segments(seeded: None) -> None:
    result = _invoke(*SEPTEMBER, "--rule", "v.0")
    assert result.exit_code == 0, result.output
    assert "2024-09-05 → 2024-09-17  ESU4 (118)" in result.output
    assert "2024-09-18 → 2024-09-27  ESZ4 (183748)" in result.output


def test_json_range_carries_segments_and_exit_code(seeded: None) -> None:
    result = _invoke(*SEPTEMBER, "--rule", "c.0+5s", "--json")
    assert result.exit_code == 0, result.output
    body = json.loads(result.output)
    (schedule,) = body["schedules"]
    assert schedule["rule"] == "c.0+5s" and schedule["is_default"] is False
    assert [s["first_session"] for s in schedule["segments"]] == [
        "2024-09-05",
        "2024-09-16",
    ]
    assert body["exit_code"] == 0


def test_unresolved_sessions_exit_three_with_output(seeded: None) -> None:
    result = _invoke(
        "--product",
        "ES",
        "--rule",
        "v.0",
        "--start",
        "2024-09-03",
        "--end",
        "2024-09-10",
    )
    assert result.exit_code == 3, result.output
    assert "unresolved 2024-09-03" in result.output
    assert "ESU4 (118)" in result.output


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--rule", "n.0"], "unknown roll method 'n'; known: c, v"),
        (["--rule", "v.0+2s"], "offset suffix is allowed only on calendar"),
        (
            ["--product", "ES", "--start", "2020-01-02", "--end", "2026-01-02"],
            "TICK_ROLL_MAX_SESSIONS",
        ),
        (["--product", "ZZ"], "--product ZZ is not in the universe"),
    ],
)
def test_refusals_exit_one(seeded: None, args: list[str], message: str) -> None:
    result = _invoke(*args)
    assert result.exit_code == 1, result.output
    assert message in " ".join(result.output.split())


def test_no_product_answers_every_universe_product_and_status_rule(
    seeded: None,
) -> None:
    result = _invoke("--json")
    assert result.exit_code == 0, result.output
    lines = json.loads(result.output)["lines"]
    assert [(line["product"], line["rule"]) for line in lines] == [
        (e.product, rule) for e in TICK_UNIVERSE for rule in ("v.0", "c.0")
    ]
    assert {line["raw_symbol"] for line in lines} == {"ESZ4"}


def test_unreachable_tick_database_exits_four(
    session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MT_TICK_DB_URL", raising=False)
    dead = f"postgresql://nobody@127.0.0.1:{_closed_port()}/none"
    with _settings(dead, session_migrated_db):
        result = _invoke(*SEPTEMBER)
    assert result.exit_code == 4, result.output


def test_held_lock_times_out_and_exits_four(
    seeded: None, migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(active_contract, "TICK_ROLL_STATEMENT_TIMEOUT_SECONDS", 1)
    with psycopg.connect(migrated_tick_db) as holder:
        holder.execute("LOCK TABLE tick_ingest_ledger IN ACCESS EXCLUSIVE MODE")
        result = _invoke(*SEPTEMBER, "--rule", "v.0")
        holder.rollback()
    assert result.exit_code == 4, result.output
    assert "statement timeout" in result.output
