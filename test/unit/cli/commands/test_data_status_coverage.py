"""Unit tests for the stale-coverage indicator on mt data status (slice 167 s6).

These tests exercise the CLI end to end with a mocked DB layer, because the
point of the slice-167 guard is that an operator *sees* the warning. A test
that only asserts the helper was called would not catch a banner that is
computed and never printed — which is the exact defect class this slice's
process journal records.
"""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.maintenance.status_coverage import CoverageFreshness
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

runner = CliRunner()


def _settings():
    settings = MagicMock()
    settings.timescale_db_url = "postgresql://ts/db"
    return settings


def _verdict(view_name: str, *, is_fresh: bool) -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=is_fresh,
        signals=() if is_fresh else (StalenessSignal.NOT_SCHEDULED,),
        lag=timedelta(0) if is_fresh else timedelta(days=4),
        threshold=timedelta(days=1),
        detail="test verdict",
    )


def _freshness(*, stale: bool) -> CoverageFreshness:
    return CoverageFreshness(
        verdicts=(
            _verdict("minute_coverage", is_fresh=not stale),
            _verdict("daily_coverage", is_fresh=True),
        )
    )


def _row(symbol: str = "SPY", granularity: str = "daily"):
    from manta_trading.cli.rendering.status_table import HealthStatus, StatusRow

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
def _mocked_status(*, stale: bool, rows=None):
    """Patch the whole status data path; yield nothing, run the CLI inside."""
    from manta_trading.data.maintenance.auto_extend import AutoExtendResult

    rows = [_row()] if rows is None else rows
    freshness = _freshness(stale=stale)
    settings = _settings()

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
            return_value=({"OK": len(rows)}, freshness),
        ),
        patch(
            "manta_trading.data.maintenance.status_queries.fetch_symbol_gaps",
            return_value=[],
        ),
    ):
        yield


class TestStaleCoverageBanner:
    def test_banner_printed_when_coverage_stale(self):
        with _mocked_status(stale=True):
            result = runner.invoke(app, ["data", "status"])
        assert result.exit_code == 0
        assert "STALE" in result.stdout
        assert "minute_coverage" in result.stdout

    def test_no_banner_when_coverage_fresh(self):
        with _mocked_status(stale=False):
            result = runner.invoke(app, ["data", "status"])
        assert result.exit_code == 0
        assert "coverage is STALE" not in result.stdout

    def test_stale_coverage_does_not_change_exit_code(self):
        """D3a: report, do not refuse. Status stays a pure reporting command."""
        with _mocked_status(stale=True):
            stale_result = runner.invoke(app, ["data", "status"])
        with _mocked_status(stale=False):
            fresh_result = runner.invoke(app, ["data", "status"])
        assert stale_result.exit_code == fresh_result.exit_code == 0

    def test_banner_precedes_the_tables(self):
        """Stale coverage understates the numbers below it, so it reads first."""
        with _mocked_status(stale=True):
            result = runner.invoke(app, ["data", "status"])
        assert result.stdout.index("STALE") < result.stdout.index("Data Status")

    def test_banner_shown_on_empty_universe(self):
        """A no-row result is when a stale verdict matters most."""
        with _mocked_status(stale=True, rows=[]):
            result = runner.invoke(app, ["data", "status"])
        assert result.exit_code == 0
        assert "STALE" in result.stdout
        assert "No instruments found" in result.stdout


class TestStaleCoverageJson:
    def test_json_carries_coverage_sibling_of_rows(self):
        with _mocked_status(stale=True):
            result = runner.invoke(app, ["data", "status", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["coverage"]["is_stale"] is True
        assert "rows" in payload

    def test_json_column_contract_unchanged(self):
        """D2/criterion 3: freshness must not appear as a per-row field."""
        with _mocked_status(stale=True):
            result = runner.invoke(app, ["data", "status", "--json"])
        payload = json.loads(result.stdout)
        for row in payload["rows"]:
            assert "coverage" not in row
            assert "is_stale" not in row

    def test_json_is_stale_false_when_fresh(self):
        with _mocked_status(stale=False):
            result = runner.invoke(app, ["data", "status", "--json"])
        payload = json.loads(result.stdout)
        assert payload["coverage"]["is_stale"] is False

    def test_json_empty_universe_carries_coverage_flag(self):
        with _mocked_status(stale=True, rows=[]):
            result = runner.invoke(app, ["data", "status", "--json"])
        payload = json.loads(result.stdout)
        assert payload["coverage_stale"] is True
