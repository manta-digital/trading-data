"""Tests for the Settings class (pydantic-settings env config)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from manta_trading.config import ENV_FILE, Settings
from manta_trading.constants import API_MAX_BARS_PER_REQUEST, API_SERVING_SESSION


class TestSettingsDefaults:
    """Verify default values when no env vars are set."""

    def test_log_level_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_LOG_LEVEL", raising=False)
        s = Settings()
        assert s.log_level == "INFO"

    def test_log_format_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_LOG_FORMAT", raising=False)
        s = Settings()
        assert s.log_format == "text"

    def test_api_keys_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_DATABENTO_API_KEY", raising=False)
        monkeypatch.delenv("MT_EODHD_API_KEY", raising=False)
        s = Settings(_env_file=None)
        assert s.databento_api_key is None
        assert s.eodhd_api_key is None

    def test_minute_provider_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT_MINUTE_PROVIDER", raising=False)
        s = Settings(_env_file=None)
        assert s.minute_provider == "eodhd"

    def test_db_url_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_MARKET_DB_URL", raising=False)
        monkeypatch.delenv("MT_TIMESCALE_DB_URL", raising=False)
        s = Settings(_env_file=None)
        assert s.market_db_url is None
        assert s.timescale_db_url is None

    def test_tick_db_url_default_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_TICK_DB_URL", raising=False)
        s = Settings(_env_file=None)
        assert s.tick_db_url is None

    def test_corporate_actions_provider_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT_CORPORATE_ACTIONS_PROVIDER", raising=False)
        s = Settings(_env_file=None)
        assert s.corporate_actions_provider == "eodhd"

    def test_eodhd_daily_limit_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT_EODHD_DAILY_LIMIT", raising=False)
        s = Settings(_env_file=None)
        assert s.eodhd_daily_limit == 100_000


class TestSettingsEnvOverride:
    """Verify env var override with MT_ prefix."""

    def test_log_level_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_LOG_LEVEL", "DEBUG")
        s = Settings()
        assert s.log_level == "DEBUG"

    def test_api_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_EODHD_API_KEY", "test-key-123")
        s = Settings()
        assert s.eodhd_api_key == "test-key-123"

    def test_db_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_MARKET_DB_URL", "postgresql://localhost/market")
        monkeypatch.setenv("MT_TIMESCALE_DB_URL", "postgresql://localhost/timescale")
        s = Settings()
        assert s.market_db_url == "postgresql://localhost/market"
        assert s.timescale_db_url == "postgresql://localhost/timescale"

    def test_tick_db_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_TICK_DB_URL", "postgresql://localhost/tick")
        s = Settings()
        assert s.tick_db_url == "postgresql://localhost/tick"

    def test_eodhd_api_key_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_EODHD_API_KEY", "eodhd-test-key")
        s = Settings()
        assert s.eodhd_api_key == "eodhd-test-key"

    def test_minute_provider_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_MINUTE_PROVIDER", "eodhd")
        s = Settings()
        assert s.minute_provider == "eodhd"

    def test_corporate_actions_provider_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_CORPORATE_ACTIONS_PROVIDER", "eodhd")
        s = Settings()
        assert s.corporate_actions_provider == "eodhd"

    def test_eodhd_daily_limit_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_EODHD_DAILY_LIMIT", "50000")
        s = Settings()
        assert s.eodhd_daily_limit == 50_000


class TestSettingsEnvFile:
    """Verify .env file loading."""

    def test_env_file_loading(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "MT_LOG_LEVEL=WARNING\n"
            "MT_MARKET_DB_URL=postgresql://localhost/market\n"
            "MT_TIMESCALE_DB_URL=postgresql://localhost/timescale\n"
        )
        monkeypatch.delenv("MT_LOG_LEVEL", raising=False)
        monkeypatch.delenv("MT_MARKET_DB_URL", raising=False)
        monkeypatch.delenv("MT_TIMESCALE_DB_URL", raising=False)
        s = Settings(_env_file=str(env_file))
        assert s.log_level == "WARNING"
        assert s.market_db_url == "postgresql://localhost/market"
        assert s.timescale_db_url == "postgresql://localhost/timescale"


class TestSettingsExtraIgnore:
    """Verify extra="ignore" — unknown MT_ vars do not raise."""

    def test_unknown_env_var_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_UNKNOWN_KEY", "whatever")
        s = Settings()
        assert not hasattr(s, "unknown_key")


class TestSettingsEnvFileConstant:
    """Verify the ENV_FILE constant is defined."""

    def test_env_file_constant(self) -> None:
        assert ENV_FILE == ".env"


class TestApiPolicySettings:
    """Slice 186 D9 — the two serving-API ceilings are operator-settable."""

    def test_defaults_come_from_constants(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MT_API_MAX_BARS_PER_REQUEST", raising=False)
        monkeypatch.delenv("MT_API_STATEMENT_TIMEOUT", raising=False)
        s = Settings(_env_file=None)
        assert s.api_max_bars_per_request == API_MAX_BARS_PER_REQUEST
        assert s.api_max_bars_per_request == 75_000
        assert s.api_statement_timeout == API_SERVING_SESSION.statement_timeout

    def test_max_bars_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_API_MAX_BARS_PER_REQUEST", "1000")
        assert Settings(_env_file=None).api_max_bars_per_request == 1000

    def test_statement_timeout_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MT_API_STATEMENT_TIMEOUT", "5s")
        assert Settings(_env_file=None).api_statement_timeout == "5s"

    def test_non_integer_max_bars_fails_at_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The failure must be at Settings() construction — i.e. at server
        startup — not at the first request that reads the ceiling."""
        monkeypatch.setenv("MT_API_MAX_BARS_PER_REQUEST", "lots")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)

    def test_non_positive_max_bars_fails_at_load(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A zero or negative ceiling would reject every request; catching it
        at load is the difference between a startup error and an outage."""
        monkeypatch.setenv("MT_API_MAX_BARS_PER_REQUEST", "0")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)


class TestKalshiRequestsPerMinute:
    """Slice 262 Decision 13: optional rate-budget override."""

    def test_unset_is_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MT_KALSHI_REQUESTS_PER_MINUTE", raising=False)
        s = Settings(_env_file=None)
        assert s.kalshi_requests_per_minute is None

    def test_env_override_loads_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_KALSHI_REQUESTS_PER_MINUTE", "120")
        s = Settings(_env_file=None)
        assert s.kalshi_requests_per_minute == 120

    def test_zero_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MT_KALSHI_REQUESTS_PER_MINUTE", "0")
        with pytest.raises(ValidationError):
            Settings(_env_file=None)
