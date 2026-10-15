"""The tick rows of ``mt data health`` (slice 234 TD5, TD6), tick read stubbed."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import psycopg
import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    EXIT_UNHEALTHY,
    CheckSeverity,
    HealthCheck,
    verdict_line,
)
from manta_trading.cli.commands.health_tick import (
    DATABASE_CHECK,
    RETENTION_CHECK,
    check_retention,
    retention_window,
)
from manta_trading.data.tick.constants import (
    TICK_RETENTION_WARN_DAYS,
    TICK_RUN_UNIT_COMMAND,
)
from manta_trading.data.tick.line_state import TickLineState, TickUnavailable
from manta_trading.data.tick.manifest_request_reads import NearDeadline

#: Rich fixes its width at the first print; pin it so lines do not wrap.
runner = CliRunner(env={"COLUMNS": "200"})
PRIMARY = [HealthCheck("minute data", True, "fresh")]
NON_OK = [s for s in TickLineState if s is not TickLineState.OK]
DEADLINE = datetime(2026, 10, 17, 9, 30, tzinfo=UTC)


def _settings() -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = "postgresql://ts/db"
    return s


def _invoke(tick: Any, *args: str, connect: Any = None) -> Any:
    with (
        patch("manta_trading.cli.app.Settings", return_value=_settings()),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.health.pass_run_recorder", _no_recorder),
        patch(
            "manta_trading.cli.commands.health.psycopg.connect",
            connect if connect is not None else MagicMock(),
        ),
        patch("manta_trading.cli.commands.health.gather", return_value=PRIMARY),
        patch("manta_trading.cli.tick_composed.read_line", return_value=tick),
    ):
        return runner.invoke(app, ["data", "health", *args])


class _no_recorder:
    def __init__(self, _settings: Any) -> None: ...

    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> Literal[False]:
        return False


@pytest.mark.parametrize("state", NON_OK)
def test_a_non_ok_tick_database_is_informational(state: TickLineState) -> None:
    result = _invoke(TickUnavailable(state, "why"))
    assert result.exit_code == EXIT_HEALTHY, result.output
    assert f"INFO {DATABASE_CHECK}" in result.output
    assert "OK   minute data" in result.output
    assert RETENTION_CHECK not in result.output
    assert result.output.splitlines()[-1] == "healthy"


def test_a_reachable_database_with_nothing_near_passes() -> None:
    result = _invoke([])
    assert result.exit_code == EXIT_HEALTHY
    assert f"OK   {DATABASE_CHECK}" in result.output
    assert f"OK   {RETENTION_CHECK}" in result.output


def test_a_unit_near_its_deadline_fails_health() -> None:
    result = _invoke([NearDeadline("GLBX-1", DEADLINE, 2)])
    assert result.exit_code == EXIT_UNHEALTHY
    line = next(x for x in result.output.splitlines() if RETENTION_CHECK in x)
    assert line.startswith("FAIL")
    assert "GLBX-1" in result.output
    assert "2026-10-17 09:30 UTC" in result.output
    assert TICK_RUN_UNIT_COMMAND in result.output


def test_a_primary_outage_exits_2_with_the_tick_lines_printed() -> None:
    down = MagicMock(side_effect=psycopg.OperationalError("primary down"))
    result = _invoke(TickUnavailable(TickLineState.UNREACHABLE, "x"), connect=down)
    assert result.exit_code == EXIT_UNAVAILABLE
    assert f"INFO {DATABASE_CHECK}" in result.output
    assert "primary down" in result.output


def test_json_marks_the_informational_check_and_does_not_count_it() -> None:
    result = _invoke(TickUnavailable(TickLineState.NOT_CONFIGURED, "unset"), "--json")
    payload = json.loads(result.output)
    assert payload["healthy"] is True
    tick = next(c for c in payload["checks"] if c["name"] == DATABASE_CHECK)
    assert tick["severity"] == CheckSeverity.INFORMATIONAL.value
    assert tick["ok"] is False
    minute = next(c for c in payload["checks"] if c["name"] == "minute data")
    assert minute["severity"] == CheckSeverity.VERDICT.value


def test_verdict_line_ignores_informational_checks() -> None:
    info = HealthCheck(DATABASE_CHECK, False, "down", CheckSeverity.INFORMATIONAL)
    failing = HealthCheck("cagg x", False, "lag")
    assert verdict_line([*PRIMARY, info]) == "healthy"
    assert verdict_line([*PRIMARY, failing, info]) == "UNHEALTHY: 1 of 2 checks failing"


def test_the_window_is_the_fixed_constant_while_tick_is_manual() -> None:
    assert retention_window() == timedelta(days=TICK_RETENTION_WARN_DAYS)


def test_retention_names_every_job_and_converts_to_utc() -> None:
    local = DEADLINE.astimezone(ZoneInfo("America/Chicago"))
    check = check_retention(
        [NearDeadline("A", local, 1), NearDeadline(None, DEADLINE, 3)],
        timedelta(days=7),
    )
    assert not check.ok
    assert "job A (1 units) by 2026-10-17 09:30 UTC" in check.detail
    assert "job (none) (3 units)" in check.detail
