"""``mt data tick ingest``: exit codes, report and ``--json`` (slice 225, 5.3).

The run itself is replaced; ``test_tick_ingest_cli.py`` drives the real verb
against a database.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import UUID

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands import tick_store_cmds
from manta_trading.cli.commands.tick_exit import EXIT_BY_OUTCOME
from manta_trading.config import Settings
from manta_trading.data.tick.pass_contract import (
    PassResult,
    PhaseReport,
    TickOutcome,
    TickPassPhaseName,
)

#: Rich fixes its console width at the first print of the process: pin it, so
#: this module never narrows the width a later module's assertions rely on.
runner = CliRunner(env={"COLUMNS": "200"})
INGEST = ["data", "tick", "ingest"]
SUMMARY: dict[str, Any] = {
    "ingested": 1,
    "failed": 1,
    "records": 3774,
    "superseded": [],
    "skipped": {
        "awaiting_definitions": 0,
        "outranked": 0,
        "changed_during_ingest": 0,
        "not_selectable": 1,
    },
    "units": [
        {
            "unit_id": 7,
            "unit_date": "2024-09-03",
            "schema": "trades",
            "outcome": "ingested",
            "records": 3774,
            "reason": None,
            "duration_seconds": 1.5,
        },
        {
            "unit_id": 8,
            "unit_date": "2024-09-04",
            "schema": "trades",
            "outcome": "failed",
            "records": 0,
            "reason": "counts: provider 5, decoded 4, stored 4",
        },
        {"unit_id": 99, "outcome": "not_selectable", "reason": "no such unit"},
    ],
}


def _result(outcome: TickOutcome) -> PassResult:
    report = PhaseReport(TickPassPhaseName.INGEST, outcome, SUMMARY, 1500)
    started = datetime(2026, 9, 30, tzinfo=UTC)
    return PassResult(UUID(int=5), started, (report,), outcome, 1500)


@pytest.fixture(autouse=True)
def settings() -> Iterator[None]:
    with patch(
        "manta_trading.cli.app.Settings", side_effect=lambda: Settings(_env_file=None)
    ):
        yield


def _returning(result: PassResult) -> Any:
    calls: list[tuple[int, ...]] = []

    async def fake(settings: Settings, unit_ids: tuple[int, ...]) -> PassResult:
        calls.append(unit_ids)
        return result

    return patch.object(tick_store_cmds, "_ingest", fake), calls


@pytest.mark.parametrize("outcome", list(TickOutcome))
def test_the_exit_code_follows_the_outcome(outcome: TickOutcome) -> None:
    patcher, _ = _returning(_result(outcome))
    with patcher:
        result = runner.invoke(app, INGEST)
    assert result.exit_code == EXIT_BY_OUTCOME[outcome], result.output


def test_json_is_the_pass_result_with_the_exit_code() -> None:
    patcher, _ = _returning(_result(TickOutcome.PARTIAL))
    with patcher:
        result = runner.invoke(app, [*INGEST, "--json"])
    body = json.loads(result.stdout)
    assert body["exit_code"] == EXIT_BY_OUTCOME[TickOutcome.PARTIAL]
    (phase,) = body["phases"]
    assert phase["name"] == "ingest"
    assert phase["summary"]["units"][2] == {
        "unit_id": 99,
        "outcome": "not_selectable",
        "reason": "no such unit",
    }


def test_repeated_unit_ids_reach_the_run() -> None:
    patcher, calls = _returning(_result(TickOutcome.OK))
    with patcher:
        runner.invoke(app, [*INGEST, "--unit-id", "7", "--unit-id", "99"])
    assert calls == [(7, 99)]


def test_the_report_shows_units_skips_totals_and_reasons() -> None:
    patcher, _ = _returning(_result(TickOutcome.PARTIAL))
    with patcher:
        result = runner.invoke(app, INGEST)
    text = result.stdout
    assert "unit 7  2024-09-03 trades  3774 rec  → ingested  (1.5 s)" in text
    assert "counts: provider 5, decoded 4, stored 4" in text
    assert "unit 99  → not_selectable  no such unit" in text
    assert "skipped: 0 awaiting definitions, 0 outranked," in text
    assert "ingested 1 units, 3774 records; failed 1" in text
    assert "outcome partial  (exit 3)" in text
