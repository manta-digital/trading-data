"""Unit tests for mt data instruments populate-delisted-dates (slice 159, T6)."""

from __future__ import annotations

import contextlib
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.data.universe.populate_delisted_dates import PopulateDelistedDatesReport

runner = CliRunner()

CMD = ["data", "instruments", "populate-delisted-dates"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(
    *,
    timescale_url: str | None = "postgresql://ts/db",
    eodhd_api_key: str | None = "test-key",
) -> MagicMock:
    s = MagicMock()
    s.timescale_db_url = timescale_url
    s.eodhd_api_key = eodhd_api_key
    s.daily_provider.value = "eodhd"
    return s


@contextlib.contextmanager
def _patch_app(settings: MagicMock):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
    ):
        yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_help_exits_zero() -> None:
    with _patch_app(_settings()):
        result = runner.invoke(app, [*CMD, "--help"])
    assert result.exit_code == 0
    assert "--dry-run" in result.output


def test_missing_env_exits_error() -> None:
    settings = _settings(timescale_url=None)
    with _patch_app(settings):
        result = runner.invoke(app, CMD)
    assert result.exit_code != 0
    assert "MT_TIMESCALE_DB_URL" in result.output


def test_dry_run_flag_passed_through() -> None:
    mock_report = PopulateDelistedDatesReport(
        total=5, updated=0, skipped_empty=0, error_count=0
    )
    settings = _settings()

    with (
        _patch_app(settings),
        patch(
            "manta_trading.data.universe.populate_delisted_dates.populate_delisted_dates",
            return_value=mock_report,
        ),
        patch("psycopg.connect"),
        patch("httpx.Client"),
        patch("manta_trading.data.acquisition.daemon.runner.QUOTA_BUCKET_VAR"),
        patch("manta_trading.data.acquisition.quota.QuotaBucket"),
    ):
        result = runner.invoke(app, [*CMD, "--dry-run"])

    assert result.exit_code == 0
    output_lower = result.output.lower()
    assert "dry run" in output_lower


def test_error_count_nonzero_exits_one() -> None:
    mock_report = PopulateDelistedDatesReport(
        total=3, updated=1, skipped_empty=0, error_count=2
    )
    settings = _settings()

    with (
        _patch_app(settings),
        patch(
            "manta_trading.data.universe.populate_delisted_dates.populate_delisted_dates",
            return_value=mock_report,
        ),
        patch("psycopg.connect"),
        patch("httpx.Client"),
        patch("manta_trading.data.acquisition.daemon.runner.QUOTA_BUCKET_VAR"),
        patch("manta_trading.data.acquisition.quota.QuotaBucket"),
    ):
        result = runner.invoke(app, CMD)

    assert result.exit_code == 1
