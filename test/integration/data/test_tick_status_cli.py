"""``mt data tick status`` and ``coverage`` end to end (slice 225, 6.7; FR11).

No Databento key and no archive directory: both verbs only read. Sync tests:
each verb runs its own event loop.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.runs import connect
from tick_support.tier_units import TRADES_DAY, seed_tier_unit
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_ENV_PREFIX, TickSchema
from manta_trading.data.tick.tick_coverage import build_coverage
from manta_trading.data.tick.universe import TICK_UNIVERSE

runner = CliRunner(env={"COLUMNS": "200"})
START, END = date(2024, 9, 3), date(2024, 9, 5)
COVERAGE = ["data", "tick", "coverage", "--start", f"{START}", "--end", f"{END}"]


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@contextmanager
def _app_patches(settings: Settings) -> Iterator[None]:
    """The settings, and no logging setup: the app's handlers would bind
    CliRunner's stream, closed after each invoke, and break a later module."""
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


def _patched(tick_url: str, calendar_url: str) -> Any:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=tick_url,
        timescale_db_url=calendar_url,
    )
    assert settings.databento_api_key is None and settings.tick_archive_dir is None
    return _app_patches(settings)


@pytest.fixture
def ingested(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)
    url = migrated_tick_db

    async def seed() -> None:
        await seed_tier_unit(url, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="T")
        await run_ingest_phase(url, tmp_path, ingest_inputs(url, session_migrated_db))

    asyncio.run(seed())
    with _patched(url, session_migrated_db):
        yield


def test_status_prints_the_buckets_and_the_complete_note(ingested: None) -> None:
    result = runner.invoke(app, ["data", "tick", "status"])
    assert result.exit_code == 0, result.output
    assert "sessions held: 2" in result.stdout
    assert "complete = every unit ingested; raw-count proof: mt data tick coverage" in (
        result.stdout
    )
    assert "spreads hidden, --all-instruments to list" in result.stdout


def test_status_json_is_to_dict_plus_exit_code(ingested: None) -> None:
    result = runner.invoke(app, ["data", "tick", "status", "--json"])
    body = json.loads(result.stdout)
    assert body["exit_code"] == 0
    assert body["complete_basis"] == "units"
    assert body["products"][0]["sessions_held"] == 2


def test_coverage_json_is_to_dict_plus_exit_code(
    ingested: None, migrated_tick_db: str, session_migrated_db: str
) -> None:
    result = runner.invoke(app, [*COVERAGE, "--json"])
    assert result.exit_code == 0, result.output

    async def expected() -> dict[str, Any]:
        async with connect(migrated_tick_db) as conn:
            coverage = await build_coverage(
                conn, session_migrated_db, TICK_UNIVERSE[0], START, END
            )
            return coverage.to_dict()

    body = json.loads(result.stdout)
    assert body == {"products": [asyncio.run(expected())], "exit_code": 0}


def test_a_coverage_mismatch_exits_3(ingested: None, migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM tick_trade WHERE ctid ="
            " (SELECT ctid FROM tick_trade ORDER BY ts_event LIMIT 1)"
        )
    result = runner.invoke(app, COVERAGE)
    assert result.exit_code == 3, result.output
    assert "mismatch" in result.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["data", "tick", "status", "--product", "GC"],
        ["data", "tick", "coverage", "--start", "2024-09-05", "--end", "2024-09-05"],
    ],
)
def test_preflight_refusals_exit_1(ingested: None, args: list[str]) -> None:
    assert runner.invoke(app, args).exit_code == 1


@pytest.mark.parametrize("verb", [["status"], COVERAGE[2:]])
def test_an_unreachable_tick_database_exits_4(
    session_migrated_db: str, verb: list[str]
) -> None:
    url = f"postgresql://nobody@127.0.0.1:{_closed_port()}/tick"
    with _patched(url, session_migrated_db):
        result = runner.invoke(app, ["data", "tick", *verb])
    assert result.exit_code == 4, result.output
