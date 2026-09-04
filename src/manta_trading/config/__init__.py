"""Configuration package — Settings class for environment-based config."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from manta_trading.constants import (
    API_MAX_BARS_PER_REQUEST,
    API_SERVING_SESSION,
    DAILY_CYCLE_RETRY_INTERVAL,
)
from manta_trading.data.acquisition.daily.provider import DailyProviderName
from manta_trading.data.historical_minute.provider import MinuteProviderName
from manta_trading.data.kalshi.selection import CollectionRule

_MINUTES_PER_DAY = 24 * 60

ENV_FILE = ".env"

#: The Kalshi collection rule's environment prefix (slice 265, Decision 3) and
#: the prefix it replaced. Spelled once each, side by side: the five
#: ``kalshi_collection_*`` fields below are exactly this prefix under
#: ``env_prefix`` (``MT_`` + the field name, upper-cased), the ``status``
#: renderer cites it, and the guard translates old names to new ones.
KALSHI_COLLECTION_ENV_PREFIX = "MT_KALSHI_COLLECTION_"
RENAMED_KALSHI_CANDLE_ENV_PREFIX = "MT_KALSHI_CANDLE_"
#: The trades-tape filter's environment name (slice 268) — the
#: ``kalshi_trades_excluded_categories`` field under ``env_prefix``; spelled
#: once for the ``status`` renderer and its tests.
KALSHI_TRADES_FILTER_ENV = "MT_KALSHI_TRADES_EXCLUDED_CATEGORIES"

#: What pydantic-settings accepts for ``env_file`` / ``_env_file``.
_EnvFile = str | Path | Sequence[str | Path] | None


class RenamedSettingError(ValueError):
    """A setting still under its pre-rename name is set (slice 265)."""


def _env_file_paths(env_file: _EnvFile) -> tuple[Path, ...]:
    if env_file is None:
        return ()
    if isinstance(env_file, str | Path):
        return (Path(env_file),)
    return tuple(Path(each) for each in env_file)


def _renamed_settings_in_use(env_file: _EnvFile) -> set[str]:
    """Every ``MT_KALSHI_CANDLE_*`` name set in the environment **or** in the
    env file pydantic-settings is about to read. Both sources, because
    ``extra="ignore"`` means a stale line in ``.env`` never reaches
    ``os.environ`` — an environment-only scan would pass and the rule would
    silently revert to its defaults, the exact failure this guard prevents.
    """
    old = RENAMED_KALSHI_CANDLE_ENV_PREFIX
    found = {name for name in os.environ if name.upper().startswith(old)}
    for path in _env_file_paths(env_file):
        if path.is_file():
            found |= {
                name for name in dotenv_values(path) if name.upper().startswith(old)
            }
    return found


def reject_renamed_settings(env_file: _EnvFile) -> None:
    """Fail loudly if any ``MT_KALSHI_CANDLE_*`` variable is still set.

    pydantic-settings would otherwise **ignore** the old variable and fall
    back to the field default — a silent fallback, which CLAUDE.md forbids;
    an operator who set an allow-list would collect the default universe and
    never learn why. Runs before ``Settings`` reads anything, on the same env
    file it is about to read.
    """
    found = _renamed_settings_in_use(env_file)
    if not found:
        return
    old, new = RENAMED_KALSHI_CANDLE_ENV_PREFIX, KALSHI_COLLECTION_ENV_PREFIX
    renames = ", ".join(
        f"{name} is now {new}{name.upper()[len(old) :]}" for name in sorted(found)
    )
    raise RenamedSettingError(
        f"the Kalshi collection rule settings were renamed to {new}* in slice 265 "
        f"and the old names are no longer read: {renames}. Rename the "
        "variable(s) in the environment or the env file and retry."
    )


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

    def __init__(self, **values: Any) -> None:
        # The rename guard's seam: a validator cannot see which env file this
        # construction reads, so resolve it exactly as pydantic-settings will —
        # the ``_env_file`` keyword when passed (``None`` = environment only),
        # else ``model_config``'s — and scan it before anything is parsed.
        env_file: _EnvFile = (
            values["_env_file"]
            if "_env_file" in values
            else self.model_config.get("env_file")
        )
        reject_renamed_settings(env_file)
        super().__init__(**values)

    # Logging
    log_level: str = "INFO"
    log_format: str = "text"

    # Provider credentials
    databento_api_key: str | None = None
    eodhd_api_key: str | None = None
    finnhub_api_key: str | None = None
    # Kalshi authenticated mode (slice 261, TD 4a): a key ID plus the *path*
    # to the RSA private-key PEM file — never the key itself. Both or
    # neither; the client refuses a partial pair at construction.
    kalshi_api_key_id: str | None = None
    kalshi_private_key_path: Path | None = None
    # Kalshi rate budget override (slice 262, Decision 13): when set it
    # replaces the mode's constant budget at client construction; None
    # keeps 261's per-mode constants. Requests per minute, > 0.
    kalshi_requests_per_minute: int | None = Field(default=None, gt=0)
    # Kalshi collection rule (slice 264, Decision 2; renamed from
    # MT_KALSHI_CANDLE_* in slice 265, Decision 3, because one rule now governs
    # candles and trades). Defaults are the PM's rule C; every value is
    # overridable so another operator can collect a different universe.
    # Category strings are Kalshi's own series.category values — the venue
    # owns that vocabulary, so they are data, not an enum. The two category
    # sets are written comma-separated in the environment
    # (MT_KALSHI_COLLECTION_EXCLUDED_CATEGORIES=Sports, Mentions); NoDecode
    # stops pydantic-settings from JSON-parsing a set-typed field first, and
    # the validator below does the split. Empty allow-list = every category;
    # an empty pattern disables that clause. The patterns are PostgreSQL
    # regexes (series.ticker case-sensitive, series.title case-insensitive) —
    # a regex the database rejects fails the phase loudly with the database's
    # error.
    #
    # traded_only applies to **candles only**: the candle phase schedules on
    # it, while the trades path renders the rule in the "any" form because a
    # trade is itself proof of trading (design 265, *Settings — the rename*).
    kalshi_collection_traded_only: bool = True
    kalshi_collection_categories: Annotated[frozenset[str], NoDecode] = frozenset()
    kalshi_collection_excluded_categories: Annotated[frozenset[str], NoDecode] = (
        frozenset({"Sports", "Mentions"})
    )
    kalshi_collection_excluded_series_pattern: str | None = r"MENTION|SAY"
    kalshi_collection_excluded_title_pattern: str | None = (
        r"\m(say|says|mention|mentions)\M"
    )
    # Trades-tape category filter (slice 268, Decisions 1, 2): categories whose
    # trades are classified and counted but not stored. Deliberately NOT part
    # of the collection rule — candles for these categories keep collecting.
    # Empty (the default) means no filtering. Values are validated against the
    # catalog at phase start (Decision 9), not parsed here.
    kalshi_trades_excluded_categories: Annotated[frozenset[str], NoDecode] = frozenset()

    @field_validator(
        "kalshi_collection_categories",
        "kalshi_collection_excluded_categories",
        "kalshi_trades_excluded_categories",
        mode="before",
    )
    @classmethod
    def _split_category_list(cls, value: object) -> object:
        # A .env author writes ``Sports, Mentions``, not a JSON list: split on
        # commas, trim whitespace, drop empties; "" is the empty set. A value
        # that is already a collection (a programmatic Settings(...)) passes.
        if isinstance(value, str):
            return frozenset(part.strip() for part in value.split(",") if part.strip())
        return value

    @field_validator(
        "kalshi_collection_excluded_series_pattern",
        "kalshi_collection_excluded_title_pattern",
        mode="before",
    )
    @classmethod
    def _empty_pattern_disables(cls, value: object) -> object:
        # ``MT_..._PATTERN=`` means "no clause" — None, so the repository omits
        # it; the empty regex would match every ticker and exclude everything.
        if isinstance(value, str) and value.strip() == "":
            return None
        return value

    def collection_rule(self) -> CollectionRule:
        """The Kalshi collection rule in force — the single parse point, for
        candles and trades alike (slice 265, Decision 3).

        ``selection.selection_sql`` evaluates it as: allow-list if non-empty
        → exclude-list → patterns → traded. A category named in both lists is
        excluded — exclude wins.
        """
        return CollectionRule(
            traded_only=self.kalshi_collection_traded_only,
            categories=self.kalshi_collection_categories,
            excluded_categories=self.kalshi_collection_excluded_categories,
            excluded_series_pattern=self.kalshi_collection_excluded_series_pattern,
            excluded_title_pattern=self.kalshi_collection_excluded_title_pattern,
        )

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
    api_max_bars_per_request: int = Field(default=API_MAX_BARS_PER_REQUEST, gt=0)
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
    # Migration/maintenance credential (slice 913 D4). Deliberately separate
    # from `timescale_db_url`: the daemon, API, and CLI read paths run as the
    # DML-only application role, while DDL — migrate apply, init, rechunk,
    # cagg repair/refresh, restore — resolves this key explicitly. Callers must
    # never fall back to `timescale_db_url` when this is unset; that fallback
    # would restore exactly the single-credential coupling 913 removes.
    timescale_maintenance_url: str | None = None
    # Tick data database (separate instance)
    tick_db_url: str | None = None

    # Minute-bar backfill window. Operator override for the earliest date
    # the daemon will fetch 1-minute bars from. When unset, the effective
    # floor falls back to EODHD_INTRADAY_HORIZON (2004-01-01) clamped up
    # by the per-symbol first listing/data date — i.e. "everything the
    # provider has for that symbol." Set to narrow the window for cost
    # control or testing. ISO-8601 date string (YYYY-MM-DD) in env.
    minute_history_start: date | None = None
