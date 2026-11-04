"""``mt data tick active --rule n.0`` end to end (slice 303, D9, D10).

The September 2024 roll and its statistics are seeded as in
``test_tick_active_open_interest``; the CLI is invoked as an operator would.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from tick_support.roll_seed import (
    ESH5,
    ESU4,
    ESZ4,
    SEPTEMBER,
    SEPTEMBER_CROSS,
    seed_september_roll,
    seed_tier_days,
)
from tick_support.rows import insert_statistics
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_ENV_PREFIX, TickSchema
from manta_trading.data.tick.statistics_constants import StatType

runner = CliRunner(env={"COLUMNS": "200"})
ACTIVE = ["data", "tick", "active"]
SEPTEMBER_RANGE = ["--product", "ES", "--start", "2024-09-05", "--end", "2024-09-28"]
CALENDAR_DAYS = [date(2024, 9, 1) + timedelta(days=n) for n in range(32)]


def _interest(day: date, contract: Any) -> int:
    crossed = day >= SEPTEMBER_CROSS
    if contract is ESU4:
        return 4_000 if crossed else 6_000
    return (7_000 if crossed else 3_000) if contract is ESZ4 else 50


@pytest.fixture
def seeded(
    migrated_tick_db: str, session_migrated_db: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    for name in list(os.environ):
        if name.upper().startswith(TICK_ENV_PREFIX) or "DATABENTO" in name.upper():
            monkeypatch.delenv(name)
    seed_september_roll(migrated_tick_db)
    units = seed_tier_days(migrated_tick_db, CALENDAR_DAYS, TickSchema.STATISTICS)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        for sequence, day in enumerate(SEPTEMBER):
            for contract in (ESU4, ESZ4, ESH5):
                insert_statistics(
                    conn,
                    units[day],
                    instrument_id=contract.instrument_id,
                    stat_type=int(StatType.OPEN_INTEREST),
                    quantity=_interest(day, contract),
                    price=None,
                    session_date=day,
                    sequence=sequence,
                )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        tick_db_url=migrated_tick_db,
        timescale_db_url=session_migrated_db,
    )
    with (
        patch("manta_trading.cli.app.Settings", side_effect=lambda: settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


def _invoke(*args: str) -> Any:
    return runner.invoke(app, [*ACTIVE, *args])


def test_n0_schedule_shows_the_roll_and_names_its_basis(seeded: None) -> None:
    result = _invoke(*SEPTEMBER_RANGE, "--rule", "n.0")
    assert result.exit_code == 0, result.output
    assert "2024-09-05 → 2024-09-17  ESU4 (118)  [statistics]" in result.output
    assert "2024-09-18 → 2024-09-27  ESZ4 (183748)  [statistics]" in result.output


def test_n0_json_carries_basis_family_on_answers_and_basis_on_segments(
    seeded: None,
) -> None:
    result = _invoke(*SEPTEMBER_RANGE, "--rule", "n.0", "--json")
    assert result.exit_code == 0, result.output
    (schedule,) = json.loads(result.output)["schedules"]
    assert {a["basis_family"] for a in schedule["answers"]} == {"statistics"}
    assert {s["basis"] for s in schedule["segments"]} == {"statistics"}


def test_the_latest_line_for_n0_names_what_it_ranked_on(seeded: None) -> None:
    result = _invoke("--product", "ES", "--rule", "n.0")
    assert result.exit_code == 0, result.output
    assert "inputs: open interest of session" in result.output
    assert "(statistics)" in result.output


def test_volume_and_calendar_schedules_gain_only_the_basis(seeded: None) -> None:
    volume = json.loads(_invoke(*SEPTEMBER_RANGE, "--rule", "v.0", "--json").output)
    calendar = json.loads(_invoke(*SEPTEMBER_RANGE, "--rule", "c.0", "--json").output)
    (v,) = volume["schedules"]
    (c,) = calendar["schedules"]
    assert {s["basis"] for s in v["segments"]} == {"tick_tier"}
    assert {s["basis"] for s in c["segments"]} == {None}
    assert {a["basis_family"] for a in c["answers"]} == {None}


def test_an_offset_on_n_is_refused_with_the_parsers_message(seeded: None) -> None:
    result = _invoke(*SEPTEMBER_RANGE, "--rule", "n.0+2s")
    assert result.exit_code == 1, result.output
    assert "offset suffix is allowed only on calendar" in result.output
