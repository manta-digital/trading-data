"""Configuration package — Settings class for environment-based config."""

from __future__ import annotations

from datetime import date

from pydantic_settings import BaseSettings, SettingsConfigDict

from manta_trading.data.acquisition.daily.provider import DailyProviderName
from manta_trading.data.historical_minute.provider import MinuteProviderName

ENV_FILE = ".env"


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file.

    All variables are prefixed with ``MT_`` (e.g. ``MT_LOG_LEVEL``).
    """

    model_config = SettingsConfigDict(
        env_prefix="MT_",
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Logging
    log_level: str = "INFO"
    log_format: str = "text"

    # Provider credentials
    databento_api_key: str | None = None
    eodhd_api_key: str | None = None
    finnhub_api_key: str | None = None

    # Minute-bar provider selection. Pydantic accepts the StrEnum as both
    # the env-var string ("eodhd") and the enum value, and rejects unknown
    # strings at load time.
    minute_provider: MinuteProviderName = MinuteProviderName.EODHD

    # Daily-bar provider selection. EODHD is the default after the
    # AlphaVantage account was cancelled (2026-04-27); minute and daily
    # share one provider unless an explicit override.
    daily_provider: DailyProviderName = DailyProviderName.EODHD

    # Corporate-actions provider selection (slice 128). The StrEnum is upgraded
    # in Phase 2.1 once the protocol module lands; held as a string here to
    # keep the config layer free of a data-layer import cycle.
    corporate_actions_provider: str = "eodhd"

    # EODHD daily request quota. Drives the slice-128 backfill quota guard.
    # The free/$30-tier limit is 100K calls/day; override via env when on a
    # different plan.
    eodhd_daily_limit: int = 100_000

    # Database
    market_db_url: str | None = None
    timescale_db_url: str | None = None
    # Tick data database (separate instance)
    tick_db_url: str | None = None

    # Minute-bar backfill window. Operator override for the earliest date
    # the daemon will fetch 1-minute bars from. When unset, the effective
    # floor falls back to EODHD_INTRADAY_HORIZON (2004-01-01) clamped up
    # by the per-symbol first listing/data date — i.e. "everything the
    # provider has for that symbol." Set to narrow the window for cost
    # control or testing. ISO-8601 date string (YYYY-MM-DD) in env.
    minute_history_start: date | None = None
