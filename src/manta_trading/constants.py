"""Project-wide constants for the manta_trading data platform.

Single source of truth for all threshold values, history limits, and
grace periods used by the data acquisition and quality pipelines.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
MAX_RETRY_COUNT: int = 5
"""Maximum number of fetch retries before a gap is marked RETRY_EXHAUSTED."""

DAILY_STALENESS_THRESHOLD: timedelta = timedelta(days=2)
"""A daily-granularity symbol is STALE if last_attempt_ts is older than this."""

MINUTE_STALENESS_THRESHOLD: timedelta = timedelta(days=1)
"""A minute-granularity symbol is STALE if last_attempt_ts is older than this."""

DAILY_HISTORY_MONTHS: int | None = None
"""Maximum history depth for daily bars. None means unbounded (all available)."""

EODHD_INTRADAY_HORIZON: date = date(2004, 1, 1)
"""Earliest date for which EODHD provides 1-minute bars.

Used as the absolute floor when computing per-symbol minute-history
windows. The effective floor for a symbol is
``max(EODHD_INTRADAY_HORIZON, settings.minute_history_start,
instruments.first_listing_date or instruments.first_data_date)``.
Override per-deployment via the ``MT_MINUTE_HISTORY_START`` env var.
"""

DAILY_HISTORY_FLOOR: date = date(1970, 1, 1)
"""Earliest date used as the start of a daily-bar fetch when no per-symbol
lifecycle anchor (first_listing_date / first_data_date) is available.

Single source of truth — replaces the three scattered ``datetime(1970,1,1)``
literals in the daily acquisition paths.
"""

LATE_BAR_GRACE_PERIOD: timedelta = timedelta(minutes=30)
"""Grace period after session_close_utc before a day is considered completed."""

MAX_GAP_STALENESS: timedelta = timedelta(minutes=5)
"""Maximum age of a data_gaps row before the gap is considered stale metadata."""

TRADING_SESSIONS_EXTENSION_YEARS: int = 2
"""Number of years beyond the current year to populate in trading_sessions.

The populated horizon is current_year + TRADING_SESSIONS_EXTENSION_YEARS.
Run ``mt data --extend`` to extend the horizon when it lapses.
"""

TRADING_SESSIONS_HORIZON_WARN_DAYS: int = 90
"""``mt data --extend --strict`` exits non-zero when any calendar's
MAX(session_date) is within this many days of today.
"""

DAEMON_LOCK_TIMEOUT: str = "30s"
"""PostgreSQL lock_timeout for advisory lock acquisition in the data daemon.

See slice 145 failure-modes table (PG advisory lock § lock_timeout exceeded):
a daemon iteration that can't acquire the lock within this budget is classified
as transient_failure and retried on the next cycle rather than blocking
indefinitely.
"""

MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT: str = "30s"
"""PostgreSQL statement_timeout for the universe-wide minute coverage-index
query (slice 162 ``build_minute_coverage_index``).

A small multiple of the measured ~3s full-universe scan of the
``minute_4hour_ohlcv`` cagg. On timeout the coverage index build fails safe:
coverage-aware seeding is skipped for that cycle rather than falling back to
the old full-window `[history_start, today]` seed.
"""

MINUTE_SEED_PROGRESS_LOG_INTERVAL: int = 250
"""Emit a minute-seed progress INFO line every this many symbols scanned.

The universe-wide seed pass (slice 162) runs silently otherwise; this bounds
how often the daemon reports `seeded N/<total> symbols, M gaps` during a
long-running cycle.
"""

EODHD_DAILY_QUOTA: int = 100_000
"""EODHD All-In-One plan: maximum API credits per UTC day."""

EODHD_PER_MINUTE_BURST: int = 1000
"""EODHD All-In-One plan: maximum API credits per rolling 60-second window.

Bursting up to this rate is allowed at any time, subject to the daily quota
above. Daemon throttling targets this as the short-window ceiling — NOT a
flattened average like 14/min or 70/min, which is the daily quota divided
by minutes-per-day and is not a meaningful operational limit.
"""

EODHD_INTRADAY_CALL_COST: int = 5
"""Credits charged per /intraday call (1-minute bars, ≤120-day window)."""

EODHD_EOD_CALL_COST: int = 1
"""Credits charged per /eod call (per-symbol daily history)."""

EODHD_BULK_EOD_BASE_COST: int = 100
"""Credits charged per /eod-bulk-last-day call covering a full exchange.

Note: when the ``symbols`` parameter is used, an ADDITIONAL 1 credit per
requested ticker is charged (e.g. 10 symbols = 110 credits). The
``symbols`` parameter is NOT supported for ``type=splits`` or
``type=dividends`` — those calls cover the entire exchange and cost a
flat 100 credits.
"""


class DailyMode(StrEnum):
    """Operating mode for the daemon's daily acquisition cycle."""

    BACKFILL = "BACKFILL"
    """Per-symbol /eod calls: used when any scope member has unresolved gaps."""

    STEADY_STATE = "STEADY_STATE"
    """One /eod-bulk-last-day call for the full exchange: used when all scope
    members are caught up (no UNKNOWN gaps)."""


class Granularity(StrEnum):
    """Canonical granularity tokens for OHLCV data requests."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"
    MO1 = "1mo"
    Q1 = "1q"


GRANULARITY_SOURCE: dict[Granularity, str] = {
    Granularity.M1: "minute_ohlcv",
    Granularity.M5: "minute_5min_ohlcv",
    Granularity.M15: "minute_15min_ohlcv",
    Granularity.H1: "minute_hourly_ohlcv",
    Granularity.H4: "minute_4hour_ohlcv",
    Granularity.D1: "daily_ohlcv",
    Granularity.W1: "daily_weekly_ohlcv",
    Granularity.MO1: "daily_monthly_ohlcv",
    Granularity.Q1: "daily_quarterly_ohlcv",
}
