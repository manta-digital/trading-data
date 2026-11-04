"""``mt data tick coverage --schema statistics`` (slice 303, D8).

Units, definitions, ledger and statistics rows are seeded directly for session
2024-12-03 (its statistics window is 12-02 to 12-04; its tick-tier days are
12-02 and 12-03). The ledger's record count is compared with the stored rows per
required stat type, and traded volume with cleared volume per contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.rows import (
    insert_day_condition,
    insert_ledger_row,
    insert_statistics,
    insert_statistics_ledger_row,
)
from tick_support.statistics_seed import seed_outright, seed_unit, seed_units
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick import universe
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_TABLE,
    StatType,
)
from manta_trading.data.tick.universe import HistoryWant

runner = CliRunner(env={"COLUMNS": "200"})

SESSION = date(2024, 12, 3)
START, END = SESSION, date(2024, 12, 4)
WINDOW = [date(2024, 12, 2), date(2024, 12, 3), date(2024, 12, 4)]
EXACT, SHORT = 42_000_001, 42_000_002
ES = next(e for e in universe.TICK_UNIVERSE if e.product == "ES")
WITH_STATISTICS = replace(
    ES,
    tier=TickSchema.TBBO,
    start=START,
    end=date(2024, 12, 5),
    history=(
        HistoryWant(TickSchema.DEFINITION, START, date(2024, 12, 5)),
        HistoryWant(TickSchema.STATISTICS, START, date(2024, 12, 5)),
    ),
)
ARGS = [
    "data", "tick", "coverage", "--product", "ES",
    "--start", f"{START}", "--end", f"{END}", "--schema", "statistics",
]  # fmt: skip


@contextmanager
def _app_patches(settings: Settings) -> Iterator[None]:
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


def _seed(conn: psycopg.Connection[Any], volumes: dict[int, tuple[int, int]]) -> None:
    """Per contract ``(cleared, traded)``: every required type once, and the tick
    ledger's volume."""
    statistics = seed_units(conn, TickSchema.STATISTICS, WINDOW)[1]
    ticks = seed_units(conn, TickSchema.TBBO, WINDOW[:2])[1]
    definition = seed_unit(conn, TickSchema.DEFINITION, SESSION)
    for day in WINDOW:
        insert_day_condition(conn, condition_date=day, condition="available")
    for sequence, (instrument, (cleared, traded)) in enumerate(volumes.items()):
        seed_outright(conn, definition, instrument)
        for stat_type in StatType:
            insert_statistics(
                conn,
                statistics,
                instrument_id=instrument,
                stat_type=int(stat_type),
                quantity=cleared if stat_type is StatType.CLEARED_VOLUME else None,
                session_date=SESSION,
                sequence=sequence,
            )
            insert_statistics_ledger_row(
                conn,
                statistics,
                instrument_id=instrument,
                stat_type=int(stat_type),
                session_date=SESSION,
            )
        insert_ledger_row(
            conn,
            ticks,
            instrument_id=instrument,
            session_date=SESSION,
            volume=traded,
        )


@pytest.fixture
def seeded(
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    monkeypatch.setattr(universe, "TICK_UNIVERSE", (WITH_STATISTICS,))
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=migrated_tick_db,
        timescale_db_url=session_migrated_db,
    )
    with _app_patches(settings):
        yield migrated_tick_db


def _agreeing(conn_url: str, volumes: dict[int, tuple[int, int]]) -> None:
    with psycopg.connect(conn_url, autocommit=True) as conn:
        _seed(conn, volumes)


def test_agreeing_counts_and_an_under_volume_exit_0_with_the_json_shape(
    seeded: str,
) -> None:
    _agreeing(seeded, {EXACT: (100, 100), SHORT: (200, 150)})
    result = runner.invoke(app, [*ARGS, "--json"])
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["exit_code"] == 0
    (es,) = body["products"]
    assert es["schema"] == "statistics" and es["mismatched"] is False
    (session,) = es["sessions"]
    assert session["session_date"] == "2024-12-03"
    assert session["check"] == "ok" and session["status"] == "complete"
    assert session["ledger"] == session["raw"] == 6  # two contracts x three types
    assert "ledger_volume" not in session and "raw_volume" not in session
    checks = {c["instrument_id"]: c for c in es["volume_checks"]}
    assert checks[EXACT]["verdict"] == "agree"
    assert (checks[SHORT]["verdict"], checks[SHORT]["ratio"]) == ("under", 0.75)
    assert {c["family"] for c in es["volume_checks"]} == {"tick_tier"}


def test_a_deleted_statistics_row_is_a_count_mismatch_and_exits_3(
    seeded: str,
) -> None:
    _agreeing(seeded, {EXACT: (100, 100)})
    with psycopg.connect(seeded, autocommit=True) as conn:
        deleted = conn.execute(
            f"DELETE FROM {STATISTICS_TABLE} WHERE stat_type = %s",
            (int(StatType.OPEN_INTEREST),),
        )
        assert deleted.rowcount == 1
    result = runner.invoke(app, [*ARGS, "--json"])
    assert result.exit_code == 3, result.output
    (es,) = json.loads(result.stdout)["products"]
    (bad,) = es["sessions"]
    (mismatch,) = bad["mismatches"]
    assert (mismatch["stat_type"], mismatch["ledger"], mismatch["raw"]) == (9, 1, 0)
    assert runner.invoke(app, ARGS).exit_code == 3


def test_more_traded_than_cleared_is_over_and_exits_3(seeded: str) -> None:
    _agreeing(seeded, {EXACT: (100, 101)})
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 3, result.output
    assert "over" in result.stdout and "traded 101, cleared 100" in result.stdout


def test_cleared_volume_with_nothing_traded_is_no_trades_and_exits_3(
    seeded: str,
) -> None:
    _agreeing(seeded, {EXACT: (100, 0)})
    result = runner.invoke(app, [*ARGS, "--json"])
    assert result.exit_code == 3, result.output
    (es,) = json.loads(result.stdout)["products"]
    assert es["mismatched"] is True
    assert es["volume_checks"][0]["verdict"] == "no_trades"
    assert all(not s["mismatches"] for s in es["sessions"])  # counts agree


def test_an_under_volume_alone_does_not_fail_the_run(seeded: str) -> None:
    _agreeing(seeded, {EXACT: (100, 40)})
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 0, result.output
    assert "volume tick_tier: under 1" in result.stdout


def test_a_schema_the_entry_does_not_want_is_refused(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        universe, "TICK_UNIVERSE", (replace(WITH_STATISTICS, history=()),)
    )
    result = runner.invoke(app, ARGS)
    assert result.exit_code == 1, result.output
    assert "does not want statistics" in result.output
