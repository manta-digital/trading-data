"""Unit tests for mt data pull command (slice 154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from manta_trading.cli.app import app

runner = CliRunner()


def _settings(*, timescale_url: str | None = "postgresql://ts/db"):
    s = MagicMock()
    s.timescale_db_url = timescale_url
    return s


def _patch_app(settings):
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with (
            patch("manta_trading.cli.app.Settings", return_value=settings),
            patch("manta_trading.cli.app.setup_logging"),
        ):
            yield

    return _cm()


class TestDataPullGranularityValidation:
    def test_cagg_granularity_exits_with_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(app, ["data", "pull", "5m", "--symbol", "AAPL"])
        assert result.exit_code != 0
        assert "cagg" in result.output.lower() or "derived" in result.output.lower()

    @pytest.mark.parametrize("token", ["15m", "1h", "4h", "1w", "1mo", "1q"])
    def test_various_cagg_tokens_exit_with_error(self, token: str):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(app, ["data", "pull", token, "--symbol", "AAPL"])
        assert result.exit_code != 0

    def test_unknown_granularity_exits_with_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(app, ["data", "pull", "99x", "--symbol", "AAPL"])
        assert result.exit_code != 0


class TestDataPullSymbolSelection:
    def test_no_symbol_selector_exits_with_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(app, ["data", "pull", "1d"])
        assert result.exit_code != 0
        assert "Symbol selection required" in result.output

    def test_multiple_selectors_exits_with_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app, ["data", "pull", "1d", "--symbol", "AAPL", "--universe"]
            )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


class TestDataPullMutualExclusivity:
    def test_verify_and_dry_run_together_exit_with_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app,
                ["data", "pull", "1d", "--symbol", "AAPL", "--verify", "--dry-run"],
            )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output


class TestDataPullVerify:
    def test_verify_calls_no_fetch(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._pull_verify"
            ) as mock_verify, patch(
                "manta_trading.cli.commands.data._pull_fetch"
            ) as mock_fetch:
                mock_verify.return_value = None
                runner.invoke(
                    app,
                    [
                        "data",
                        "pull",
                        "1d",
                        "--symbol",
                        "AAPL",
                        "--verify",
                    ],
                )
        mock_verify.assert_called_once()
        mock_fetch.assert_not_called()


class TestDataPullResetConfirmation:
    def test_reset_without_yes_triggers_prompt(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._pull_fetch_terminal_gaps",
                return_value=[],
            ), patch(
                "manta_trading.cli.commands.data._pull_reset_and_fetch"
            ) as mock_reset:
                # Send empty input — user does not type 'reset'
                result = runner.invoke(
                    app,
                    ["data", "pull", "1d", "--symbol", "AAPL", "--reset"],
                    input="\n",
                )
        # Should exit 2 (operator declined) or show prompt
        assert "reset" in result.output.lower() or result.exit_code != 0

    def test_reset_with_yes_skips_prompt(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._pull_fetch_terminal_gaps",
                return_value=[],
            ), patch(
                "manta_trading.cli.commands.data._pull_reset_and_fetch"
            ) as mock_reset:
                result = runner.invoke(
                    app,
                    [
                        "data",
                        "pull",
                        "1d",
                        "--symbol",
                        "AAPL",
                        "--reset",
                        "--yes",
                    ],
                )
        mock_reset.assert_called_once()

    def test_reset_with_json_skips_prompt(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._pull_fetch_terminal_gaps",
                return_value=[],
            ), patch(
                "manta_trading.cli.commands.data._pull_reset_and_fetch"
            ) as mock_reset:
                result = runner.invoke(
                    app,
                    [
                        "data",
                        "pull",
                        "1d",
                        "--symbol",
                        "AAPL",
                        "--reset",
                        "--json",
                    ],
                )
        mock_reset.assert_called_once()


class TestDataPullDryRun:
    def test_dry_run_reports_without_fetching(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.cli.commands.data._pull_query_unknown_gaps",
                return_value=[
                    {
                        "symbol": "AAPL",
                        "gap_start": "2024-01-01",
                        "gap_end": "2024-01-05",
                        "fetch_status": "UNKNOWN",
                        "attempt_count": 0,
                    }
                ],
            ), patch(
                "manta_trading.cli.commands.data._pull_query_cold_symbols",
                return_value=[],
            ), patch(
                "manta_trading.cli.commands.data._pull_fetch"
            ) as mock_fetch:
                result = runner.invoke(
                    app,
                    [
                        "data",
                        "pull",
                        "1d",
                        "--symbol",
                        "AAPL",
                        "--dry-run",
                    ],
                )
        assert result.exit_code == 0
        assert "fetch" in result.output.lower()
        mock_fetch.assert_not_called()


class TestDataPullUniverseDelistedFilter:
    """Tests for --universe delisted filter and --include-delisted flag (slice 158)."""

    def _make_conn_mock(self) -> tuple[MagicMock, MagicMock]:
        """Build a psycopg connection mock that records cursor.execute calls."""
        cur = MagicMock()
        cur.__enter__ = lambda s: s
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = []

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        conn.cursor.return_value = cur
        return conn, cur

    def test_universe_default_excludes_delisted(self):
        s = _settings()
        conn, cur = self._make_conn_mock()
        with _patch_app(s):
            with patch("psycopg.connect", return_value=conn):
                runner.invoke(app, ["data", "pull", "1d", "--universe"])
        executed_sql = cur.execute.call_args[0][0]
        assert "delisted_at_eodhd = FALSE" in executed_sql
        assert "delisted_date IS NULL" in executed_sql

    def test_universe_include_delisted_removes_filter(self):
        s = _settings()
        conn, cur = self._make_conn_mock()
        with _patch_app(s):
            with patch("psycopg.connect", return_value=conn):
                runner.invoke(
                    app, ["data", "pull", "1d", "--universe", "--include-delisted"]
                )
        executed_sql = cur.execute.call_args[0][0]
        assert "delisted_at_eodhd" not in executed_sql

    def test_include_delisted_without_universe_exits_error(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(
                app, ["data", "pull", "1d", "--include-delisted", "--symbol", "AAPL"]
            )
        assert result.exit_code == 1
        assert "--universe" in result.output


class TestPullFetchLoopProviderErrors:
    """A non-retriable provider answer for one symbol is skipped and counted;
    a streak aborts the run (issue #19 follow-up: the 2026-08-31 universe pull
    died with a traceback on the first HTTP 402)."""

    def _run(self, side_effect, symbols):
        from datetime import date

        from manta_trading.cli.commands.data import _pull_fetch_inner

        conn = MagicMock()
        conn.__enter__ = lambda s: s
        conn.__exit__ = MagicMock(return_value=False)
        with (
            patch("psycopg.connect", return_value=conn),
            patch(
                "manta_trading.data.acquisition.daemon.minute.run_minute_refetch",
                side_effect=side_effect,
            ) as mock_refetch,
        ):
            _pull_fetch_inner(
                symbols=symbols,
                granularity="1m",
                start=date(2026, 6, 1),
                end=None,
                settings=_settings(),
                json_output=False,
                verbose=True,
            )
        return mock_refetch

    def test_one_provider_error_is_skipped_and_the_loop_continues(self, capsys):
        from manta_trading.data.acquisition.outcomes import ProviderResponseError

        ok = MagicMock(success_count=1, partial_count=0, empty_count=0)
        mock = self._run([ProviderResponseError("HTTP 403"), ok], ["BAD", "GOOD"])
        out = capsys.readouterr().out
        assert mock.call_count == 2
        assert "BAD" in out and "provider-error" in out
        assert "1 fetched, 1 failed" in out

    def test_consecutive_provider_errors_abort_the_run(self, capsys):
        from manta_trading.constants import PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS
        from manta_trading.data.acquisition.outcomes import ProviderResponseError

        symbols = [f"S{i}" for i in range(PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS + 3)]
        mock = self._run(ProviderResponseError("quota exhausted (HTTP 402)"), symbols)
        captured = capsys.readouterr()
        assert mock.call_count == PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS
        assert "aborting" in (captured.out + captured.err)
        assert f"{PULL_MAX_CONSECUTIVE_PROVIDER_ERRORS} failed" in captured.out
