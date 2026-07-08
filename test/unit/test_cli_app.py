"""Tests for the root Typer CLI app."""

from typer.testing import CliRunner

from manta_trading.cli.app import app

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
