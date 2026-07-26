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

CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT: str = "10s"
"""PostgreSQL statement_timeout for the ``assert_cagg_fresh`` catalog read and
the two ``max()`` edge probes (slice 168).

Precedent: ``MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT``. A small multiple of the
measured probe cost on prod (~0.19 s for the cagg edge, ~0.75 s for the raw
edge). Both probes are bounded ``max(time)`` index scans, not expression
aggregates over compressed chunks — this is not the query shape behind the
2026-07-20 incident — but the timeout discipline applies regardless: on timeout
the freshness check degrades to a refusal (``PROBE_FAILED``) rather than
stalling the reader that called it.
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

MINUTE_COVERAGE_VIEW: str = "minute_coverage"
"""Name of the hierarchical coverage continuous aggregate over
``minute_4hour_ohlcv`` (slice 167).

Single source of truth: passed to ``assert_cagg_fresh``, rendered into
migrations 046/047/048, and referenced by tests. Never restate as a literal.
"""

DAILY_COVERAGE_VIEW: str = "daily_coverage"
"""Name of the coverage continuous aggregate over raw ``daily_ohlcv`` (slice 167).

Unlike ``MINUTE_COVERAGE_VIEW`` this one is **not** hierarchical — its source is
the raw hypertable — so the timestamps it yields are exact rather than truncated
to a parent bucket. See slice 167 D3/D7.
"""

COVERAGE_BUCKET_INTERVAL: timedelta = timedelta(days=365)
"""``time_bucket`` width for both coverage continuous aggregates (slice 167).

One year. Sized so ``bars_summary`` groups ~15k rows (~5,871 symbols × ~22 years
of history) instead of the 4.4-billion-row raw scan that held the full-universe
``data_status`` read at 7.8 s. Grouping at this size is sub-millisecond
*regardless of the parent cagg's chunk count*, which is the durability argument
for the hierarchical structure (slice 167 D1).

``timedelta`` rather than a calendar year: TimescaleDB's ``time_bucket`` on a
``timestamptz`` column takes a fixed-width interval, and bucket boundaries need
not align to calendar years — the buckets are a grouping device for coverage
bookkeeping, not a reporting calendar.
"""

MINUTE_CAGG_REFRESH_START_OFFSET: timedelta = timedelta(days=1)
"""``start_offset`` of the four minute caggs' own refresh policies.

Measured on prod 2026-07-26 (jobs 1002/1003/1007/1008 — all four carry the same
1-day value). Recorded here as the **parent** window that slice 167's
hierarchical coverage policy must exceed; ``MINUTE_CAGG_COMPRESS_AFTER``'s
docstring already depends on this number, and the slice-167 constants test
asserts the coverage ``start_offset`` against it rather than against a literal.
Not itself rendered into a migration — migrations 035/037 own those policies.
"""

MINUTE_COVERAGE_REFRESH_START_OFFSET: timedelta = timedelta(days=30)
"""``start_offset`` for the ``minute_coverage`` refresh policy (slice 167 D4).

**Measured, not chosen by feel** (prod, 2026-07-26): the parent
``minute_4hour_ohlcv`` policy (job 1003) runs ``schedule_interval`` 1 h with
``start_offset`` 1 day and ``end_offset`` 4 h. A hierarchical cagg must
re-materialize any parent bucket that changed since it last ran, so its
``start_offset`` must exceed the parent's entire refresh window — 1 day — with
margin for a parent backfill or repair that rewrites recent history.

30 days is that window plus a wide margin, deliberately generous: a **too-narrow**
``start_offset`` on a hierarchical cagg is exactly what caused the ~79%
under-materialization that slice 163 had to repair, and stranded history is
never revisited by a scheduled run. The cost of over-wide is bounded — one
1-year bucket per symbol per refresh — while the cost of too-narrow is silent
permanent corruption. Asserted against the parent value by the slice-167
constants test.
"""

MINUTE_COVERAGE_REFRESH_END_OFFSET: timedelta = timedelta(hours=4)
"""``end_offset`` for the ``minute_coverage`` refresh policy (slice 167 D4).

Matches the parent 4h cagg's own ``end_offset`` (measured: 4 h). Refreshing
closer to now than the parent itself materializes would read buckets the parent
has not yet filled, producing coverage that undercounts the trailing edge.
"""

MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL: timedelta = timedelta(hours=1)
"""``schedule_interval`` for the ``minute_coverage`` refresh policy (slice 167 D4).

Matches the parent's 1 h cadence (measured, job 1003). Refreshing more often than
the parent produces no new data; less often widens the documented lag bound for
no benefit.
"""

DAILY_COVERAGE_REFRESH_START_OFFSET: timedelta = timedelta(days=30)
"""``start_offset`` for the ``daily_coverage`` refresh policy (slice 167 D4).

``daily_coverage``'s source is the **raw** ``daily_ohlcv`` hypertable, which has
no refresh policy of its own (measured, prod 2026-07-26 — only a compression
policy, ``compress_after`` 7 days). So the constraint here is not a parent
refresh window but late-arriving and revised daily bars: providers restate
recent history, and adjustment rebasing rewrites it.

30 days matches the minute side, keeping one operator-visible number rather than
two, and comfortably covers the 7-day compression horizon after which rows are
no longer expected to change.
"""

DAILY_COVERAGE_REFRESH_END_OFFSET: timedelta = timedelta(hours=1)
"""``end_offset`` for the ``daily_coverage`` refresh policy (slice 167 D4).

The raw source has no materialization lag to wait on, so this need only keep the
refresh off the actively-written head. 1 h is the smallest of the existing
minute-side end offsets and is well inside daily bars' once-per-session cadence.
"""

DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL: timedelta = timedelta(hours=1)
"""``schedule_interval`` for the ``daily_coverage`` refresh policy (slice 167 D4).

Matches the minute coverage cadence. Daily bars land once per session, so this is
far more often than strictly required; the refresh is cheap (one 1-year bucket
per symbol) and a uniform cadence keeps the documented lag bound uniform.
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

CAGG_BASE_GRANULARITY: dict[Granularity, Granularity] = {
    Granularity.M1: Granularity.M1,
    Granularity.M5: Granularity.M1,
    Granularity.M15: Granularity.M1,
    Granularity.H1: Granularity.M1,
    Granularity.H4: Granularity.M1,
    Granularity.D1: Granularity.D1,
    Granularity.W1: Granularity.D1,
    Granularity.MO1: Granularity.D1,
    Granularity.Q1: Granularity.D1,
}
"""The raw hypertable each granularity derives from, as a granularity.

The four minute caggs materialize from ``minute_ohlcv``; the three daily caggs
from ``daily_ohlcv``. The two base granularities map to themselves, which is how
callers distinguish "this is a cagg" from "this is the source" without parsing
view names. Used with GRANULARITY_SOURCE to resolve a cagg view to the raw table
its freshness is measured against (slice 168) — never infer this from a name
prefix.
"""

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
