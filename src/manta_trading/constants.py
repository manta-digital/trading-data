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

MAX_COVERAGE_SOURCE_STALENESS: timedelta = timedelta(days=1)
"""Absolute ceiling on how far a derived read (continuous aggregate) may lag its
raw source before the reader refuses to trust it (slice 168).

One ceiling serves both the acquisition path (``build_minute_coverage_index``)
and slice 167's status path: a derived read older than a full trading day is
stale for either purpose. Matches ``MINUTE_STALENESS_THRESHOLD``'s convention.

The ceiling is **required**, not belt-and-braces. The staleness threshold is
``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`` — without it, the daily
caggs' 21/90/270-day ``start_offset`` values mean a daily cagg stalled 100 days
would pass every ``start_offset``-relative check. It is also a full refresh
cycle above every minute policy's 1-day ``start_offset``, so it never fires on
a healthy cagg.
"""

CAGG_FRESHNESS_CACHE_TTL: timedelta = timedelta(seconds=60)
"""TTL for the process-local ``assert_cagg_fresh`` verdict cache (slice 168 D6).

Two orders of magnitude below ``MAX_COVERAGE_SOURCE_STALENESS``, so a cached
verdict can never mask a lag the uncached check would catch. Stale verdicts are
cached on the same terms as fresh ones — the cache never converts a refusal
into a pass.
"""

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

MINUTE_OHLCV_CHUNK_INTERVAL: timedelta = timedelta(days=7)
"""TimescaleDB ``chunk_time_interval`` for the ``minute_ohlcv`` hypertable.

Single source of truth (slice 166). The original 4-hour interval (slice 156)
produced 25,256 chunks over 22.5 years; planning any un-pruned query against
that many chunks took ~14 minutes and fragmented compression batches (85 GB
TOAST overhead). 7 days matches ``daily_ohlcv``'s proven-healthy interval and
the ``compress_after = 7 days`` policy cadence. Referenced by the
create-hypertable migration (001c), migration 043, and the slice 166 rechunk
maintenance command — never restate the value as a literal elsewhere.
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

MINUTE_CAGG_CHUNK_INTERVAL: timedelta = timedelta(days=70)
"""TimescaleDB ``chunk_time_interval`` for the four minute continuous
aggregates' materialized hypertables (slice 163).

Single source of truth. Derived from the journal wall-clock rule — chunk
interval = wall-clock span ÷ target chunk count, never data volume: 22.5 years
÷ 70 days ≈ 117 chunks per cagg, matching the healthy ``daily_*`` caggs
(70 days / ~300 chunks). Replaces the ~1.67-day interval TimescaleDB assigned
by defaulting to 10× ``minute_ohlcv``'s then-4-hour source interval, which
produced ~4,236 chunks per cagg. Rendered into migration 044 and asserted by
the ``mt data caggs repair`` pre-flight — never restate the value as a literal
elsewhere. On a cold start this is naturally satisfied: TimescaleDB sizes a new
cagg's mat hypertable at 10× the source interval, and post-043 ``minute_ohlcv``
is 7 days → 70 days automatic (migration 044 then a verified no-op).
"""

MINUTE_CAGG_COMPRESS_AFTER: timedelta = timedelta(days=7)
"""``compress_after`` for the four minute caggs' columnstore policies (slice 163).

Must be strictly **greater than** the caggs' refresh-policy ``start_offset``
(1 day) so the columnstore policy never compresses inside the actively-refreshed
head, where the trailing refresh still rewrites rows. 7 days mirrors the raw
``minute_ohlcv`` policy cadence (migration 042). Rendered into migration 045 —
never restate as a literal.
"""

MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT: str = "1800s"
"""PostgreSQL statement_timeout for the ``mt data caggs verify``/``repair``
prod parity and sweep queries (slice 163).

Precedent: ``MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT``. Must exceed the cost of
a single-window ``refresh_continuous_aggregate`` + parity ``COUNT(*)`` over one
70-day window on the 4.4-billion-row raw table.

**Sized from measurement, not estimate.** The original 300s was set from the 4h
cagg (17-62 s/window) and held for 1h (max 181 s) and 15m (max ~200 s), but the
5m sweep crossed it on prod at window 103/119: per-window cost climbed
268.8 s -> 287.5 s -> 305.7 s with raw volume and the refresh INSERT was
cancelled mid-sweep. Per-window cost scales with both raw volume *and* bucket
density, so the ceiling must clear the worst case — the finest granularity over
the densest years — not the granularity that happened to be measured first.
1800s leaves ~5x headroom over the observed 5m peak.

This bounds a single statement, not the sweep: a genuinely stuck query still
gets cancelled, just not a legitimately slow one. On timeout or client
interrupt, the maintenance code cancels the server-side backend (journal
20260720 discipline) before raising — never leaves a runaway query on prod.
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

MINUTE_OHLCV_TABLE: str = GRANULARITY_SOURCE[Granularity.M1]
"""The raw minute hypertable — the source of truth all four minute caggs derive
from. Derived from GRANULARITY_SOURCE so the name has exactly one definition."""

MINUTE_CAGG_GRANULARITIES: tuple[Granularity, ...] = (
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
)
"""The four minute continuous aggregates, in ascending bucket width — the
canonical order for reporting and for resolving user granularity input. This is
NOT a repair sweep order; per-granularity repair sequencing is an operator
decision (see cagg_repair.REPAIR_RUN_ORDER)."""
