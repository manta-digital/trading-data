"""``mt data tick ingest`` end to end, with no Databento key (slice 225, FR11).

The real verb on ``open_tick_store``: a migrated tick database with a real
slice seeded, the session-migrated calendar, and no provider key anywhere.
Sync tests: the verb runs its own event loop.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from tick_support.ingest import trade_count
from tick_support.tier_units import TRADES_DAY, seed_tier_unit
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_ENV_PREFIX, TickSchema

runner = CliRunner(env={"COLUMNS": "200"})


@pytest.fixture
def no_key(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # no .env for the preflight's key check
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=migrated_tick_db,
        tick_archive_dir=tmp_path,
        timescale_db_url=session_migrated_db,
    )
    assert settings.databento_api_key is None
    with patch("manta_trading.cli.app.Settings", side_effect=lambda: settings):
        yield


def test_ingest_runs_without_a_provider_key(
    no_key: None, migrated_tick_db: str, tmp_path: Path
) -> None:
    seeded = asyncio.run(
        seed_tier_unit(
            migrated_tick_db, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="JOB"
        )
    )
    result = runner.invoke(app, ["data", "tick", "ingest", "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["phases"][0]["summary"]["ingested"] == 1
    assert trade_count(migrated_tick_db, seeded.unit_id) == seeded.record_count


def test_an_unselectable_unit_id_is_reported_with_its_reason(
    no_key: None, migrated_tick_db: str, tmp_path: Path
) -> None:
    asyncio.run(
        seed_tier_unit(
            migrated_tick_db, tmp_path, TickSchema.TRADES, TRADES_DAY, job_id="JOB"
        )
    )
    result = runner.invoke(app, ["data", "tick", "ingest", "--unit-id", "999"])
    assert result.exit_code == 0, result.output
    assert "unit 999  → not_selectable  no such unit" in result.stdout
    assert "ingested 0 units" in result.stdout
