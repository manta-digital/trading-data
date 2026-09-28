"""`mt data migrate` and `mt data init` route by track database (slice 923).

Settings are real ``Settings(_env_file=None)`` so a developer's ``.env`` cannot
supply a URL the test means to leave unset. The DB factory is stubbed: it
records the ``conninfo`` it received and opens nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from psycopg_pool import PoolTimeout
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.config import Settings
from manta_trading.market.schema.databases import Database, LedgerMisrouteError
from manta_trading.market.schema.migrations import TRACKS

PRIMARY_APP = "postgresql://primary-app.invalid/trading"
PRIMARY_MAINT = "postgresql://primary-maint.invalid/trading"
TICK_APP = "postgresql://tick-app.invalid/tick"
TICK_MAINT = "postgresql://tick-maint.invalid/tick"

_ALL = {
    "timescale_db_url": PRIMARY_APP,
    "timescale_maintenance_url": PRIMARY_MAINT,
    "tick_db_url": TICK_APP,
    "tick_maintenance_url": TICK_MAINT,
}

runner = CliRunner()


def _settings(**overrides: str | None) -> Settings:
    return Settings(_env_file=None, **{**_ALL, **overrides})  # type: ignore[arg-type]


def _mock_db() -> MagicMock:
    db = MagicMock()
    db.apply_schema_migrations.return_value = []
    db.list_migration_state.return_value = {"applied": [], "pending": []}
    return db


@contextmanager
def _invoke_env(settings: Settings, db: MagicMock) -> Iterator[list[str | None]]:
    """Patch the app's Settings and the DB factory; yield the conninfos seen."""
    seen: list[str | None] = []

    def _factory(ctx: object, conninfo: str | None = None) -> MagicMock:
        seen.append(conninfo)
        return db

    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch(
            "manta_trading.cli.commands.data._create_timescale_db",
            side_effect=_factory,
        ),
    ):
        yield seen


def _run(args: list[str], settings: Settings, db: MagicMock | None = None):
    db = db or _mock_db()
    with _invoke_env(settings, db) as seen:
        result = runner.invoke(app, ["data", *args])
    return result, seen, db


@pytest.mark.parametrize(
    ("args", "expected_url"),
    [
        pytest.param(
            ["migrate", "apply", "--track", "tick"], TICK_MAINT, id="apply-tick"
        ),
        pytest.param(
            ["migrate", "apply", "--track", "minute"], PRIMARY_MAINT, id="apply-minute"
        ),
        pytest.param(["migrate", "apply"], PRIMARY_MAINT, id="apply-default"),
        pytest.param(
            ["migrate", "status", "--track", "tick"], TICK_APP, id="status-tick"
        ),
        pytest.param(["migrate", "status"], PRIMARY_APP, id="status-default"),
        pytest.param(["init", "--database", "tick"], TICK_MAINT, id="init-tick"),
        pytest.param(
            ["init", "--database", "tick", "--validate-only"],
            TICK_APP,
            id="init-tick-validate",
        ),
        pytest.param(["init"], PRIMARY_MAINT, id="init-default"),
        pytest.param(["init", "--validate-only"], PRIMARY_APP, id="init-validate"),
    ],
)
def test_command_receives_its_track_database_url(
    args: list[str], expected_url: str
) -> None:
    result, seen, _ = _run(args, _settings())
    assert result.exit_code == 0, result.output
    assert seen == [expected_url]


@pytest.mark.parametrize(
    ("args", "track"),
    [
        (["migrate", "apply", "--track", "tick"], "tick"),
        (["init", "--database", "tick"], "tick"),
        (["migrate", "apply", "--track", "kalshi"], "kalshi"),
    ],
)
def test_apply_paths_guard_then_apply_the_track(args: list[str], track: str) -> None:
    result, _, db = _run(args, _settings())
    assert result.exit_code == 0, result.output
    db.check_ledger_belongs.assert_called_once()
    assert db.check_ledger_belongs.call_args.args[1] == track
    db.apply_schema_migrations.assert_called_once_with(TRACKS[track])


@pytest.mark.parametrize(
    "args",
    [["migrate", "apply", "--track", "tick"], ["init", "--database", "tick"]],
)
def test_tick_maintenance_unset_never_falls_back(args: list[str]) -> None:
    """Functional Requirement 3: all three other URLs set."""
    result, seen, _ = _run(args, _settings(tick_maintenance_url=None))
    assert result.exit_code == 1
    assert "MT_TICK_MAINTENANCE_URL not configured" in result.output
    assert seen == []


@pytest.mark.parametrize(
    "args",
    [
        ["migrate", "status", "--track", "tick"],
        ["init", "--database", "tick", "--validate-only"],
    ],
)
def test_tick_application_unset_never_reaches_primary(args: list[str]) -> None:
    """Functional Requirement 2: MT_TICK_DB_URL unset, primary set."""
    result, seen, _ = _run(args, _settings(tick_db_url=None))
    assert result.exit_code == 1
    assert "MT_TICK_DB_URL not configured" in result.output
    assert seen == []


def test_status_tick_unconfigured_json_names_variable() -> None:
    result, _, _ = _run(
        ["migrate", "status", "--track", "tick", "--json"], _settings(tick_db_url=None)
    )
    assert result.exit_code == 1
    assert '"error": "MT_TICK_DB_URL not configured"' in result.output
    assert '"connected": false' in result.output


@pytest.mark.parametrize(
    "error",
    [
        PoolTimeout("couldn't get a connection after 30.00 sec"),
        psycopg.OperationalError("refused"),
    ],
    ids=["pool-timeout", "operational-error"],
)
@pytest.mark.parametrize(
    "args",
    [["migrate", "apply", "--track", "tick"], ["init", "--database", "tick"]],
)
def test_connection_failure_is_one_line_and_applies_nothing(
    args: list[str], error: Exception
) -> None:
    db = _mock_db()
    db.check_ledger_belongs.side_effect = error
    result, _, _ = _run(args, _settings(), db)
    assert result.exit_code == 1
    assert (
        "could not connect to the tick database (MT_TICK_MAINTENANCE_URL)"
        in result.output
    )
    assert "Traceback" not in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    db.apply_schema_migrations.assert_not_called()
    db.close.assert_called_once()


def test_misroute_refusal_exits_without_applying() -> None:
    db = _mock_db()
    db.check_ledger_belongs.side_effect = LedgerMisrouteError(
        Database.TICK, "tick", [TRACKS["minute"][1]["id"]], "MT_TICK_MAINTENANCE_URL"
    )
    result, _, _ = _run(["migrate", "apply", "--track", "tick"], _settings(), db)
    assert result.exit_code == 1
    assert "refusing:" in result.output
    assert "MT_TICK_MAINTENANCE_URL" in result.output
    db.apply_schema_migrations.assert_not_called()


def test_track_choices_include_tick() -> None:
    result, _, _ = _run(["migrate", "apply", "--help"], _settings())
    assert "tick" in result.output


@pytest.mark.parametrize("value", ["primary", "tick"])
def test_database_choices_accepted(value: str) -> None:
    result, _, _ = _run(["init", "--database", value], _settings())
    assert result.exit_code == 0, result.output


def test_database_rejects_unknown_choice() -> None:
    result, seen, _ = _run(["init", "--database", "market"], _settings())
    assert result.exit_code != 0
    assert seen == []
    assert {d.value for d in Database} == {"primary", "tick"}
