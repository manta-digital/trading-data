"""Unit tests for mt data get command (slice 154)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
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


def _sample_df() -> pd.DataFrame:
    import numpy as np

    dates = pd.date_range("2024-01-02", periods=3, freq="D", tz="UTC")
    df = pd.DataFrame(
        {
            "open": [150.0, 151.0, 152.0],
            "high": [155.0, 156.0, 157.0],
            "low":  [148.0, 149.0, 150.0],
            "close": [153.0, 154.0, 155.0],
            "volume": [1_000_000, 1_100_000, 900_000],
        },
        index=dates,
    )
    df.index.name = "trade_date"
    return df


class TestDataGetValidation:
    def test_unknown_granularity_exits_nonzero(self):
        s = _settings()
        with _patch_app(s):
            result = runner.invoke(app, ["data", "get", "AAPL", "99x"])
        assert result.exit_code != 0
        assert "Unknown granularity" in result.output

    def test_missing_timescale_url_exits_nonzero(self):
        s = _settings(timescale_url=None)
        with _patch_app(s):
            result = runner.invoke(app, ["data", "get", "AAPL", "1d"])
        assert result.exit_code != 0

    def test_invalid_start_date_exits_nonzero(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ):
                result = runner.invoke(
                    app, ["data", "get", "AAPL", "1d", "--start", "not-a-date"]
                )
        assert result.exit_code != 0
        assert "--start" in result.output


class TestDataGetRouting:
    def test_daily_token_routes_to_daily_db(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                result = runner.invoke(
                    app, ["data", "get", "AAPL", "1d", "--start", "2024-01-01"]
                )
        assert result.exit_code == 0
        instance.get_daily_data.assert_called_once()
        call_kwargs = instance.get_daily_data.call_args
        # adjusted=True by default
        assert call_kwargs.kwargs.get("adjusted", True) is True

    def test_minute_token_routes_to_minute_db(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_minute_db.TimescaleMinuteDataDB",
                autospec=True,
            ) as MockMinute:
                instance = MockMinute.return_value
                instance.get_minute_data.return_value = sample
                result = runner.invoke(
                    app, ["data", "get", "AAPL", "1m", "--start", "2024-01-01"]
                )
        assert result.exit_code == 0
        instance.get_minute_data.assert_called_once()

    @pytest.mark.parametrize("token", ["1w", "1mo", "1q"])
    def test_coarser_daily_tokens_route_to_daily_db(self, token: str):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                result = runner.invoke(
                    app, ["data", "get", "AAPL", token, "--start", "2024-01-01"]
                )
        assert result.exit_code == 0
        instance.get_daily_data.assert_called_once()


class TestDataGetRawFlag:
    def test_raw_flag_passes_adjusted_false_to_daily_db(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                runner.invoke(
                    app,
                    ["data", "get", "AAPL", "1d", "--start", "2024-01-01", "--raw"],
                )
        call_kwargs = instance.get_daily_data.call_args
        assert call_kwargs.kwargs.get("adjusted") is False

    def test_default_no_raw_passes_adjusted_true(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                runner.invoke(
                    app, ["data", "get", "AAPL", "1d", "--start", "2024-01-01"]
                )
        call_kwargs = instance.get_daily_data.call_args
        assert call_kwargs.kwargs.get("adjusted") is True

    def test_raw_flag_passes_adjusted_false_to_minute_db(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_minute_db.TimescaleMinuteDataDB",
                autospec=True,
            ) as MockMinute:
                instance = MockMinute.return_value
                instance.get_minute_data.return_value = sample
                runner.invoke(
                    app,
                    ["data", "get", "AAPL", "1m", "--start", "2024-01-01", "--raw"],
                )
        call_kwargs = instance.get_minute_data.call_args
        assert call_kwargs.kwargs.get("adjusted") is False


class TestDataGetOutputFormats:
    def test_json_output(self):
        import json

        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                result = runner.invoke(
                    app,
                    ["data", "get", "AAPL", "1d", "--start", "2024-01-01", "--json"],
                )
        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["symbol"] == "AAPL"
        assert payload["granularity"] == "1d"
        assert isinstance(payload["rows"], list)

    def test_csv_output(self):
        s = _settings()
        sample = _sample_df()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.return_value = sample
                result = runner.invoke(
                    app,
                    ["data", "get", "AAPL", "1d", "--start", "2024-01-01", "--csv"],
                )
        assert result.exit_code == 0
        assert "open" in result.output
        assert "close" in result.output


class TestDataGetKeyError:
    def test_keyerror_surfaces_as_named_error(self):
        s = _settings()
        with _patch_app(s):
            with patch(
                "manta_trading.market.timescale_daily_db.TimescaleDailyDataDB",
                autospec=True,
            ) as MockDaily:
                instance = MockDaily.return_value
                instance.get_daily_data.side_effect = KeyError("prev_close")
                result = runner.invoke(
                    app, ["data", "get", "AAPL", "1d", "--start", "2024-01-01"]
                )
        assert result.exit_code != 0
        assert "Adjustment data missing" in result.output
