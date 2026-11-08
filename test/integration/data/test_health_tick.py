"""``mt data health``'s tick rows against a real tick database (234, 4.5).

The primary checks are stubbed passing (they have their own tests); the tick
read is real. Deadlines are seeded relative to the wall clock, because the
command judges against ``now``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from tick_support.rows import (
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_request,
    insert_unit,
)
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNHEALTHY,
    HealthCheck,
)
from manta_trading.config import Settings
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import cross_tier_checks, family_report
from manta_trading.data.tick import universe as universe_module
from manta_trading.data.tick.constants import (
    TICK_RUN_UNIT_COMMAND,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_reads import VolumeClass, VolumeVerdict
from manta_trading.data.tick.universe import TICK_UNIVERSE, HistoryWant

runner = CliRunner(env={"COLUMNS": "200"})
#: Nothing listens here: the closed-port stand-in for an unreachable database.
CLOSED_PORT_URL = "postgresql://nobody@127.0.0.1:1/none"
STUBBED_PRIMARY = "postgresql://stubbed/primary"


def _invoke(tick_url: str, calendar_url: str = STUBBED_PRIMARY) -> Any:
    """``calendar_url`` is the primary the futures rows read the calendar from;
    the default stub makes that read fail, as for any test not about it."""
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        timescale_db_url=calendar_url,
        tick_db_url=tick_url,
    )
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.health.pass_run_recorder") as recorder,
        # The backup rows have their own tests and read the host's environment.
        patch(
            "manta_trading.cli.commands.health_backup.gather_backup", return_value=[]
        ),
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
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed(
        migrated_tick_db,
        "HOLED",
        3,
        fetch_status=FetchStatus.PROVIDER_HOLE.value,
        failure_reason="no file delivered",
    )
    _seed(migrated_tick_db, "REOPENED", 3, reopened_at=datetime.now(UTC))
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert result.exit_code == EXIT_HEALTHY, result.output
    assert "OK   tick database" in result.output
    assert "OK   tick retention" in result.output


# -- futures family rows (slice 305 D5) ---------------------------------------

DAY = date(2024, 12, 3)
WANTS = replace(
    TICK_UNIVERSE[0],
    tier=TickSchema.TBBO,
    start=DAY,
    end=date(2024, 12, 4),
    history=(
        HistoryWant(TickSchema.OHLCV_1M, DAY, date(2024, 12, 4)),
        HistoryWant(TickSchema.STATISTICS, DAY, date(2024, 12, 5)),
    ),
)


def _seed_family(
    conn: psycopg.Connection[Any],
    schema: TickSchema,
    day: date,
    **unit: object,
) -> None:
    request = insert_request(
        conn, schema=schema.value, range_start=day, range_end=day + timedelta(days=1)
    )
    insert_unit(
        conn,
        request,
        unit_date=day,
        **({"state": UnitState.INGESTED.value, **FILE_COLUMNS} | unit),
    )


def _seed_universe(url: str, *, tick_unit: dict[str, object] | None = None) -> None:
    """Every family of ``WANTS`` loaded for session 2024-12-03 (its tick tier and
    bars from 12-02 and 12-03, statistics through 12-04), the dataset edge far
    ahead. ``tick_unit`` overrides the tick tier's 12-03 unit."""
    with psycopg.connect(url, autocommit=True) as conn:
        for day in (date(2024, 12, 2), DAY, date(2024, 12, 4)):
            insert_day_condition(conn, condition_date=day, condition="available")
            _seed_family(conn, TickSchema.STATISTICS, day)
        for day in (date(2024, 12, 2), DAY):
            _seed_family(conn, TickSchema.OHLCV_1M, day)
            overrides = tick_unit if tick_unit and day == DAY else {}
            _seed_family(conn, TickSchema.TBBO, day, **overrides)
        insert_dataset_edge(conn, available_end=datetime(2030, 1, 1, tzinfo=UTC))


@pytest.fixture
def one_product(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(universe_module, "TICK_UNIVERSE", (WANTS,))


def _row(result: Any, name: str) -> str:
    return next(x for x in result.output.splitlines() if name in x)


def test_a_failed_unit_makes_its_family_row_fail_and_health_exit_1(
    one_product: None, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed_universe(
        migrated_tick_db,
        tick_unit={
            "fetch_status": FetchStatus.FAILED_RETRYABLE.value,
            "failure_reason": "download failed",
        },
    )
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert result.exit_code == EXIT_UNHEALTHY, result.output
    assert _row(result, "futures ES tbbo").startswith("FAIL")
    assert _row(result, "futures ES ohlcv-1m").startswith("OK")


def test_an_over_verdict_makes_its_family_row_fail(
    one_product: None,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def over(
        conn: Any, shape: Any, family: SchemaFamily, product: str, sessions: Any
    ) -> tuple[VolumeVerdict, ...]:
        return (VolumeVerdict(DAY, 1, "ESZ4", family, 10, 11, VolumeClass.OVER),)

    monkeypatch.setattr(cross_tier_checks, "volume_check", over)
    _seed_universe(migrated_tick_db)
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert result.exit_code == EXIT_UNHEALTHY, result.output
    assert _row(result, "futures ES tbbo").startswith("FAIL")
    assert "traded_vs_cleared tbbo: 1 over" in _row(result, "futures ES tbbo")


def test_a_family_that_is_not_caught_up_is_informational(
    one_product: None, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _seed_universe(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = %s WHERE unit_id = ("
            " SELECT max(u.unit_id) FROM tick_archive_unit u JOIN tick_request r"
            " USING (request_id) WHERE r.schema = 'ohlcv-1m')",
            (UnitState.REQUESTED.value,),
        )
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert result.exit_code == EXIT_HEALTHY, result.output
    assert _row(result, "futures ES ohlcv-1m").startswith("INFO")
    assert _row(result, "futures ES ohlcv-1m").count("not caught up") == 1


def test_a_statement_failing_partway_is_one_counted_row_naming_the_step(
    one_product: None,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def timed_out(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.errors.QueryCanceled(
            "canceling statement due to statement timeout"
        )

    monkeypatch.setattr(family_report, "ledger_totals", timed_out)
    _seed_universe(migrated_tick_db)
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert result.exit_code == EXIT_UNHEALTHY, result.output
    assert _row(result, "futures completeness").startswith("FAIL")
    assert "ES tick_tier: records:" in _row(result, "futures completeness")
    assert result.output.count("futures ") == 1  # no per-family rows


def test_a_product_without_a_statistics_want_is_labelled_load_complete_only(
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bare = replace(WANTS, history=WANTS.history[:1])
    monkeypatch.setattr(universe_module, "TICK_UNIVERSE", (bare,))
    _seed_universe(migrated_tick_db)
    result = _invoke(migrated_tick_db, session_migrated_db)
    assert _row(result, "futures ES ohlcv-1m").endswith("load-complete only")
    assert "futures ES statistics" not in result.output
