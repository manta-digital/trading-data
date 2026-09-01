"""Unit tests for ``mt data health`` (slice 919)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from manta_trading.cli.app import app
from manta_trading.cli.commands.health import (
    EXIT_HEALTHY,
    EXIT_UNAVAILABLE,
    EXIT_UNHEALTHY,
    HealthCheck,
    check_cagg,
    check_phase_recency,
    check_quota,
    check_raw_freshness,
    render,
)

runner = CliRunner()
NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class TestRules:
    def test_raw_freshness_within_threshold_passes(self) -> None:
        c = check_raw_freshness(
            "minute data", NOW - timedelta(days=1), now=NOW, threshold=timedelta(days=4)
        )
        assert c.ok and "minute data" == c.name

    def test_raw_freshness_past_threshold_fails(self) -> None:
        c = check_raw_freshness(
            "minute data",
            NOW - timedelta(days=24),
            now=NOW,
            threshold=timedelta(days=4),
        )
        assert not c.ok and "24.0 d ago" in c.detail

    def test_raw_freshness_no_rows_fails(self) -> None:
        assert not check_raw_freshness(
            "daily data", None, now=NOW, threshold=timedelta(days=5)
        ).ok

    def test_cagg_verdict_is_passed_through(self) -> None:
        assert check_cagg("minute_5min_ohlcv", False, "lag=21 days").ok is False
        assert (
            check_cagg("minute_5min_ohlcv", True, "fresh").name
            == "cagg minute_5min_ohlcv"
        )

    def test_quota_counts_extra_calls_as_headroom(self) -> None:
        # 2026-08-31: 99,996/100,000 used but 405,425 extra — healthy.
        c = check_quota(99_996, 100_000, 405_425, headroom_min=20_000)
        assert c.ok and "405,429 remaining" in c.detail

    def test_quota_below_floor_fails(self) -> None:
        assert not check_quota(99_996, 100_000, 0, headroom_min=20_000).ok

    def test_phase_recency_recent_passes_stale_fails_never_fails(self) -> None:
        limit = timedelta(hours=3)
        assert check_phase_recency(
            "kalshi trades", NOW - timedelta(hours=1), now=NOW, threshold=limit
        ).ok
        assert not check_phase_recency(
            "kalshi trades", NOW - timedelta(hours=5), now=NOW, threshold=limit
        ).ok
        assert not check_phase_recency(
            "kalshi trades", None, now=NOW, threshold=limit
        ).ok

    def test_render_names_the_failing_count(self) -> None:
        text = render([HealthCheck("a", True, "x"), HealthCheck("b", False, "y")])
        assert text.splitlines()[-1] == "UNHEALTHY: 1 of 2 checks failing"
        assert render([HealthCheck("a", True, "x")]).splitlines()[-1] == "healthy"


def _settings(**overrides):
    s = MagicMock()
    s.timescale_db_url = "postgresql://ts/db"
    s.eodhd_api_key = "k"
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


def _invoke(settings, checks):
    with (
        patch("manta_trading.cli.app.Settings", return_value=settings),
        patch("manta_trading.cli.app.setup_logging"),
        patch("manta_trading.cli.commands.health.psycopg.connect"),
        patch("manta_trading.cli.commands.health.gather", return_value=checks),
    ):
        return runner.invoke(app, ["data", "health"])


class TestCommand:
    def test_all_passing_exits_zero(self) -> None:
        result = _invoke(_settings(), [HealthCheck("minute data", True, "fresh")])
        assert result.exit_code == EXIT_HEALTHY
        assert "healthy" in result.output

    def test_any_failing_exits_one(self) -> None:
        result = _invoke(
            _settings(),
            [
                HealthCheck("minute data", True, "fresh"),
                HealthCheck("cagg x", False, "lag"),
            ],
        )
        assert result.exit_code == EXIT_UNHEALTHY
        assert "FAIL cagg x" in result.output

    def test_missing_configuration_exits_two(self) -> None:
        result = _invoke(_settings(eodhd_api_key=None), [])
        assert result.exit_code == EXIT_UNAVAILABLE

    def test_json_payload_carries_every_check(self) -> None:
        import json

        checks = [HealthCheck("a", True, "x"), HealthCheck("b", False, "y")]
        with (
            patch("manta_trading.cli.app.Settings", return_value=_settings()),
            patch("manta_trading.cli.app.setup_logging"),
            patch("manta_trading.cli.commands.health.psycopg.connect"),
            patch("manta_trading.cli.commands.health.gather", return_value=checks),
        ):
            result = runner.invoke(app, ["data", "health", "--json"])
        payload = json.loads(result.output)
        assert payload["healthy"] is False
        assert [c["name"] for c in payload["checks"]] == ["a", "b"]
