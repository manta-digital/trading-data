---
docType: slice-design
slice: cagg-freshness-assertion-for-derived-data-readers
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [163]
interfaces: [162, 167]
dateCreated: 20260726
dateUpdated: 20260726
status: complete
---

# Slice Design: Cagg freshness assertion for derived-data readers

## Overview

Slice 163 uncovered a production defect that its own fix does not close. A
TimescaleDB refresh policy only reconsiders the last `start_offset` of data, so
**any interruption longer than `start_offset` leaves a hole that resuming the
policy never heals.** During 163's Phase D, job 1003 (the `minute_4hour_ohlcv`
refresh) was paused to allow cagg restructuring and left paused. The daemon's
coverage index reads that exact cagg, so its leading edge froze while raw
`minute_ohlcv` kept growing; `compute_missing_minute_sessions` concluded the
trailing days were missing and re-seeded gap rows every cycle across ~349 of
4,198 symbols for four days. Resuming the job was **not** sufficient — the
policy's `start_offset => '1 day'` healed only the most recent day and stranded
the rest permanently, requiring a manual windowed
`refresh_continuous_aggregate` call.

Two properties make this failure class worth its own slice rather than a
defensive one-liner:

1. **It is silent.** Each redundant re-seed is individually harmless — gap rows
   land under `ON CONFLICT DO NOTHING`, no error is raised, no data is
   corrupted. The only cost is wasted EODHD quota. Nothing in the logs looks
   wrong, so nobody goes looking. The incident was caught because the PM
   noticed anomalous chunk counts, not by any check in the system.
2. **A universe-wide `max(time)` comparison hides it.** The raw and cagg maxima
   differed by a single bucket while 349 symbols were invisible for four days.
   Detection requires per-symbol/per-day granularity, which no existing check
   performs.

163 added a `preflight()` guard, but that guard covers exactly one path: the
maintenance tool refusing to repair a cagg while the coverage-index cagg's
refresh is paused. The other causes of the same staleness — a crashed job, a
policy failing on every fire, an out-of-band `alter_job`, a server restart
mid-maintenance — never pass through maintenance tooling at all. Today they are
covered only by runbook R2's human discipline.

This slice implements the fix designed and validated against prod on
2026-07-25 (recorded as rule 3 of the tick-tier ADR, journal 20260725): a
shared `assert_cagg_fresh(conn, view_name)` helper placed in the **reader**
path, wired to its first consumer.

## Value

Removes a silent, recurring quota leak and — more importantly — closes the
failure class before the tick tier arrives. Tick data multiplies both the
volume pressure that motivates aggressive cagg use and the temptation to let
derived data inform acquisition decisions (ADR rule 2). Slice 167 is about to
add a **second** consumer of a cagg-derived coverage read (`bars_summary`); if
this guard does not exist first, 167 ships an unguarded consumer and the class
doubles instead of closing.

Placing the assertion in the reader is deliberate. Maintenance is the path
already guarded; the uncovered causes never run through it.

## Technical decisions carried in from 163

These were settled during 163's Phase D and validated against prod. The design
phase refines placement and naming, not these conclusions.

### D1 — Four OR'd staleness signals; no single one suffices

One catalog read of `timescaledb_information.jobs` + `job_stats` supplies
`start_offset`, `scheduled`, `last_run_status`, and `last_successful_finish`;
two `max()` probes supply the edges.

| Signal | Catches |
|---|---|
| `raw_max - cagg_max > threshold` | the actual 163 incident |
| `NOT scheduled` | any pause, including out-of-band `alter_job` |
| `now() - last_successful_finish > threshold` | crashed / erroring job still marked `scheduled` |
| `last_run_status <> 'Success'` | policy failing on every fire |

### D2 — The threshold needs a ceiling, not just `start_offset`

`min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)`. This is **required**, not
belt-and-braces. Simulation against the four prod policies exposed a false
negative in the naive design: the daily caggs use 21/90/**270**-day offsets, so
a daily cagg stalled 100 days passes every `start_offset`-relative check. The
threshold is uselessly loose exactly where staleness hides longest — and
because 167's `bars_summary` reads the daily caggs, this applies directly to
the next consumer.

`MAX_COVERAGE_SOURCE_STALENESS = timedelta(days=1)`, defined in the constants
module, not inline. One ceiling serves both the acquisition path and 167's
status path: a derived read older than a full trading day is stale for either
purpose, and a second per-consumer ceiling would be a tuning knob with no
distinct failure it catches. The value matches the existing
`MINUTE_STALENESS_THRESHOLD` convention (`timedelta(days=1)`) and is a full
refresh cycle above every minute policy's `start_offset`, so it never fires on
a healthy cagg. The 140-arch Constants section is amended when this lands.

### D3 — Fail-safe by refusing, never by falling back

On trip: log ERROR naming the cagg, the measured lag, and which signals fired,
then return `None`. `build_minute_coverage_index` already returns `None` on
failure and its caller already skips coverage-aware seeding, so the guard
reuses an existing fail-safe rather than inventing one.

Never fall back to a full-window seed — that reintroduces the 22-year re-seed
slice 162 exists to prevent. Prefer failing loudly and doing less work over
silently doing redundant work; silent redundancy is what made the original bug
invisible (ADR rule 4).

**Indeterminate freshness is treated as stale.** The helper adds three I/O
paths, and a guard that cannot prove freshness has not proven freshness. Each
failure mode trips the guard — same ERROR-and-refuse path as a detected lag,
with the log naming the cause rather than a lag measurement:

| Failure mode | Handling |
|---|---|
| Catalog read returns no job row for the view | Trip. A cagg with no refresh policy is never self-healing — this is the strongest form of the 163 incident, not an exemption. |
| View name is not a cagg / not in `GRANULARITY_SOURCE` | Raise `ValueError`. A programming error in the caller, not a runtime data condition; it must not be silently absorbed into a refusal. |
| Probe timeout, connection loss, or any other `psycopg.Error` | Trip. Log the exception at ERROR (`logger.exception`) and return the stale verdict; never propagate into the reader's own error path. |

Every probe runs under an explicit `statement_timeout` (per the standing prod
query discipline), sized to a small multiple of the measured probe cost so a
hung catalog or edge query degrades to a refusal rather than stalling the
caller. Note the raw-edge probe is a bounded `max(time)` index scan, not an
expression aggregate over compressed chunks — it is not the query shape behind
the 2026-07-20 incident — but the timeout applies regardless.

### D4 — Detect and refuse; do not auto-remediate

An automatic catch-up `refresh_continuous_aggregate` from inside the daemon's
read path would make a heavy write the side effect of a read. Catch-up stays
with runbook R2 as an operator action.

### D5 — Generalized helper, not an inlined check

`assert_cagg_fresh(conn, view_name)` lives in a shared maintenance module and
resolves views through `GRANULARITY_SOURCE` rather than hardcoding names (the
163 `preflight()` precedent). Granularity-agnostic so 167 consumes it
unchanged.

Probe cost measured on prod: **~0.19 s** for the cagg edge, **~0.75 s** for
raw, both planning-dominated — negligible against a once-per-cycle index build
already costing ~23 s.

### D6 — The verdict is cached with a short TTL

~1 s per call is free on the acquisition path (once per daemon cycle against a
~23 s build) but is the entire budget on the read path. The parent
architecture's NFR is "view latency stays sub-second at full-universe scope"
(140-arch:150) — the NFR slice 167 exists to reach, and 167 gates it with a
CI-enforced load test. An uncached synchronous probe would consume that budget
by itself and put this guard in direct conflict with its second consumer.

The helper therefore memoizes its verdict per view name for
`CAGG_FRESHNESS_CACHE_TTL` (constants module; 60 s), returning the cached
verdict without probing while it is warm. Properties that make this safe:

- **Bounded staleness of the verdict itself.** The TTL is two orders of
  magnitude below `MAX_COVERAGE_SOURCE_STALENESS`, so a cached verdict can
  never mask a lag the uncached check would have caught.
- **Fail-safe direction preserved.** A *stale* verdict is cached on the same
  terms as a fresh one; the cache never converts a refusal into a pass.
- **Both consumers benefit without special-casing.** The daemon pays the probe
  once per cycle as before (its cycle far exceeds the TTL, so it always
  probes); `bars_summary` pays it at most once per TTL across all symbols in a
  full-universe view, amortizing to ~0 per read.

Cache state is process-local and keyed by view name only — no connection or
transaction identity — so it must not be consulted for maintenance decisions.
This helper is a reader-path guard; 163's `preflight()` remains the
(uncached, always-probing) maintenance guard.

## Scope

- `assert_cagg_fresh(conn, view_name)` in a shared maintenance module, with the
  D6 TTL verdict cache.
- `MAX_COVERAGE_SOURCE_STALENESS` and `CAGG_FRESHNESS_CACHE_TTL` in the
  constants module.
- Wire the first consumer: `build_minute_coverage_index`
  (`src/manta_trading/data/gaps/minute_coverage.py`).
- Induced-staleness integration test (see success criteria).

Out of scope: auto-remediation (D4); a freshness check inside maintenance
tooling (163's `preflight()` already covers that path); 167's `bars_summary`
wiring, which is 167's own work using this helper.

## Success criteria

1. **Staleness is induced, not mocked.** On a throwaway DB: pause a refresh
   policy, advance raw past `start_offset`, and observe the guard trip, the
   daemon skip seeding, and the ERROR name the cagg and the measured lag. A
   test that only asserts the helper was *called* does **not** satisfy this
   criterion.
2. Each of the four D1 signals trips independently, verified in isolation.
3. The 270-day false negative is pinned by a regression test that **fails**
   when the ceiling is removed.
4. A fresh, healthy cagg passes with no false positive; the daemon's normal
   seeding path is unchanged.
5. Probe overhead stays within the measured envelope (~1 s total) against a
   once-per-cycle index build.
6. The helper is granularity-agnostic — exercised against a minute cagg and a
   daily cagg without signature change — so 167 consumes it as-is.
7. Re-running the 163 incident shape (pause job 1003, let raw advance) produces
   a refusal instead of a re-seed.
8. **The TTL cache is correct in both directions**: a warm call issues no
   probe queries (pinned by query count, not by timing); a cached *stale*
   verdict still refuses; the entry expires after
   `CAGG_FRESHNESS_CACHE_TTL` and re-probes. Repeated calls across a
   full-universe read amortize to well under the sub-second consumer NFR.

## Verification walkthrough (executed 2026-07-26)

Every step below was run at Phase 6 and its actual output recorded. Reproducible
by an external agent with `MT_TIMESCALE_DB_URL` set.

**Isolation note.** The draft said "throwaway DB". The shipped mechanism is the
`test_rechunk_driver.py` precedent instead: a **scratch hypertable with its own
cagg and its own refresh policy**, built and dropped per test against
`MT_TIMESCALE_DB_URL`. There is no separate test URL in use, and criterion 1
requires pausing a refresh policy — so the tests pause the *scratch* policy and
never touch a production job. `assert_cagg_fresh` grew a keyword-only
`source_table` seam for exactly this (production callers omit it and resolve
through `GRANULARITY_SOURCE`).

```bash
export PGCONNECT_TIMEOUT=10
export MT_TIMESCALE_DB_URL=$(grep MT_TIMESCALE_DB_URL .env | sed 's/^[^=]*=//' | tr -d '"')
```

`.env` values are double-quoted; without `tr -d '"'` psql silently falls back to
the local socket.

### 1-4, 6. Induced staleness, each signal, healthy path

```bash
uv run pytest test/integration/test_cagg_freshness.py -q
```

**Actual: `11 passed in 4.69s`.** Covers criterion 1 (pause the scratch policy,
advance raw past `start_offset`, assert refusal), criterion 2 (each signal in
isolation), criterion 4 (healthy scratch cagg passes), criterion 6 (minute-shaped
and daily-shaped caggs through one unchanged signature), and 8.3a (an
over-timeout probe returns a bounded `PROBE_FAILED` with no orphaned backend).

Unit-level signal isolation and cache behavior:

```bash
uv run pytest test/unit/market/test_cagg_freshness.py -q     # 46 passed
uv run pytest test/unit/data/gaps/test_minute_coverage.py -q # 15 passed
```

### 5. The ceiling regression actually fails when the ceiling is removed

Replace `min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)` with `start_offset`
in `_resolve_threshold`, then:

```bash
uv run pytest test/unit/market/test_cagg_freshness.py -q -k "270 or ceiling or min_of_offset"
```

**Actual: `5 failed, 3 passed`**, including
`test_daily_cagg_stalled_100_days_is_stale_despite_270_day_offset`. Restore with
`git checkout src/manta_trading/market/maintenance/cagg_freshness.py`;
`46 passed` again.

### 7. Probe cost and no false positives on production caggs

```bash
uv run python -c "
import os, psycopg, time
from manta_trading.market.maintenance.cagg_freshness import _evaluate
views = ['minute_5min_ohlcv','minute_15min_ohlcv','minute_hourly_ohlcv','minute_4hour_ohlcv',
         'daily_weekly_ohlcv','daily_monthly_ohlcv','daily_quarterly_ohlcv']
with psycopg.connect(os.environ['MT_TIMESCALE_DB_URL'], autocommit=True) as conn:
    for v in views:
        t = time.monotonic(); verdict = _evaluate(conn, v); el = time.monotonic() - t
        print(f'{v:26} fresh={verdict.is_fresh} lag={verdict.lag} thr={verdict.threshold} {el:.2f}s')
"
```

**Actual (2026-07-26):**

| cagg | fresh | lag | threshold | probe |
|---|---|---|---|---|
| `minute_5min_ohlcv` | True | 0:00:00 | 1d 0:05:00 | 0.95s |
| `minute_15min_ohlcv` | True | 0:00:00 | 1d 0:15:00 | 0.48s |
| `minute_hourly_ohlcv` | True | 0:00:00 | 1d 1:00:00 | 0.40s |
| `minute_4hour_ohlcv` | True | 0:00:00 | 1d 4:00:00 | 0.37s |
| `daily_weekly_ohlcv` | True | 0:00:00 | 8d | 2.14s |
| `daily_monthly_ohlcv` | True | 31d | 31d | 1.14s |
| `daily_quarterly_ohlcv` | True | 0:00:00 | 91d | 1.10s |

All seven fresh — no false positive (criterion 4). Probe cost 0.37-2.14 s, within
the ~1 s envelope for the minute caggs that this slice's only consumer reads
(criterion 5); the daily caggs are slower but are read once per call by 167, not
by the daemon.

**Caveat for 167:** `daily_monthly_ohlcv` passes at lag 31d against a 31d
threshold — zero margin. This is structural (its policy's `end_offset` is 30
days, so the current month is deliberately unmaterialized), not a defect, but it
is the one cagg where a genuine stall takes longest to surface via the lag
signal. The other three signals still cover it.

## Corrections discovered during implementation

Four findings from the induced-staleness tests changed the design. All are code
changes in this slice, and each is covered by a test that fails without the fix.

1. **Bucket width must be cancelled, not budgeted for.** Comparing raw
   `max(time)` to cagg `max(time_bucket)` reports the bucket width itself as
   lag, because `time_bucket` is the bucket *start*. Measured on prod before the
   fix: `daily_weekly` 4d, `daily_monthly` 42d, `daily_quarterly` 72d — all
   healthy, all tripping the 1-day ceiling, which would have broken 167. The raw
   edge is now bucketed to the cagg's own grid in SQL (`time_bucket(%s::interval,
   max(time))`), so the structural offset cancels and D2's threshold stays
   `min(start_offset, ceiling)` as designed. Bound as a parameter so
   variable-width `1 mon` / `3 mons` buckets align via PostgreSQL rather than
   Python interval arithmetic. `end_offset` **is** added to the threshold — that
   is data a policy deliberately declines to materialize.
2. **`SET LOCAL statement_timeout` is a no-op under autocommit.** Each statement
   is its own transaction, so the setting is discarded before the next statement
   runs. Verified on PG 17.7: `SET LOCAL` then `SHOW` returns `0`, and a
   `pg_sleep(2)` under a 100 ms `SET LOCAL` runs to completion. Every probe was
   therefore unbounded — the exact failure D3 exists to prevent, and invisible to
   task 4.1a, which only asserted the statement was *issued*. Now plain `SET`,
   restored to `DEFAULT` after probing. Test 8.3a pins the live behavior.
3. **`last_successful_finish` is `-infinity` for a never-run policy**, which
   psycopg cannot load into a `datetime` — it raises `DataError` mid-fetch, which
   would surface as `PROBE_FAILED` and refuse reads on every freshly-created
   cagg. Normalized to NULL in SQL.
4. **A never-run policy is not stale.** A policy created moments ago on an
   already-materialized cagg reports NULL `last_successful_finish` and NULL
   `last_run_status` — the cold-start shape every new cagg passes through.
   Signalling on it would refuse reads on healthy new caggs; the lag signal
   covers actual currency without depending on job history. Conversely, an
   **unmeasurable** edge now refuses rather than passing: an empty raw table
   yields `PROBE_FAILED`, and a cagg with no rows against a populated raw table
   yields `LAG_EXCEEDS_THRESHOLD` (maximal lag, not absent lag).

## Cross-slice dependencies and interfaces

- **Dependencies: [163]** — design origin and the prod validation this slice
  implements. 163's `preflight()` remains; it guards the maintenance path while
  this guards the reader path.
- **Interfaces: [162]** — `build_minute_coverage_index` is 162's seeding path;
  this slice adds a precondition, not a behavior change.
- **Interfaces: [167]** — 167's D3a requires this guard. Sequencing this slice
  first reduces 167's D3a to "consume the guard; do not ship an unguarded
  consumer." **The slice plan is authoritative on sequencing:** 167 lists
  `dependencies: [166, 163, 168]`, a hard dependency, so 167 does not start
  until this slice lands. The "167 builds the helper itself" fallback
  previously carried here and in 167's D3a is removed — under the plan that
  scenario cannot occur, and keeping it invited a second implementation of a
  guard whose whole value is being shared.
- 167's `bars_summary` reads on the sub-second NFR path (140-arch:150). D6's
  TTL cache is what keeps this guard inside that budget; 167 consumes the
  helper as-is and does not need its own amortization scheme.

## Code review disposition (20260726)

Review `reviews/168-review.code...md` (z-ai/glm-5.2, CONCERNS) raised three
findings. Two fixed; one deliberately declined.

- **F001 (concern) — fixed.** `assert_cagg_fresh`'s `now` seam only governed
  cache TTL; `_evaluate` called the module-level `_now()` directly, so a caller
  passing a custom clock silently got narrower control than the signature
  implied. `now` is now threaded into `_evaluate` and used for the
  `LAST_SUCCESS_TOO_OLD` comparison. Pinned by
  `test_now_seam_reaches_the_staleness_evaluation_not_just_the_cache`, verified
  to fail when the pass-through is reverted.
- **F003 (note) — fixed.** `_EvalConnection` used a falsy check
  (`x if x else _NOW`), so a test explicitly passing
  `last_successful_finish=None` could not produce the cold-start shape the
  production code branches on. Replaced with an explicit `_UNSET` sentinel, and
  two tests now exercise that path: a never-run policy is fresh, but still trips
  on lag.
- **F002 (concern) — declined, deliberately.** The module is 569 lines against
  the ~300 guideline. Roughly 250 are executable; the balance is docstrings and
  the four incident write-ups recorded above (`SET LOCAL` under autocommit,
  `-infinity`, bucket-width cancellation, cold start). The proposed
  `cagg_probes.py` extraction was evaluated and rejected on scope: it leaves the
  remaining module at ~320 lines — still over the guideline — while requiring
  import churn across all 49 unit tests for zero behavior change. The line count
  here is documentation-driven, not complexity-driven: the module has one job
  and one public entry point, and the comments it carries are the slice's most
  durable artifact. **PM decision: file length over guideline is acceptable when
  the excess is not code complexity.** Revisit only if a second concern lands in
  this module, or if the executable portion itself grows past the guideline.

## Notes

- Design rules: journal 20260725 ADR, rule 3 (`start_offset` is a maintenance
  budget; long pauses are not self-healing) and rule 4 (silent-and-harmless is
  the hardest failure to find).
- Runbook `user/runbooks/cagg-maintenance-pausing.md` R2 documents the manual
  catch-up this slice detects the absence of. R2 stays authoritative for
  remediation.
