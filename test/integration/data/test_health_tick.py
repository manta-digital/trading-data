"""``mt data health``'s tick rows against a real tick database (234, 4.5).

The primary checks are stubbed passing (they have their own tests); the tick
read is real. Deadlines are seeded relative to the wall clock, because the
command judges against ``now``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
from tick_support.rows import insert_request, insert_unit
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNHEALTHY,
    HealthCheck,
)
from manta_trading.config import Settings
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import TICK_RUN_UNIT_COMMAND, UnitState

runner = CliRunner(env={"COLUMNS": "200"})
#: Nothing listens here: the closed-port stand-in for an unreachable database.
CLOSED_PORT_URL = "postgresql://nobody@127.0.0.1:1/none"


def _invoke(tick_url: str) -> Any:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        timescale_db_url="postgresql://stubbed/primary",
        tick_db_url=tick_url,
    )
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.health.pass_run_recorder") as recorder,
        patch("manta_trading.cli.commands.health.psycopg.connect", MagicMock()),
        patch(
            "manta_trading.cli.commands.health.gather",
            return_value=[HealthCheck("minute data", True, "fresh")],
        ),
    ):
        recorder.return_value.__enter__.return_value = None
        return runner.invoke(app, ["data", "health"])


def _seed(url: str, job: str, days: float, **unit: object) -> None:
    deadline = datetime.now(UTC) + timedelta(days=days)
    with psycopg.connect(url, autocommit=True) as conn:
        request_id = insert_request(
            conn, provider_job_id=job, download_deadline=deadline
        )
        insert_unit(conn, request_id, **({"state": UnitState.SUBMITTED.value} | unit))


def test_an_unreachable_tick_database_is_informational() -> None:
    result = _invoke(CLOSED_PORT_URL)
    assert result.exit_code == EXIT_HEALTHY, result.output
    assert "INFO tick database" in result.output
    assert "unreachable" in result.output
    assert "tick retention" not in result.output


def test_a_unit_three_days_from_its_deadline_fails_retention(
    migrated_tick_db: str,
) -> None:
    _seed(migrated_tick_db, "GLBX-SOON", 3)
    result = _invoke(migrated_tick_db)
    assert result.exit_code == EXIT_UNHEALTHY, result.output
    line = next(x for x in result.output.splitlines() if "tick retention" in x)
    assert line.startswith("FAIL")
    assert "GLBX-SOON" in line
    assert " UTC" in line
    assert TICK_RUN_UNIT_COMMAND in line


def test_holed_and_reopened_units_never_fail_retention(
    migrated_tick_db: str,
) -> None:
    _seed(
        migrated_tick_db,
        "HOLED",
        3,
        fetch_status=FetchStatus.PROVIDER_HOLE.value,
        failure_reason="no file delivered",
    )
    _seed(migrated_tick_db, "REOPENED", 3, reopened_at=datetime.now(UTC))
    result = _invoke(migrated_tick_db)
    assert result.exit_code == EXIT_HEALTHY, result.output
    assert "OK   tick database" in result.output
    assert "OK   tick retention" in result.output
