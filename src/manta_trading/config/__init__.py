"""Configuration package — Settings class for environment-based config."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from manta_trading.constants import (
    API_MAX_BARS_PER_REQUEST,
    API_SERVING_SESSION,
    DAILY_CYCLE_RETRY_INTERVAL,
)
from manta_trading.data.acquisition.daily.provider import DailyProviderName
from manta_trading.data.historical_minute.provider import MinuteProviderName

_MINUTES_PER_DAY = 24 * 60

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

    # How soon after a daily cycle ends the daemon may start another (912).
    # Operator-tunable because the right value is an empirical trade, not a
    # derivable one: short enough that an interrupted pass resumes promptly,
    # long enough that a provider outage does not re-issue the 100-credit bulk
    # EOD call on every tick for the rest of the day. Bounded below by 1 (zero
    # would busy-loop) and above by one day (beyond that the cadence gate stops
    # reopening within the pass it is meant to retry).
    daily_cycle_retry_minutes: int = Field(
        default=int(DAILY_CYCLE_RETRY_INTERVAL.total_seconds() // 60),
        gt=0,
        le=_MINUTES_PER_DAY,
    )

    @property
    def daily_cycle_retry_interval(self) -> timedelta:
        """The retry cadence as the runner consumes it."""
        return timedelta(minutes=self.daily_cycle_retry_minutes)

    # Serving-API policy ceilings (slice 186 D9). Both defaults live in
    # constants.py — one definition of the number and its derivation — and are
    # referenced, not restated, here. Env names follow the MT_ prefix:
    # MT_API_MAX_BARS_PER_REQUEST and MT_API_STATEMENT_TIMEOUT. They are read
    # once in the API lifespan hook, so changing one requires a restart, the
    # same contract as MT_TIMESCALE_DB_URL. work_mem, the estimator's
    # derivation inputs, and the pool sizes are deliberately not settable.
    api_max_bars_per_request: int = Field(
        default=API_MAX_BARS_PER_REQUEST, gt=0
    )
    # Pattern-constrained because the value is interpolated into a SET
    # statement (Postgres does not accept a bind parameter there). Validating
    # the shape at load time is what keeps that interpolation safe, and it
    # turns a typo into a startup error instead of a per-connection failure.
    api_statement_timeout: str = Field(
        default=API_SERVING_SESSION.statement_timeout,
        pattern=r"^\d+(us|ms|s|min|h|d)?$",
    )

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
