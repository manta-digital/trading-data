"""Unit tests: the ``mt data overview`` command itself (slice 922, Task 4.4).

The build and render are covered next door; this file covers the command's
contract with the operator and with systemd:

- ``--json`` is the only option, by design. An overview with filters would be
  a query tool, and the point is that one screen answers the question.
- exit 0 whenever the database answered, exit 2 when it did not. A credit
  endpoint that will not answer is a line on the screen, not a failure: the
  rest of the screen is still true.
- the text output is not run through Rich, which would rewrap the aligned
  columns to the terminal width.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import psycopg
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import EXIT_UNAVAILABLE
from manta_trading.cli.commands.overview import OverviewFacts, SourceFreshness
from manta_trading.data.acquisition.pass_runs import PassKind

runner = CliRunner()

_NOW = __import__("datetime").datetime(
    2026, 9, 12, 16, 10, tzinfo=__import__("datetime").timezone.utc
)


def _settings(**overrides):
    settings = MagicMock()
    settings.timescale_db_url = "postgresql://ts/db"
    settings.eodhd_api_key = "k"
    settings.minute_firing_days = (5,)
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _facts() -> OverviewFacts:
    facts = OverviewFacts(_NOW, "manta9000", minute_firing_days=(5,))
    facts.open_runs = {kind: [] for kind in PassKind}
    facts.latest_ended = {kind: None for kind in PassKind}
    facts.sources = [SourceFreshness("minute bars", None)]
    facts.credits_error = "unavailable (no key)"
    return facts


def _invoke(args: list[str], settings=None, *, gather_raises=None):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings or _settings()),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.overview.psycopg.connect"),
        patch(
            "manta_trading.cli.commands.overview.gather",
            side_effect=gather_raises,
            return_value=_facts(),
        ),
    ):
        return runner.invoke(app, ["data", "overview", *args])


class TestOptions:
    def test_json_is_the_only_option(self) -> None:
        result = _invoke(["--help"])
        assert result.exit_code == 0
        assert "--json" in result.output
        for absent in ("--symbol", "--all", "--detail", "--minute", "--daily"):
            assert absent not in result.output

    def test_it_is_registered_under_data(self) -> None:
        with (
            patch("manta_trading.cli.app.Settings", return_value=_settings()),
            patch("manta_trading.cli.app.setup_logging"),
        ):
            result = runner.invoke(app, ["data", "--help"])
        assert "overview" in result.output


class TestExitCodes:
    def test_a_database_that_answered_exits_zero(self) -> None:
        assert _invoke([]).exit_code == 0

    def test_an_unreachable_database_exits_unavailable(self) -> None:
        result = _invoke(
            [], gather_raises=psycopg.OperationalError("connection refused")
        )
        assert result.exit_code == EXIT_UNAVAILABLE
        assert "could not run" in result.output

    def test_a_statement_timeout_exits_unavailable(self) -> None:
        result = _invoke(
            [], gather_raises=psycopg.errors.QueryCanceled("statement timeout")
        )
        assert result.exit_code == EXIT_UNAVAILABLE

    def test_no_database_url_exits_unavailable(self) -> None:
        result = _invoke([], _settings(timescale_db_url=None))
        assert result.exit_code == EXIT_UNAVAILABLE
        assert "MT_TIMESCALE_DB_URL" in result.output

    def test_an_unreachable_credit_endpoint_still_exits_zero(self) -> None:
        """The rest of the screen is still true."""
        result = _invoke([])
        assert result.exit_code == 0
        assert "unavailable" in result.output


class TestOutput:
    def test_the_text_screen_carries_every_block(self) -> None:
        result = _invoke([])
        for block in ("PASSES", "SOURCES", "EODHD credits", "minute universe"):
            assert block in result.output

    def test_the_columns_are_not_rewrapped(self) -> None:
        """Rich would rewrap to the terminal width and break the alignment."""
        result = _invoke([])
        header = next(
            line for line in result.output.splitlines() if line.startswith("PASSES")
        )
        assert "cadence" in header and "last run" in header

    def test_json_output_parses(self) -> None:
        result = _invoke(["--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert {"now", "passes", "sources", "credits", "universe"} <= set(payload)

    def test_json_output_is_not_the_text_screen(self) -> None:
        result = _invoke(["--json"])
        assert "PASSES" not in result.output
