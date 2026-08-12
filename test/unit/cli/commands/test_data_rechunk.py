"""CLI surface for `mt data rechunk --table` (slice 170 B7).

The driver itself is covered by test/unit/market/test_rechunk.py and the
integration tier; what is asserted here is the CLI contract only — which
target the command dispatches, that an unknown value is rejected before any
database work, and that the exit-code contract is identical for both targets.

``run_rechunk`` is stubbed throughout, so no test in this file opens a
connection.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.market.maintenance.rechunk import (
    PreflightError,
    RechunkError,
    RechunkResult,
    RechunkTarget,
)

APP_VAR = "MT_TIMESCALE_DB_URL"
MAINT_VAR = "MT_TIMESCALE_MAINTENANCE_URL"

_STUB_URL = "postgresql://stub@192.0.2.1:5432/trading?connect_timeout=1"

# Exit codes the command promises in its docstring.
_EXIT_OK = 0
_EXIT_PREFLIGHT_FAILED = 1
_EXIT_RECHUNK_FAILED = 2


def _result(dry_run: bool = True) -> RechunkResult:
    return RechunkResult(
        total_windows=118,
        rewritten=0,
        compressed_only=0,
        skipped_uncompressed=0,
        already_done=118,
        dry_run=dry_run,
    )


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both URLs present, so nothing fails on credential routing.

    Settings reads .env directly, so patching os.environ alone is not enough
    to make this hermetic — but both keys being *set* is all these tests need,
    and psycopg is never reached because run_rechunk is stubbed.
    """
    monkeypatch.setenv(APP_VAR, _STUB_URL)
    monkeypatch.setenv(MAINT_VAR, _STUB_URL)


@pytest.fixture
def captured_targets(monkeypatch: pytest.MonkeyPatch) -> list[RechunkTarget]:
    """Record the target each invocation dispatches; never touch a database."""
    seen: list[RechunkTarget] = []

    def _stub(conninfo: str, **kwargs: Any) -> RechunkResult:
        seen.append(kwargs["target"])
        return _result(dry_run=bool(kwargs.get("dry_run")))

    monkeypatch.setattr(
        "manta_trading.market.maintenance.rechunk.run_rechunk", _stub
    )
    return seen


class TestTargetDispatch:
    def test_default_invocation_targets_minute(
        self, credentials: None, captured_targets: list[RechunkTarget]
    ) -> None:
        """No --table must keep planning the minute table — the slice 166
        invocation is unchanged by the 170 refactor (Success Criterion 7)."""
        result = CliRunner().invoke(app, ["data", "rechunk", "--dry-run"])

        assert result.exit_code == _EXIT_OK, result.output
        assert captured_targets == [RechunkTarget.MINUTE]

    def test_table_daily_targets_daily(
        self, credentials: None, captured_targets: list[RechunkTarget]
    ) -> None:
        result = CliRunner().invoke(
            app, ["data", "rechunk", "--dry-run", "--table", "daily"]
        )

        assert result.exit_code == _EXIT_OK, result.output
        assert captured_targets == [RechunkTarget.DAILY]

    def test_table_minute_is_explicit_and_equivalent(
        self, credentials: None, captured_targets: list[RechunkTarget]
    ) -> None:
        result = CliRunner().invoke(
            app, ["data", "rechunk", "--dry-run", "--table", "minute"]
        )

        assert result.exit_code == _EXIT_OK, result.output
        assert captured_targets == [RechunkTarget.MINUTE]

    def test_invalid_table_is_rejected_without_running(
        self, credentials: None, captured_targets: list[RechunkTarget]
    ) -> None:
        """Typer validates against the enum, so a typo cannot reach the driver
        and cannot be silently coerced to the default target."""
        result = CliRunner().invoke(
            app, ["data", "rechunk", "--dry-run", "--table", "dialy"]
        )

        assert result.exit_code != _EXIT_OK
        assert captured_targets == [], "driver ran despite an invalid --table"

    def test_help_lists_both_targets(self) -> None:
        result = CliRunner().invoke(app, ["data", "rechunk", "--help"])

        assert result.exit_code == _EXIT_OK
        assert "--table" in result.output
        for choice in ("minute", "daily"):
            assert choice in result.output


class TestExitCodeContractPerTarget:
    """Both targets share the driver, so they must share its exit codes —
    an operator's runbook cannot depend on which table was rewritten."""

    @pytest.mark.parametrize(
        ("argv", "expected_target"),
        [
            ([], RechunkTarget.MINUTE),
            (["--table", "daily"], RechunkTarget.DAILY),
        ],
        ids=["minute", "daily"],
    )
    @pytest.mark.parametrize(
        ("raised", "expected_exit"),
        [
            (PreflightError("jobs still scheduled"), _EXIT_PREFLIGHT_FAILED),
            (RechunkError("window 2020-01-02 failed"), _EXIT_RECHUNK_FAILED),
        ],
        ids=["preflight", "rechunk"],
    )
    def test_driver_failure_maps_to_its_exit_code(
        self,
        credentials: None,
        monkeypatch: pytest.MonkeyPatch,
        argv: list[str],
        expected_target: RechunkTarget,
        raised: Exception,
        expected_exit: int,
    ) -> None:
        seen: list[RechunkTarget] = []

        def _stub(conninfo: str, **kwargs: Any) -> RechunkResult:
            seen.append(kwargs["target"])
            raise raised

        monkeypatch.setattr(
            "manta_trading.market.maintenance.rechunk.run_rechunk", _stub
        )

        result = CliRunner().invoke(app, ["data", "rechunk", *argv])

        assert result.exit_code == expected_exit, result.output
        assert seen == [expected_target]
