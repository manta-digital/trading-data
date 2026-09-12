"""Unit tests: ``mt data status`` summarises by default (slice 922, Task 5.4).

The table used to be what an operator got for asking the simplest question.
Twelve thousand rows do not answer "is the data current"; the source
freshness block and the health footer do, in a screenful. So the summary
became the default and the table moved behind ``--detail``.

The rule that decides between them is stated once and tested here: **asking
for a filter is asking for rows**. Any of ``--symbol``, ``--health``,
``--daily``, ``--minute``, ``--all``, ``--json`` or ``--detail`` prints the
table, because each of them is a request about particular rows.

The database layer is mocked, because what is under test is which output the
operator gets, not what the queries return.
"""

from __future__ import annotations

import contextlib
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.overview import SourceFreshness
from manta_trading.cli.rendering.status_table import HealthStatus, StatusRow
from manta_trading.data.maintenance.status_coverage import CoverageFreshness
from manta_trading.data.quality.fetch_status import FetchStatus

runner = CliRunner()

_NOW = datetime(2026, 9, 12, 16, 10, tzinfo=UTC)

_GAP_COUNTS = {
    ("minute", FetchStatus.UNKNOWN.value): 892_063,
    ("minute", FetchStatus.PROVIDER_HOLE.value): 500_716,
    ("daily", FetchStatus.RETRY_EXHAUSTED.value): 35,
}


def _row(symbol: str = "SPY", granularity: str = "daily") -> StatusRow:
    return StatusRow(
        symbol=symbol,
        granularity=granularity,
        health=HealthStatus.OK,
        bars_stored=1000,
        first_bar_ts=None,
        last_bar_ts=None,
        gap_count=0,
        last_attempt_ts=None,
        last_attempt_outcome="SUCCESS",
        target_end_ts=None,
        effective_start=None,
    )


@contextlib.contextmanager
def _mocked(rows: list[StatusRow] | None = None):
    from manta_trading.data.maintenance.auto_extend import AutoExtendResult

    rows = [_row()] if rows is None else rows
    settings = MagicMock()
    settings.timescale_db_url = "postgresql://ts/db"
    freshness = CoverageFreshness(verdicts=())
    sources = [
        SourceFreshness("minute bars", _NOW - timedelta(hours=20)),
        SourceFreshness("daily bars", None),
    ]

    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("psycopg.connect"),
        patch(
            "manta_trading.data.maintenance.auto_extend"
            ".maybe_extend_trading_sessions",
            return_value=AutoExtendResult(triggered=False, error=None),
        ),
        patch(
            "manta_trading.data.maintenance.status_queries"
            ".fetch_status_rows_with_freshness",
            return_value=(rows, freshness),
        ),
        patch(
            "manta_trading.data.maintenance.status_queries"
            ".fetch_all_health_counts_with_freshness",
            return_value=({"OK": len(rows), "GAPS": 2, "STALE": 1, "FAILED": 0},
                          freshness),
        ),
        patch(
            "manta_trading.data.maintenance.status_queries.fetch_symbol_gaps",
            return_value=[],
        ),
        patch(
            "manta_trading.data.maintenance.status_queries"
            ".fetch_gap_status_counts",
            return_value=dict(_GAP_COUNTS),
        ),
        patch(
            "manta_trading.cli.commands.overview.read_source_freshness",
            return_value=sources,
        ),
    ):
        yield


def _run(*args: str):
    with _mocked():
        return runner.invoke(app, ["data", "status", *args])


class TestTheDefaultIsTheSummary:
    def test_it_prints_the_sources_block(self) -> None:
        result = _run()
        assert result.exit_code == 0, result.output
        assert "SOURCES" in result.output
        assert "minute bars" in result.output

    def test_it_prints_the_health_footer(self) -> None:
        result = _run()
        assert "OK: 1" in result.output
        assert "GAPS: 2" in result.output

    def test_it_prints_the_gap_breakdown(self) -> None:
        result = _run()
        assert "still asking" in result.output

    def test_it_does_not_print_the_table(self) -> None:
        """The whole point: no scrolling past rows to answer the question."""
        assert "Data Status" not in _run().output

    def test_an_empty_source_reads_none_not_a_blank(self) -> None:
        assert "none" in _run().output


class TestAskingForRowsPrintsTheTable:
    """Each of these is a request about particular rows."""

    @pytest.mark.parametrize(
        "args",
        [
            ["--detail"],
            ["--all"],
            ["--daily"],
            ["--minute"],
            ["--health", "GAPS"],
            ["--symbol", "SPY"],
        ],
        ids=["detail", "all", "daily", "minute", "health", "symbol"],
    )
    def test_the_table_is_printed(self, args: list[str]) -> None:
        result = _run(*args)
        assert result.exit_code == 0, result.output
        assert "SOURCES" not in result.output

    def test_detail_prints_the_table(self) -> None:
        assert "Data Status" in _run("--detail").output

    def test_json_is_the_report_not_the_summary(self) -> None:
        with _mocked():
            result = runner.invoke(app, ["data", "status", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["scope"] == "all"
        assert "gap_status_counts" in payload

    def test_the_new_footer_counts_reach_json(self) -> None:
        with _mocked():
            result = runner.invoke(app, ["data", "status", "--json"])
        payload = json.loads(result.output)
        assert payload["gap_status_counts"]["minute"]["UNKNOWN"] == 892_063


class TestTheDetailPathKeepsItsFooter:
    def test_the_gap_breakdown_appears_under_the_table_too(self) -> None:
        assert "still asking" in _run("--detail").output

    def test_the_health_counts_appear(self) -> None:
        assert "OK: 1" in _run("--detail").output


class TestHelp:
    def test_detail_is_documented(self) -> None:
        with _mocked():
            result = runner.invoke(app, ["data", "status", "--help"])
        assert "--detail" in result.output

    def test_the_help_says_what_the_default_does(self) -> None:
        with _mocked():
            result = runner.invoke(app, ["data", "status", "--help"])
        assert "Summarise" in result.output or "summar" in result.output.lower()
