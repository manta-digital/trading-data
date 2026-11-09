"""Exit codes of ``mt data tick parity`` (slice 306, D7).

0 every compared minute agrees; 3 a finished comparison with a finding; 1 a bad
target; 4 a database failure, with nothing on stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tick_support.cli import clear_tick_env, cli_settings, closed_port
from tick_support.parity_seed import SESSION, ParityDb, bar, run_parity, seed_parity
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.tick_exit import (
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_PREFLIGHT,
    EXIT_STORAGE,
)

SPAN = ("--start", str(SESSION), "--end", "2024-12-04")
runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    clear_tick_env(monkeypatch)


def _matching(db: ParityDb) -> list[dict[str, int]]:
    return [bar(db.window.instrument_id, b, m) for b, m in db.tick_minutes.items()]


def _one_volume_off(db: ParityDb) -> list[dict[str, int]]:
    bars = _matching(db)
    bars[0]["volume"] += 1
    return bars


async def test_all_minutes_agreeing_exits_zero(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    db = await seed_parity(migrated_tick_db, session_migrated_db, tmp_path, _matching)

    result = await run_parity(db, *SPAN)

    assert result.exit_code == EXIT_OK, result.output
    assert json.loads(result.stdout)["exit_code"] == EXIT_OK


async def test_a_differing_minute_exits_three_with_the_report_printed(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> None:
    db = await seed_parity(
        migrated_tick_db, session_migrated_db, tmp_path, _one_volume_off
    )

    result = await run_parity(db, *SPAN)

    assert result.exit_code == EXIT_PARTIAL, result.output
    assert json.loads(result.stdout)["exit_code"] == EXIT_PARTIAL


def test_an_unknown_contract_exits_one(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    with cli_settings(migrated_tick_db, session_migrated_db):
        result = runner.invoke(app, ["data", "tick", "parity", "--contract", "ESZ9"])

    assert result.exit_code == EXIT_PREFLIGHT, result.output
    assert result.stdout == ""


def test_a_failed_connection_exits_four_with_nothing_on_stdout(
    session_migrated_db: str,
) -> None:
    dead = f"postgresql://nobody@127.0.0.1:{closed_port()}/none"
    with cli_settings(dead, session_migrated_db):
        result = runner.invoke(
            app, ["data", "tick", "parity", "--contract", "ESZ4", "--json"]
        )

    assert result.exit_code == EXIT_STORAGE, result.output
    assert result.stdout == ""
