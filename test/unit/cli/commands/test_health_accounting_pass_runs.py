"""Unit tests: health and accounting record themselves (slice 922, Task 2.8/2.9).

Writers four and five. Both are short-lived commands rather than daemon
cycles, so what matters is different from the runner's case:

- **The verdict is the detail, not the outcome** (Decision 2). A health run
  that answered "UNHEALTHY" did its job and records COMPLETE; only a run that
  could not answer at all records FAILED. Recording an unhealthy verdict as a
  failed pass would make the overview's PASSES block say the check is broken
  when the check is working and the data is not.
- **The accounting detail is the summary line**, because the overview prints
  it verbatim as the universe line (Decision 9).
- **Exit codes are unchanged** by recording, in every path.
- A command whose database is unreachable still runs and reports; it just
  records nothing.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.accounting import EXIT_ACCOUNTING_OK
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    EXIT_UNHEALTHY,
    HealthCheck,
    render,
    verdict_line,
)
from manta_trading.data.acquisition.pass_runs import PassKind, PassRunOutcome
from manta_trading.data.gaps.minute_accounting import MinuteAccountingRow

runner = CliRunner()


class FakeRecorder:
    def __init__(self) -> None:
        self.opened: list[PassKind] = []
        self.closed: list[tuple[Any, PassRunOutcome, int | None, str | None]] = []

    def open(self, kind: PassKind, *, walk_anchor_at=None):
        self.opened.append(kind)
        return uuid.UUID(int=11)

    def progress(self, run_id, *, phase=None, done=None, total=None) -> None:
        pass

    def close(self, run_id, *, outcome, exit_code=None, detail=None) -> None:
        self.closed.append((run_id, outcome, exit_code, detail))


def _settings(**overrides: Any) -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = "postgresql://ts/db"
    s.eodhd_api_key = "k"
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


@contextlib.contextmanager
def _recorder_patched(module: str) -> Iterator[FakeRecorder]:
    recorder = FakeRecorder()

    @contextlib.contextmanager
    def _factory(_settings: object) -> Iterator[FakeRecorder]:
        yield recorder

    with patch(f"manta_trading.cli.commands.{module}.pass_run_recorder", _factory):
        yield recorder


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def _run_health(
    settings: MagicMock,
    checks: list[HealthCheck] | None = None,
    *,
    gather_raises: BaseException | None = None,
    json_output: bool = False,
) -> tuple[Any, FakeRecorder]:
    args = ["data", "health"] + (["--json"] if json_output else [])
    with _recorder_patched("health") as recorder:
        with (
            patch("manta_trading.cli.app.Settings", return_value=settings),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.health.psycopg.connect"),
            patch(
                "manta_trading.cli.commands.health.gather",
                side_effect=gather_raises,
                return_value=checks,
            ),
        ):
            result = runner.invoke(app, args)
    return result, recorder


class TestVerdictLine:
    """The verdict the operator reads and the one recorded are one function."""

    def test_all_passing_is_healthy(self) -> None:
        assert verdict_line([HealthCheck("a", True, "x")]) == "healthy"

    def test_failures_are_counted(self) -> None:
        checks = [HealthCheck("a", True, "x"), HealthCheck("b", False, "y")]
        assert verdict_line(checks) == "UNHEALTHY: 1 of 2 checks failing"

    def test_render_ends_with_the_same_line(self) -> None:
        checks = [HealthCheck("a", True, "x"), HealthCheck("b", False, "y")]
        assert render(checks).splitlines()[-1] == verdict_line(checks)


class TestHealthWriter:
    def test_a_healthy_run_records_complete_with_the_verdict(self) -> None:
        result, rec = _run_health(
            _settings(), [HealthCheck("minute data", True, "fresh")]
        )
        assert result.exit_code == EXIT_HEALTHY
        assert rec.opened == [PassKind.HEALTH]
        assert len(rec.closed) == 1
        run_id, outcome, exit_code, detail = rec.closed[0]
        assert run_id == uuid.UUID(int=11)
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == EXIT_HEALTHY
        assert detail == "healthy"

    def test_an_unhealthy_run_is_complete_not_failed(self) -> None:
        """Decision 2: the verdict is the detail. The check worked."""
        result, rec = _run_health(
            _settings(),
            [
                HealthCheck("minute data", False, "stale"),
                HealthCheck("daily data", True, "fresh"),
            ],
        )
        assert result.exit_code == EXIT_UNHEALTHY
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == EXIT_UNHEALTHY
        assert detail == "UNHEALTHY: 1 of 2 checks failing"

    def test_a_run_that_could_not_answer_records_failed(self) -> None:
        result, rec = _run_health(
            _settings(),
            gather_raises=psycopg.OperationalError("connection refused"),
        )
        assert result.exit_code == EXIT_UNAVAILABLE
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert exit_code == EXIT_UNAVAILABLE
        assert detail is not None and "connection refused" in detail

    def test_a_statement_timeout_records_failed(self) -> None:
        """The session-mass timeout: exit 2, because nothing was measured."""
        result, rec = _run_health(
            _settings(),
            gather_raises=psycopg.errors.QueryCanceled("statement timeout"),
        )
        assert result.exit_code == EXIT_UNAVAILABLE
        assert rec.closed[0][1] is PassRunOutcome.FAILED

    def test_json_output_records_the_same_row(self) -> None:
        result, rec = _run_health(
            _settings(), [HealthCheck("a", True, "x")], json_output=True
        )
        assert result.exit_code == EXIT_HEALTHY
        assert rec.closed[0][3] == "healthy"

    def test_no_database_url_records_nothing_and_still_reports(self) -> None:
        result, rec = _run_health(_settings(timescale_db_url=None))
        assert result.exit_code == EXIT_UNAVAILABLE
        assert rec.opened == []
        assert rec.closed == []


# ---------------------------------------------------------------------------
# accounting
# ---------------------------------------------------------------------------


def _row(**overrides: int) -> MinuteAccountingRow:
    values: dict[str, Any] = {
        "year": None,
        "expected": 1_000_000,
        "covered": 900_000,
        "no_trade": 50_000,
        "hole": 20_000,
        "exhausted": 10_000,
        "unknown": 15_000,
        "untracked": 5_000,
    }
    values.update(overrides)
    return MinuteAccountingRow(**values)


def _run_accounting(
    settings: MagicMock,
    rows: list[MinuteAccountingRow] | None = None,
    *,
    compute_raises: BaseException | None = None,
) -> tuple[Any, FakeRecorder]:
    with _recorder_patched("accounting") as recorder:
        with (
            patch("manta_trading.cli.app.Settings", return_value=settings),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.accounting.psycopg.connect"),
            patch(
                "manta_trading.cli.commands.accounting.compute_minute_accounting",
                side_effect=compute_raises,
                return_value=rows,
            ),
        ):
            result = runner.invoke(app, ["data", "accounting"])
    return result, recorder


class TestAccountingWriter:
    def test_it_records_the_summary_line_as_the_detail(self) -> None:
        """The overview prints this detail verbatim as its universe line."""
        result, rec = _run_accounting(_settings(), [_row()])
        assert result.exit_code == EXIT_ACCOUNTING_OK
        assert rec.opened == [PassKind.ACCOUNTING]
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.COMPLETE
        assert exit_code == EXIT_ACCOUNTING_OK
        assert detail is not None
        assert detail.startswith("minute universe: 900,000/1,000,000")
        assert "50,000 fillable" in detail

    def test_the_recorded_detail_equals_the_printed_line(self) -> None:
        result, rec = _run_accounting(_settings(), [_row()])
        assert rec.closed[0][3] in result.output

    def test_an_unreachable_database_records_failed(self) -> None:
        result, rec = _run_accounting(
            _settings(), compute_raises=psycopg.OperationalError("no route to host")
        )
        assert result.exit_code == EXIT_UNAVAILABLE
        _, outcome, exit_code, detail = rec.closed[0]
        assert outcome is PassRunOutcome.FAILED
        assert exit_code == EXIT_UNAVAILABLE
        assert detail is not None and "no route to host" in detail

    def test_no_database_url_records_nothing(self) -> None:
        result, rec = _run_accounting(_settings(timescale_db_url=None))
        assert result.exit_code == EXIT_UNAVAILABLE
        assert rec.opened == []

    def test_an_empty_calendar_still_records(self) -> None:
        result, rec = _run_accounting(_settings(), [])
        assert result.exit_code == EXIT_ACCOUNTING_OK
        assert rec.closed[0][3] == "minute accounting: no calendar"


class TestSharedFactory:
    """Both commands build their recorder through the one helper."""

    @pytest.mark.parametrize("module", ["health", "accounting"])
    def test_the_command_imports_the_shared_factory(self, module: str) -> None:
        import importlib

        mod = importlib.import_module(f"manta_trading.cli.commands.{module}")
        from manta_trading.cli.commands._pass_run import pass_run_recorder

        assert mod.pass_run_recorder is pass_run_recorder
