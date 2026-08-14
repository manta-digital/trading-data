"""Project-wide constants for the manta_trading data platform.

Single source of truth for all threshold values, history limits, and
grace periods used by the data acquisition and quality pipelines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Final

DISTRIBUTION_NAME: Final[str] = "manta-trading-data"
"""PyPI distribution name for this package (slice 908).

This is the *distribution* name only — the import package is
``manta_trading`` (renamed by slice 911) and the config paths (see
``config/manager.py``) deliberately do not track it (slice 908 D8).
"""

# -- Self-update (`mt update`, slice 909) -------------------------------------

PYPI_JSON_URL_TEMPLATE: Final[str] = "https://pypi.org/pypi/{name}/json"
"""PyPI JSON API endpoint template for a distribution (slice 909 D2).

Format with ``name=DISTRIBUTION_NAME``. ``info.version`` in the response is
the latest *non-yanked* release, which is exactly what ``mt update`` offers.
"""

PYPI_REGISTRY_TIMEOUT: Final[float] = 10.0
"""Seconds allowed for the PyPI registry query (slice 909 D2).

Named for the package index specifically — "registry" alone is ambiguous in
this codebase, which also has a provider registry (review 909 F005).

Mirrors the 10 s budget of the ported ``cf update`` implementation. On expiry
``fetch_latest_version`` returns ``None`` rather than raising.
"""

UPGRADE_TIMEOUT: Final[float] = 600.0
"""Seconds allowed for the ``uv tool install --upgrade`` subprocess (909 D5).

A cold-cache clean install of this distribution measured 7.25 s wall
(2026-08-02, full dependency download plus venv build), so 600 s is ~80x the
measured cost: room for slow links and future wheel growth while still
bounding a genuine hang on the unattended ``--yes`` path.
"""

UPDATE_VERSION_PROBE_TIMEOUT: Final[float] = 10.0
"""Seconds allowed for the post-upgrade ``mt --version`` probe (909 D5).

Three timed runs of ``mt --version`` from a uv tool install measured
0.42 / 0.45 / 0.44 s wall (2026-08-02) — CLI import cost only, no network and
no database — so 10 s is ~20x the measured cost. On expiry the update reports
success without a verified version rather than failing.
"""

UPDATE_MIGRATE_PROBE_TIMEOUT: Final[float] = 30.0
"""Seconds allowed for the post-upgrade migration-status probe (909 D6).

Three timed runs of ``mt data migrate status --json`` against production
mid-backfill (2026-08-02) measured 0.58 / 1.93 / 6.90 s wall, so 30 s is ~4x
the worst sample. Its primary job is bounding an *unreachable* database
(libpq's default ``connect_timeout`` is unlimited); firing early costs only
the generic pointer line, never a wrong count.
"""

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
TOAST overhead). 7 days was derived from the wall-clock rule (span ÷ target
chunk count) and matches the ``compress_after = 7 days`` policy cadence.
Referenced by the create-hypertable migration (001c), migration 043, and the
rechunk registry's ``MINUTE`` target — never restate the value as a literal
elsewhere.
"""

DAILY_OHLCV_CHUNK_INTERVAL: timedelta = timedelta(days=70)
"""TimescaleDB ``chunk_time_interval`` for the ``daily_ohlcv`` hypertable.

Single source of truth (slice 170). Derived from the same wall-clock rule as
the minute interval — chunk interval = wall-clock span ÷ target chunk count,
never data volume: 22.6 years ÷ 70 days ≈ 118 chunks. The 7-day interval set
at creation (slice 143) was never revisited and produced 3,371 chunks over only
~34.7 M rows, the same over-chunking pathology slices 166 and 163 repaired
elsewhere: ``SELECT MAX(time)`` exceeded 30 s and a 31k-symbol ``ANY``
aggregate could not finish planning in 120 s.

70 = 10 × 7, so the existing 7-day chunks nest **exactly** inside the
epoch-aligned 70-day grid. Every target window therefore contains only whole
chunks and collapses to exactly one chunk, which is what makes the rechunk
rewrite safe (slice 166's grid-alignment caveat is satisfied by construction).

Referenced by the create-hypertable migration (023), migration 050, and the
rechunk registry's ``DAILY`` target — never restate the value as a literal
elsewhere.
"""

LATE_BAR_GRACE_PERIOD: timedelta = timedelta(minutes=30)
"""Grace period after session_close_utc before a day is considered completed.

Used by the ``data_status`` view and migration 043. This is a *session-close*
offset. It is NOT the daemon's daily-pass start gate — see
``DAILY_CYCLE_START_OFFSET``, which the daemon uses and which happens to carry
the same duration today (slice 912 D3).
"""

DAILY_CYCLE_START_OFFSET: timedelta = timedelta(minutes=30)
"""How long after UTC midnight the daemon waits before starting a daily pass.

The wait exists so the provider has published its late bars for the completed
session before the cycle asks for them. This is an offset from **UTC midnight**,
not from any session close — the two are different clocks, and a symbol's
session close has no fixed relationship to UTC midnight.

Split out from ``LATE_BAR_GRACE_PERIOD`` in slice 912 (D3), which the daemon
previously borrowed. It also equals ``DAILY_CYCLE_RETRY_INTERVAL`` as of the
912 review. All three values are equal today; that is coincidence and nothing
may rely on it. Tuning one must not drag the others along.
"""

DAILY_CYCLE_RETRY_INTERVAL: timedelta = timedelta(minutes=30)
"""Default spacing between daily-cycle attempts — a busy-loop guard only.

Slice 912 (D2) moved the "is there daily work?" question out of the runner's
timer and into ``run_daily_cycle``, which derives it from
``acquisition_state``. The runner therefore no longer needs a once-per-day
gate; it needs only to avoid spinning. This constant is that guard, and it is
NOT a statement about how often daily data changes.

Only the **default**: the operator overrides it with
``MT_DAILY_CYCLE_RETRY_MINUTES`` or ``--daily-retry-minutes``. It is tunable
because the right value is empirical, not derivable — a shorter interval
resumes an interrupted pass sooner, while a longer one spends fewer credits
when the provider is failing, since each retry re-issues the bulk EOD call and
``eodhd_get`` consumes the bucket once per retry attempt.

Raised from 15 to 30 minutes after the slice 912 code review (F002): observed
catch-up once a provider recovers is under two hours, so polling every 15
minutes bought no recovery speed the fetch itself did not already bound, while
doubling the worst-case outage spend. A fully-drained scope now costs ~47 no-op
ticks per day, each a small-table read with no provider call.

Equal to ``DAILY_CYCLE_START_OFFSET`` and ``LATE_BAR_GRACE_PERIOD`` today. All
three are coincidences of value, not of meaning — this one is a retry cadence,
that one a start gate, the third a session-close offset. Nothing may collapse
them, and ``test_constants.py`` asserts each independently for that reason.
"""

RUNNER_WAIT_PROGRESS_INTERVAL: timedelta = timedelta(minutes=5)
"""How often the runner restates that it is deliberately waiting on a gate.

A `--stop-when-done` run that meets a closed cadence gate sleeps rather than
exiting (912 D5). Announcing that once and then falling silent for up to half an
hour is indistinguishable from a hang to whoever is watching, so the wait
repeats at this interval with the remaining time. Long enough not to be noise at
the 60s sleep cap; short enough that silence never outlasts an operator's
patience.
"""

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

MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT: str = "300s"
"""PostgreSQL statement_timeout for the minute coverage queries
(slice 162 ``build_minute_coverage_index``, slice 165
``build_symbol_minute_coverage``).

Originally 30s, "a small multiple of the measured ~3s" universe scan of the
``minute_4hour_ohlcv`` cagg (slice-162 prep, 2.4M symbol-day pairs). Set to
300s in slice 165 from an end-to-end measurement, not a server-side one:
``statement_timeout`` keeps counting while the server STREAMS rows to the
client, and the universe build transfers every (symbol, day) pair. At the
2026-07-28 audit (``user/reference/prod-scale-and-coverage-scan-baseline.md``)
the server-side aggregation alone is ~18s, but the full app path —
aggregation + streaming 22,687,901 rows + psycopg parsing — measured
152.2s (9.4x pair growth from backfill since slice 162; growth, not
regression). 300s covers the measured cost plus the converging plateau
(~1.5x more pairs at full backfill; history bounded at 2004, universe
~11.6k symbols) and load variance. Transfer dominating aggregation is the
key sizing input — a day-grain coverage cagg (issue #3) fixes the
aggregation but NOT the transfer, so per-symbol reads or a compact
(array_agg) wire format are the long-term fixes if this cost ever matters.

On timeout the coverage build fails safe: coverage-aware seeding is skipped
for that cycle rather than falling back to the old full-window
`[history_start, today]` seed.
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

COVERAGE_BUCKET_INTERVAL: timedelta = timedelta(days=7)
"""``time_bucket`` width for both coverage continuous aggregates (slice 167,
narrowed from 365 days by slice 169).

**Why it is no longer a year.** A refresh policy's window is truncated to whole
buckets and only re-materializes buckets *fully contained* in it, so the open
(current) bucket is never written by the policy — it is materialized once at
cagg creation and then not again until it closes. At a 365-day width that made
both hourly policies successful no-ops for 205 consecutive runs: measured on
prod 2026-08-11, both coverage caggs sat at 2025-12-26 while raw ran to
2026-08-07, and a forced full-span refresh did not move the head.

Narrowing does **not** make the engine refresh an open bucket — nothing does.
It bounds how much data that limitation can hide. The width therefore sets the
worst-case coverage lag directly:

    worst-case coverage lag = COVERAGE_BUCKET_INTERVAL + end_offset

At 7 days that is 7 d 4 h, down from up to a year. ``COVERAGE_CONTENT_STALENESS``
and ``COVERAGE_BUCKET_LAG_BUDGET`` are both derived from it for exactly this
reason.

**Row-count trade, measured (slice 169 Task B, not worst-case estimates).** Row
count scales as 1/width. On a prod-shaped database (12,040 daily symbols over
1962-2026; 5,871 minute symbols over 2004-2026) the actuals are:

    width   minute_coverage   daily_coverage
     365 d      ~15 k (measured, slice 167)
      30 d      708,568         3,914,188
       7 d    3,019,870        16,742,957

Read cost is linear in those rows and saturates at **one** parallel worker
(measured: flat from 1 to 16 workers), so this is a straight volume trade, not
a parallelism cliff. The 4.4-billion-row raw scan slice 167 escaped is still
two orders of magnitude away.

``timedelta`` rather than a calendar interval: TimescaleDB's ``time_bucket`` on
a ``timestamptz`` column takes a fixed-width interval, and bucket boundaries
need not align to a calendar — the buckets are a grouping device for coverage
bookkeeping, not a reporting calendar. Whole days also nest cleanly inside
``minute_coverage``'s 4-hour parent buckets.

**This width sets a hard floor on the refresh policies' ``start_offset``** —
see ``COVERAGE_REFRESH_MIN_WINDOW_BUCKETS``. Changing it moves that floor;
narrowing *relaxes* it (750 days → 16 days, the one constraint that got easier).

Changing this value is a **drop-and-rebuild**, not an ``ALTER``: the width is
compiled into each cagg's view definition and TimescaleDB has no re-bucket
operation, so both caggs must be dropped, recreated, and re-materialized over
full history (migrations 051/052).
"""

COVERAGE_SOURCE_TABLE: dict[str, str] = {
    MINUTE_COVERAGE_VIEW: "minute_ohlcv",
    DAILY_COVERAGE_VIEW: "daily_ohlcv",
}
"""The table each coverage cagg's freshness is measured against (slice 167).

``assert_cagg_fresh`` resolves a view's source from ``GRANULARITY_SOURCE``,
which maps granularities to *base* hypertables and therefore has no entry for
the coverage caggs. Rather than widen that map — the coverage caggs are not a
granularity — this slice supplies the source explicitly through the helper's
``source_table`` seam, consuming slice 168's helper unchanged.

**Both entries are raw hypertables, including the hierarchical minute one.**
``minute_coverage`` derives from ``minute_4hour_ohlcv``, so measuring against
its immediate parent looks more natural — but two things argue for raw. First,
mechanically: slice 168's ``_raw_max`` probes ``max(time)`` on the source, and
a cagg's time column is ``time_bucket``, so a cagg source cannot be probed.
Second, and the reason this is right rather than merely expedient: what an
operator needs from ``data_status`` is how far coverage trails *reality*, not
how far it trails an intermediate. Measuring against raw makes the verdict cover
the whole two-hop chain — exactly the bound migration 048's ``COMMENT ON VIEW``
documents. A stalled parent would otherwise leave ``minute_coverage`` looking
fresh while ``data_status`` reported months-old coverage.
"""

COVERAGE_REFRESH_MIN_WINDOW_BUCKETS: int = 2
"""TimescaleDB's minimum refresh-window width, in buckets (slice 167 D4).

``add_continuous_aggregate_policy`` rejects a policy unless
``start_offset - end_offset >= COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * bucket``,
raising ``InvalidParameterValue: policy refresh window too small``.

The engine requires it because a refresh only re-materializes buckets *fully
contained* in ``[now - start_offset, now - end_offset]``; a partially covered
bucket at either edge is skipped rather than written with a half-computed
aggregate. A window narrower than two buckets can therefore slide into a
position where it fully contains nothing and the policy silently refreshes zero
rows. Two buckets guarantees at least one is always wholly inside.

Measured on TimescaleDB 2.21.3 with a 365-day bucket and a 4 h ``end_offset``:
730 days is rejected, 731 days accepted. Recorded as a constant so the
constants test asserts the real engine constraint rather than a remembered
number, and so a future change to ``COVERAGE_BUCKET_INTERVAL`` fails loudly at
test time instead of at migration time.

This constraint never bound the pre-167 caggs: each has a bucket far smaller
than its offsets (4 h bucket / 1 day offset; 3-month bucket / 270-day offset).
The 365-day coverage bucket was the first one large relative to any sane
refresh window — which is precisely why its open bucket was never
re-materialized (slice 169). At the narrowed 7-day width the coverage caggs
rejoin the well-behaved group: the floor drops from 731 days to 14.
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

MINUTE_CAGG_REFRESH_SCHEDULE_INTERVAL: timedelta = timedelta(hours=1)
"""``schedule_interval`` of the 1h and 4h minute caggs' refresh policies.

Measured on prod 2026-07-26 (jobs 1002/1003). Recorded here as the **parent**
hop of slice 167's two-hop coverage lag bound: raw ingest → parent cagg →
coverage cagg. Migration 048's ``COMMENT ON VIEW`` renders the documented bound
from this constant plus the coverage cadence, so the comment cannot drift from
the policies actually installed. Migration 035 owns the parent policy itself.
"""

MINUTE_COVERAGE_REFRESH_START_OFFSET: timedelta = timedelta(days=16)
"""``start_offset`` for the ``minute_coverage`` refresh policy (slice 167 D4,
re-derived at the narrowed width by slice 169 D4a).

Three constraints bind here, and the engine's is still the largest:

1. **Floor from the parent** (measured on prod 2026-07-26): the parent
   ``minute_4hour_ohlcv`` policy (job 1003) runs ``schedule_interval`` 1 h with
   ``start_offset`` 1 day, ``end_offset`` 4 h. A hierarchical cagg must
   re-materialize any parent bucket that changed since it last ran, so its
   window must exceed the parent's entire refresh window, with margin. 16 days
   clears this by an order of magnitude.
2. **Floor from TimescaleDB** (the binding one): ``start_offset - end_offset``
   must be at least ``COVERAGE_REFRESH_MIN_WINDOW_BUCKETS`` × the bucket width.
   At 7 days that is 14 days *of window*, so the smallest usable
   ``start_offset`` is 14 d + ``end_offset`` — not 14 d flat. Verified against
   TimescaleDB 2.29.1 with a 7-day bucket and a 4 h ``end_offset``: 14 days is
   rejected, 14 days 4 hours is the first value accepted.
   ``test_coverage_refresh_window_satisfies_timescale_minimum`` asserts this
   from the constants, so a width change fails at test time rather than against
   a live database — it caught exactly this off-by-one-end_offset during 169.
3. **Measured runtime must fit the schedule interval** (new in 169 D4a).

16 days is the floor (14 d 4 h) rounded up to a whole day, leaving ~20 h of
margin so a future ``end_offset`` change does not immediately breach it — the
same margin rationale the retired 750-day value used against its 731-day floor.

**Why 750 days is gone.** That value was *forced* by the 365-day width's
731-day engine floor — not independently motivated. Narrowing relaxes the floor
47x, and leaving 750 in place would not have been neutral: both hourly policies
had been successful no-ops since creation, so after the repair they begin doing
real work every hour. A 750-day window over 12,040 daily and 5,871 minute
symbols, on a host that also runs the daemon, risks a policy overrunning its
1-hour schedule interval — which would re-create the perpetually-behind head
this slice exists to fix. The engine floor minimises per-run work and maximises
schedule-interval margin by construction.

Constraint 3 could **not** be measured at selection time: slice 169 Task B.6a
measured 0.023 s per run at this offset, but on a quiescent database with no
concurrent ingest, so the refresh had almost no invalidations to process. That
number is a floor, not a prediction — it establishes that width does not drive
policy cost, not that a run fits the interval under live ingest. Choosing the
engine floor is the response to that uncertainty rather than a claim it was
resolved. Slice 169 part 2 (Task G) takes the real measurement on prod.

This is a bound on how far back the policy will *look*, not work performed per
run: refresh is driven by TimescaleDB's invalidation tracking, so a run rewrites
only the buckets that actually changed — in steady state the current bucket for
the symbols that got new bars, ~1-2 rows per affected symbol.

**Residual hazard, deliberately accepted — and larger at this width.** A bucket
older than the window that is rewritten by a deep backfill will not be picked up
by the scheduled policy. That is the same stranding shape that produced the ~79%
under-materialization slice 163 repaired. Narrowing ``start_offset`` from 750 to
16 days moves that boundary much closer in, so a backfill older than two weeks
now strands coverage where previously one older than two years would.

This is the deliberate trade for constraint 3: an hourly policy that reliably
keeps the head current beats a wide one that may overrun its interval, given
that deep-backfill stranding is *already* a known condition with an established
remedy. It is detected by ``mt data caggs verify`` and healed by ``repair`` —
the standing rule after any raw restructuring or deep backfill, unchanged by
this slice. Slice 169's own full-history rematerialization is exactly that
remedy applied once.
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

DAILY_COVERAGE_REFRESH_START_OFFSET: timedelta = timedelta(days=16)
"""``start_offset`` for the ``daily_coverage`` refresh policy (slice 167 D4,
re-derived at the narrowed width by slice 169 D4a).

``daily_coverage``'s source is the **raw** ``daily_ohlcv`` hypertable, which has
no refresh policy of its own (measured, prod 2026-07-26 — only a compression
policy, ``compress_after`` 7 days). So there is no parent refresh window to
clear; the binding constraint is TimescaleDB's two-bucket minimum window (see
``COVERAGE_REFRESH_MIN_WINDOW_BUCKETS``), identical to the minute side because
the bucket width is the same.

Matching the minute side keeps one operator-visible number rather than two.
See ``MINUTE_COVERAGE_REFRESH_START_OFFSET`` for the full derivation of why
750 days was retired: it was forced by the 365-day width's 731-day engine
floor, and leaving it would have had both hourly policies — no-ops until this
slice — begin doing real work over a 750-day window every hour.

**Narrower than the daily revision window, deliberately.** 750 days comfortably
covered provider restatements and adjustment rebasing; 16 days does not. Daily
bars are expected to stop changing after the 7-day compression horizon, so the
routine case is covered — but a deep restatement beyond two weeks will strand
coverage until ``mt data caggs verify``/``repair`` heals it. That is the same
accepted residual the minute side carries, and the same remedy applies.
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
far more often than strictly required; the refresh is cheap (one bucket per
symbol) and a uniform cadence keeps the documented lag bound uniform.
"""

COVERAGE_CONTENT_STALENESS: timedelta = COVERAGE_BUCKET_INTERVAL + max(
    MINUTE_COVERAGE_REFRESH_END_OFFSET, DAILY_COVERAGE_REFRESH_END_OFFSET
)
"""How far a coverage cagg's **content edge** may trail its raw source before
``check_coverage_freshness`` reports it stale (slice 187 D6, re-derived by
slice 169 D3).

Distinct from ``MAX_COVERAGE_SOURCE_STALENESS``, which the generic guard applies
to *bucket* lag — ``time_bucket(width, max(time))`` on raw versus
``max(time_bucket)`` on the cagg. Both sides of that comparison are bucket
starts, so no lag smaller than one bucket width is observable (see
``cagg_freshness._raw_max``). At the old 365-day width the generic check was
outright vacuous for these two views: prod returned ``is_fresh=True, lag=0``
over a 52-day staleness on 2026-08-04.

This threshold is applied to a different measurement — ``max(last_bucket)`` on
the cagg against ``max(time)`` on its source, with **no** bucket alignment.
``last_bucket`` is a content timestamp rather than a bucket start, so the lag it
yields is real rather than structural, and no bucket width can cancel it.

Derivation, computed from the constants rather than restated (D3/D5):

    COVERAGE_BUCKET_INTERVAL                              (7 days)
  + max(end_offset) over both coverage refresh policies   (4 h)
  = 7 days 4 h

**Why the bucket width replaced ``MAX_COVERAGE_SOURCE_STALENESS`` here.** The
previous derivation (1 day + 4 h) was written for the bucket-lag check and never
accounted for the open bucket. A refresh policy's window is truncated to whole
buckets, so the open bucket is never re-materialized while open — and *no*
bucket width compatible with slice 167's purpose delivers 1-day-fresh coverage
(a ~1-day bound needs a ~1-day bucket, putting ``daily_coverage`` near 280 M
rows). The old threshold was therefore unreachable by construction and fired
permanently. A permanently-firing staleness signal is indistinguishable from a
broken one and trains operators to ignore it.

So this is a **widening**, which normally deserves suspicion — the justification
is that it corrects the threshold to describe a bound the architecture actually
provides, rather than leaving it at one the architecture can never meet. It is
*derived* from the width, not chosen: a genuine stall — a dead refresh policy, a
cagg that stops materializing — still exceeds one bucket width and still fires.
Narrowing the width from 365 to 7 days cut this threshold from a nominal
1 d 4 h that never held to a real 7 d 4 h that does.

``end_offset`` is added because a policy deliberately declines to materialize
the most recent ``end_offset`` of data — that much lag is configured, not stale.
The **larger** of the two policies' offsets is used so one threshold serves both
views without firing on a healthy one. Measured on prod ``trading`` 2026-08-04,
jobs 1107/1108: ``minute_coverage`` end_offset 4 h, ``daily_coverage`` 1 h.

**Operator consequence (PM-accepted, 2026-08-13/14, provisional not permanent):**
``mt data status`` can show a ``last_bar_ts`` up to one bucket width behind
reality without the staleness banner firing. ``/api/v1/symbols`` ranges are
unaffected — slice 187 D2's per-symbol head probe reads past the coverage
horizon. The path to a genuinely good (~8 h) bound is the floor-plus-head-probe
reshape of ``bars_summary``, GitHub issue #14, out of scope here.
"""

COVERAGE_BUCKET_LAG_BUDGET: dict[str, timedelta] = {
    MINUTE_COVERAGE_VIEW: (
        COVERAGE_BUCKET_INTERVAL
        + min(MINUTE_COVERAGE_REFRESH_START_OFFSET, MAX_COVERAGE_SOURCE_STALENESS)
        + MINUTE_COVERAGE_REFRESH_END_OFFSET
    ),
    DAILY_COVERAGE_VIEW: (
        COVERAGE_BUCKET_INTERVAL
        + min(DAILY_COVERAGE_REFRESH_START_OFFSET, MAX_COVERAGE_SOURCE_STALENESS)
        + DAILY_COVERAGE_REFRESH_END_OFFSET
    ),
}
"""Per-view override of the **generic bucket-lag** staleness budget (slice 169 D3a).

``cagg_freshness._resolve_threshold`` computes
``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS) + end_offset`` and
deliberately omits a bucket-width term, because ``_raw_max`` buckets the raw
edge onto the cagg's own grid before comparing — so the structural offset
cancels exactly. **That cancellation holds only while the cagg's head bucket is
materialized.**

A refresh policy's window is truncated to whole buckets, so a cagg whose bucket
is large relative to its offsets never materializes its *open* bucket. Its
``max(time_bucket)`` then sits at the last **closed** bucket while the bucketed
raw edge sits in the **open** one, and the generic lag pins at exactly one
bucket width — permanently. Before slice 169 this did not fire only by accident:
the 365-day head bucket was written once at cagg creation, so both sides
happened to agree and the check reported ``lag=0`` over a 52-day staleness
(prod, 2026-08-04) — the false negative slice 187 D6's content-edge check was
built to work around. Narrowing the bucket removes that accident, so without
this term ``LAG_EXCEEDS_THRESHOLD`` would fire on every read of both views
forever.

    coverage bucket-lag budget = COVERAGE_BUCKET_INTERVAL
                               + min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)
                               + end_offset

**Applied per view, never globally.** The seven pre-167 caggs have no entry here
and fall back to ``_resolve_threshold``'s existing formula, untouched by
construction. Their buckets are small relative to their offsets, so the open
bucket is always inside the refresh window and the cancellation genuinely holds
— widening the budget globally would blunt a real guard on seven healthy caggs
to accommodate two exceptional ones.

Carries the **value**, not a mode switch: a boolean "open-bucket tolerant" flag
was rejected (D3a) because it encodes *why* rather than *what*, leaving the
width term to be derived somewhere else, and a second view needing a different
budget for a different reason would need a second flag.

This does **not** suppress the bucket-lag signal for these views. That would
remove a real guard (a genuinely stalled or unscheduled policy) to silence a
structural offset, and the content-edge check does not subsume it — they detect
different failures, and 168 D1's ``NOT_SCHEDULED``/``LAST_RUN_FAILED`` signals
ride the same path. A genuine stall still exceeds one bucket width and fires.
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


class FetchEntryPoint(StrEnum):
    """Which entry point drove a daemon fetch — the `via=` log marker (slice 165).

    A log-field discriminator only: nothing branches on it. StrEnum rather
    than bare str so the two valid values live in one place and a typo'd
    call site is a type error, not a silently wrong log field
    (code review 165 F002; same pattern as DailyMode above).
    """

    CYCLE = "cycle"
    """Long-running daemon cycle (`run_minute_cycle` / `run_daily_cycle`)."""

    REFETCH = "refetch"
    """Single-shot operator command (`run_minute_refetch` / `run_daily_refetch`,
    i.e. `mt data pull 1m|1d`)."""


class CycleGranularity(StrEnum):
    """Granularity tokens for daemon cycles and acquisition bookkeeping.

    These are the values stored in ``data_gaps.granularity`` and
    ``acquisition_state.granularity``, and the members of the runner's
    ``RunnerConfig.granularities`` set.

    **Not interchangeable with :class:`Granularity`**, which carries OHLCV
    *request* tokens (``"1d"``, ``"1m"``) for the query layer. The two
    vocabularies overlap conceptually and share nothing textually; conflating
    them produces a silent no-match rather than an error. Introduced by slice
    912 because these values were previously bare literals at every comparison
    site, which the project's single-definition-site rule forbids.
    """

    DAILY = "daily"
    MINUTE = "minute"


class Granularity(StrEnum):
    """Canonical granularity tokens for OHLCV data requests.

    See :class:`CycleGranularity` for the daemon/bookkeeping vocabulary; these
    are request tokens and the two are not interchangeable.
    """

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

DAILY_OHLCV_TABLE: str = GRANULARITY_SOURCE[Granularity.D1]
"""The raw daily hypertable — the source of truth the three daily rollup caggs
and ``daily_coverage`` derive from. Derived from GRANULARITY_SOURCE so the name
has exactly one definition."""

DAILY_CAGG_GRANULARITIES: tuple[Granularity, ...] = (
    Granularity.W1,
    Granularity.MO1,
    Granularity.Q1,
)
"""The three daily rollup continuous aggregates, in ascending bucket width —
the same canonical ordering as MINUTE_CAGG_GRANULARITIES. Does NOT include
``daily_coverage``: coverage is not a granularity rollup and has no Granularity
member, so callers needing the full set of caggs materializing from
``daily_ohlcv`` must add DAILY_COVERAGE_VIEW explicitly."""


@dataclass(frozen=True)
class DbSessionSettings:
    """Per-connection session settings applied by a pool's ``configure`` hook.

    Only the two values that differ between workload shapes are carried here;
    every other ``SET`` a DB class issues is a property of that class, not of
    the workload, and is deliberately not parameterized (slice 186 D1).
    """

    work_mem: str
    statement_timeout: str


DB_BULK_SESSION: Final[DbSessionSettings] = DbSessionSettings(
    work_mem="512MB", statement_timeout="300s"
)
"""Session settings for bulk and analytics paths — CLI, daemon, backfills.

These are the values every DB class has used since slice 152; naming them
changes nothing. They remain the default for every consumer that does not ask
for something else, which is what keeps the API's tighter budget (see
:data:`API_SERVING_SESSION`) from leaking into a COPY or a universe-wide scan.
"""

API_SERVING_SESSION: Final[DbSessionSettings] = DbSessionSettings(
    work_mem="64MB", statement_timeout="20s"
)
"""Session settings for the serving API's three connection pools (186 D1).

``statement_timeout`` 20s: every serving read path is sub-second to a few
seconds after slices 163/166/167 — bars ``1d`` over five years measured 2–4 s,
a cold ``/health`` coverage probe 3.19 s — and
:data:`API_MAX_BARS_PER_REQUEST` bounds the largest admitted request. 20 s is a
comfortable multiple of the worst legitimate call and still fails fast enough
to be a real limit. Operator-settable via ``MT_API_STATEMENT_TIMEOUT``.

``work_mem`` 64MB: allocated *per sort/hash/materialize node per query*, not
per connection, so the 512 MB bulk value understates rather than overstates its
ceiling across 26 pooled connections. 512 MB was chosen for bulk COPY and
universe-wide aggregation; a single-symbol windowed read sorts a bounded row
set, for which 64 MB is ample. Not operator-settable (D9).
"""

API_MAX_BARS_PER_REQUEST: Final[int] = 75_000
"""Default ceiling on the *estimated* bars a single bars request may span (D4).

Enforced before any database work by the admission check in
``api_server/routes/bars.py``; the estimate is computed from the request window
alone, so a rejected request costs one comparison. A worst-case dense response
at this ceiling is roughly 8–10 MB JSON / 3.5–4 MB msgpack.

75,000 is the agreed compromise (PM, 2026-08-03): it puts ``1m`` at ~113 days —
more than a single call needs for a three-month chart — while leaving ``5m``
and coarser effectively unbounded for normal use. Operator-settable via
``MT_API_MAX_BARS_PER_REQUEST``; this is a policy starting point, not a
commitment.
"""

INTRADAY_MINUTES_PER_TRADING_DAY: Final[int] = 960
"""Minutes of intraday coverage in a dense trading day, measured (not assumed).

Queried on prod ``trading`` 2026-08-03 for 2024-06-10: the store covers
**08:00–23:59 UTC** (16 h — EODHD US intraday includes extended hours). AAPL
returned 960 ``1m`` bars that day, SPY 724 (sparse minutes), SPY 187 ``5m``
bars against a 192-bucket ceiling. The dense case is the one an admission cap
must survive, so 960 is the input — deliberately **not** the 390-minute regular
session, which would understate a real request by 2.5x.
"""

GRANULARITY_BAR_MINUTES: Final[dict[Granularity, int]] = {
    Granularity.M1: 1,
    Granularity.M5: 5,
    Granularity.M15: 15,
    Granularity.H1: 60,
    Granularity.H4: 240,
}
"""Bucket width in minutes for the five intraday granularities.

Daily and coarser grains are excluded on purpose: their bar counts do not
derive from a minute width. See :data:`BARS_PER_TRADING_DAY`.
"""

TRADING_DAYS_PER_CALENDAR_DAY: Final[float] = 252 / 365
"""US equity trading days per calendar day — the span estimator's conversion."""

BARS_PER_TRADING_DAY: Final[dict[Granularity, float]] = {
    **{
        granularity: INTRADAY_MINUTES_PER_TRADING_DAY / minutes
        for granularity, minutes in GRANULARITY_BAR_MINUTES.items()
    },
    Granularity.D1: 1.0,
    Granularity.W1: 1 / 5,
    Granularity.MO1: 1 / 21,
    Granularity.Q1: 1 / 63,
}
"""Bars a dense symbol produces per trading day, for every granularity.

Intraday values are **derived** from
:data:`INTRADAY_MINUTES_PER_TRADING_DAY` / :data:`GRANULARITY_BAR_MINUTES`, so
correcting the measurement corrects every span limit at once. Daily and coarser
values are the calendar's own ratios. No per-granularity maximum span is
written anywhere — it is computed from this table and the live ceiling.
"""
