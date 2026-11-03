"""``mt data tick coverage --schema ohlcv-1m`` (slice 302, D6).

The derived 2024-12-03 bar day is ingested for ES; the ledger's bar count and
volume are then compared with ``count(*)`` and ``sum(volume)`` over
``tick_ohlcv_1m``, per instrument and session.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.runs import connect
from tick_support.tick_bar_helpers import seed_bar_units
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick import universe
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_coverage import (
    CoverageSchemaError,
    build_coverage,
    coverage_family,
)
from manta_trading.data.tick.universe import HistoryWant, TickUniverseEntry

runner = CliRunner(env={"COLUMNS": "200"})


@contextmanager
def _app_patches(settings: Settings) -> Iterator[None]:
    """The settings, and no logging setup (as in test_tick_status_cli)."""
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


START, END = date(2024, 12, 3), date(2024, 12, 5)
ES = next(e for e in universe.TICK_UNIVERSE if e.product == "ES")
WITH_BARS: TickUniverseEntry = replace(
    ES,
    tier=TickSchema.TBBO,
    start=date(2024, 12, 3),
    end=date(2024, 12, 5),
    history=(
        HistoryWant(TickSchema.DEFINITION, date(2024, 12, 3), date(2024, 12, 5)),
        HistoryWant(TickSchema.OHLCV_1M, date(2024, 12, 3), date(2024, 12, 5)),
    ),
)
ARGS = [
    "data", "tick", "coverage", "--product", "ES",
    "--start", f"{START}", "--end", f"{END}", "--schema", "ohlcv-1m",
]  # fmt: skip


@pytest.fixture
def bars(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    url = migrated_tick_db

    async def seed() -> None:
        await seed_bar_units(url, tmp_path, job_id="BARS")
        report = await run_ingest_phase(
            url, tmp_path, ingest_inputs(url, session_migrated_db)
        )
        assert report.summary["ingested"] == 1

    asyncio.run(seed())
    monkeypatch.setattr(universe, "TICK_UNIVERSE", (WITH_BARS,))
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=url,
        timescale_db_url=session_migrated_db,
    )
    with _app_patches(settings):
        yield url


async def _coverage(url: str, calendar_url: str) -> Any:
    async with connect(url) as conn:
        return await build_coverage(
            conn, calendar_url, WITH_BARS, START, END, TickSchema.OHLCV_1M
        )


def test_ledger_equal_to_raw_is_ok_and_exits_0(bars: str) -> None:
    result = runner.invoke(app, [*ARGS, "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["exit_code"] == 0
    (es,) = body["products"]
    assert es["schema"] == "ohlcv-1m" and es["mismatched"] is False
    assert [s["session_date"] for s in es["sessions"]] == ["2024-12-03", "2024-12-04"]
    for session in es["sessions"]:
        assert session["check"] == "ok"
        assert session["ledger"] == session["raw"] > 0
        assert session["ledger_volume"] == session["raw_volume"] > 0


def test_the_text_table_shows_bars_and_volume(bars: str) -> None:
    result = runner.invoke(app, ARGS[:-2] + ["--schema", "ohlcv-1m"])
    assert result.exit_code == 0, result.output
    assert "ledger vol" in result.stdout and "raw vol" in result.stdout
    assert "ohlcv-1m" in result.stdout


def test_a_deleted_raw_bar_is_a_mismatch_and_exits_3(bars: str) -> None:
    with psycopg.connect(bars, autocommit=True) as conn:
        deleted = conn.execute(
            f"DELETE FROM {BAR_1M_TABLE} WHERE (instrument_id, ts_event) ="
            f" (SELECT instrument_id, ts_event FROM {BAR_1M_TABLE}"
            " ORDER BY volume DESC LIMIT 1)"
        )
        assert deleted.rowcount == 1
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 3, result.output
    assert "mismatch" in result.stdout and "volume ledger" in result.stdout


def test_a_changed_raw_volume_alone_is_a_mismatch(
    bars: str, session_migrated_db: str
) -> None:
    with psycopg.connect(bars, autocommit=True) as conn:
        conn.execute(
            f"UPDATE {BAR_1M_TABLE} SET volume = volume + 1 WHERE (instrument_id,"
            f" ts_event) = (SELECT instrument_id, ts_event FROM {BAR_1M_TABLE}"
            " ORDER BY ts_event LIMIT 1)"
        )
    coverage = asyncio.run(_coverage(bars, session_migrated_db))
    assert coverage.mismatched
    (bad,) = [s for s in coverage.sessions if s.mismatches]
    (mismatch,) = bad.mismatches
    assert mismatch.ledger == mismatch.raw  # the count agrees
    assert mismatch.raw_volume == mismatch.ledger_volume + 1
    assert runner.invoke(app, ARGS).exit_code == 3


def test_a_schema_the_entry_does_not_want_is_refused(
    bars: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(universe, "TICK_UNIVERSE", (replace(WITH_BARS, history=()),))
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 1, result.output
    assert "does not want ohlcv-1m" in result.output


def test_an_unknown_schema_name_is_refused(bars: str) -> None:
    result = runner.invoke(app, ARGS[:-1] + ["nonsense"])
    assert result.exit_code == 1, result.output
    assert "--schema nonsense is not a schema" in result.output


def test_the_family_chosen_by_schema() -> None:
    assert coverage_family(WITH_BARS, None) == (SchemaFamily.TICK_TIER, None)
    assert coverage_family(WITH_BARS, TickSchema.TBBO) == (SchemaFamily.TICK_TIER, None)
    assert coverage_family(WITH_BARS, TickSchema.OHLCV_1M) == (
        SchemaFamily.BAR_1M,
        TickSchema.OHLCV_1M,
    )
    with pytest.raises(CoverageSchemaError, match="does not want trades"):
        coverage_family(WITH_BARS, TickSchema.TRADES)
    with pytest.raises(CoverageSchemaError, match="does not want ohlcv-1m"):
        coverage_family(replace(WITH_BARS, history=()), TickSchema.OHLCV_1M)
