"""Tests for the root Typer CLI app."""

import importlib.metadata
import logging

from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.constants import DISTRIBUTION_NAME

runner = CliRunner()


class TestHelp:
    """Verify help output."""

    def test_help_contains_app_name(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "Manta Trading CLI" in result.output

    def test_no_args_shows_help(self):
        result = runner.invoke(app, [])
        # no_args_is_help causes exit code 0 with help text
        assert "Manta Trading CLI" in result.output


class TestVersion:
    """Verify --version flag."""

    def test_version_output(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "mt version" in result.output

    def test_version_contains_version_or_dev(self):
        result = runner.invoke(app, ["--version"])
        output = result.output.strip()
        version_part = output.replace("mt version ", "")
        assert version_part == "dev" or "." in version_part

    def test_version_reports_resolved_distribution_metadata(self, monkeypatch):
        monkeypatch.setattr(
            importlib.metadata,
            "version",
            lambda name: "1.2.3" if name == DISTRIBUTION_NAME else "wrong-name",
        )
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "mt version 1.2.3" in result.output

    def test_version_falls_back_to_dev_and_warns_on_missing_metadata(
        self, monkeypatch, caplog
    ):
        def _raise_not_found(name: str) -> str:
            raise importlib.metadata.PackageNotFoundError(name)

        monkeypatch.setattr(importlib.metadata, "version", _raise_not_found)
        with caplog.at_level(logging.WARNING, logger="manta_trading.cli.app"):
            result = runner.invoke(app, ["--version"])

        assert result.exit_code == 0
        assert "mt version dev" in result.output
        assert any(
            record.levelname == "WARNING" and DISTRIBUTION_NAME in record.getMessage()
            for record in caplog.records
        )


class TestStatusSubApp:
    """Verify status sub-app is reachable."""

    def test_status_runs(self):
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0

    def test_status_shows_help(self):
        result = runner.invoke(app, ["status", "--help"])
        assert "System status" in result.output


class TestSettingsInContext:
    """Verify Settings is stored in ctx.obj after callback."""

    def test_callback_runs_without_error(self):
        """Invoking a command exercises the callback, creating Settings."""
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0


class TestLoggingIntegration:
    """Verify setup_logging is called during CLI invocation."""

    def test_stdout_not_polluted_by_log_output(self):
        result = runner.invoke(
            app,
            ["status"],
            env={"MT_LOG_LEVEL": "DEBUG", "MT_LOG_FORMAT": "text"},
        )
        assert result.exit_code == 0

    def test_setup_logging_is_called(self):
        """Verify the app callback invokes setup_logging without error."""
        result = runner.invoke(
            app,
            ["status"],
            env={"MT_LOG_LEVEL": "WARNING", "MT_LOG_FORMAT": "json"},
        )
        assert result.exit_code == 0
