---
docType: slice-design
slice: minute-cagg-chunk-re-sizing
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [152, 166]
interfaces: [162, 164, 167, 182]
tools: [timescaledb]
dateCreated: 20260720
dateUpdated: 20260725
status: in_progress
---

# Slice Design: Minute-cagg chunk re-sizing + full re-materialization repair

## Overview

The four minute continuous aggregates (`minute_5min_ohlcv`, `minute_15min_ohlcv`,
`minute_hourly_ohlcv`, `minute_4hour_ohlcv`) have **two defects with one repair
path**:

1. **Over-chunked ~40×** (original scope): their materialized hypertables carry
   `chunk_time_interval ≈ 1.67 days` → ~4,236 chunks each over 22.5 years,
   versus the daily caggs' healthy 70-day / ~300-chunk shape. Root cause:
   TimescaleDB sized them at 10× `minute_ohlcv`'s then-4-hour source interval.
   Consequence: single-symbol cagg reads fan out over ~140+ chunks (~2 s,
   measured in 162 prep; `DISTINCT symbol` over the 4h cagg is 1.4 s,
   per-symbol group-by 5.7 s — measured 2026-07-20).

2. **~79% under-materialized** (urgent addition, discovered in 167 design,
   2026-07-20): slice 166's raw-table rechunk (`drop_chunks` + reinsert of all
   of `minute_ohlcv`) invalidated every materialized region; the caggs'
   trailing refresh policies (`start_offset = 1 day`) re-materialize only the
   last day and can never heal history. All four caggs uniformly hold ~21% of
   the raw bars overall (9.5–21% in measured 2019+ years, higher pre-2019). Because every cagg is `materialized_only =
   true`, consumers get the missing-row results served as truth — and the
   caggs **are** the serving path for aggregated bar reads
   (`timescale_minute_db.py` granularity dispatch, API `symbols.py`, CLI).

Both defects are fixed by the same operation: set the correct chunk interval,
then rebuild each cagg's materialization from the raw table, window by window,
compressing behind the sweep. Re-chunking a cagg re-materializes it anyway, so
folding the repair here does the full rebuild **once** (a standalone repair
slice would pay it twice — PM decision 2026-07-20, recorded in the 140 plan
and 167 design).

This slice **blocks slice 167** (cagg-backed `data_status`), which cannot
derive coverage from a cagg holding ~21% of the data.

## Measured Baseline (2026-07-20, prod `trading` DB)

PostgreSQL 17.7, TimescaleDB 2.23.0. Raw `minute_ohlcv`: **4,405,379,285 rows
(exact `count(*)`, ~1.3 s, 2026-07-20)**, 1,204 chunks @ 7 days, 78 GB
(≈ 17 bytes/row compressed — see journal 20260720 entry).

| Cagg (mat hypertable) | Interval | Chunks | Size now (~21% mat.) | Est. full, uncompressed |
|---|---|---|---|---|
| `minute_5min_ohlcv` (mat_3) | 1.67 d | 4,236 | 41 GB | **~197 GB** |
| `minute_15min_ohlcv` (mat_4) | 1.67 d | 4,236 | 15 GB | ~72 GB |
| `minute_hourly_ohlcv` (mat_5) | 1.67 d | 4,236 | 4.9 GB | ~24 GB |
| `minute_4hour_ohlcv` (mat_6) | 1.67 d | 4,235 | 1.8 GB | ~9 GB |
| *(reference)* `daily_weekly_ohlcv` (mat_7) | 70 d | 337 | 152 MB | healthy |

(Est.-full column scales current size by 1/0.208 — the measured overall
materialization fraction.)

**Materialization deficit** (identical counts across all four caggs — common
cause):

| Year | Raw rows | Cagg `SUM(minute_count)` | Coverage |
|---|---|---|---|
| 2019 | 208,673,609 | 43,440,140 | 20.8% |
| 2021 | 280,079,556 | 46,267,456 | 16.5% |
| 2024 | 362,186,695 | 55,358,082 | 15.3% |
| 2025 | 442,655,155 | 59,833,368 | 13.5% |
| 2026 | 247,389,640 | 23,483,264 | 9.5% |

Total `SUM(minute_count)` = 917,581,068 ≈ **20.8%** of the exact raw
4,405,379,285. (Per-year coverage is *lower* in recent years because raw
volume grew; pre-2019 coverage is ~28% by subtraction.)

Refresh policies (must be paused per-cagg during its sweep): jobs 1007 (5min),
1008 (15min), 1002 (hourly), 1003 (4h) — resolve IDs from
`timescaledb_information.jobs` at runtime, never hardcoded. None of the mat
hypertables currently has compression enabled (`compression_state = 0`).

**Row-count record (settled by exact count, 2026-07-20):** the raw count
bounced across three wrong figures before being measured exactly —
**4,405,379,285** (`SELECT count(*)`, metadata-assisted, ~1.3 s). The wrong
figures, for the record: ~7.27 B was `approximate_row_count` post-ANALYZE
(still ~66% high on this compressed hypertable — never authoritative); ~918 M
was `SUM(minute_count)` over the corrupted cagg (the 20.8% artifact); ~1.2 B
was an operator estimate extrapolated from the 5-min cagg — poisoned by the
same under-materialization. Corrected compressed
floor: 78 GB ÷ 4.405 B ≈ 17 bytes/row. Full story and standing discipline:
journal entry 20260720.

## Technical Decisions

### D1 — Mechanism: per-window `drop_chunks` + windowed `refresh_continuous_aggregate`, not stage-and-reinsert

A cagg's materialization is **derived data** — the raw hypertable is the
source of truth. So the rebuild does not need slice 166's stage-to-temp /
reinsert machinery or its `LOCK TABLE ... IN EXCLUSIVE MODE` discipline: no
user-written rows can be lost, and `refresh_continuous_aggregate` is the
supported writer for mat hypertables, with the invalidation machinery handling
concurrent raw-table writes structurally. (The journal's lock-before-snapshot
entry applies to source-of-truth rewrites; it deliberately does **not**
transfer here — record this reasoning, don't cargo-cult the lock.)

Per 70-day epoch-grid window, oldest → newest, one cagg at a time:

1. `drop_chunks()` on the cagg over the window (removes old 1.67-day chunks
   *and their dimension slices* — required so the refresh can create one fresh
   full-width 70-day chunk; without the drop, refresh rewrites rows into the
   existing 1.67-day chunk boundaries and no re-chunking occurs).
2. `refresh_continuous_aggregate(cagg, window_start, window_end, force => true)`
   — rebuilds the window from raw. `force => true` because the corrupted
   regions' invalidation entries were already consumed (the cagg believes it
   is up-to-date; a normal refresh no-ops — journal, cagg-collision entry).
3. `compress_chunk()` on the completed chunk (compress-behind-frontier, D3).

Windows are **grid-aligned** (1970-01-01 + k×70 d; a straddling window yields
two chunks — journal, adjacency entry). All four minute-cagg bucket widths
(5m/15m/1h/4h) divide 24 h, so 70-day windows always cover whole buckets.

**Idempotent and resumable by parity, not by bookkeeping:** a window is DONE
iff the cagg's `SUM(minute_count)` over the window equals the raw bounded
`COUNT(*)` (both now cheap post-166). The repair loop skips windows at parity
and rebuilds the rest — which also makes the same command the standing heal
for *future* raw-restructuring invalidations (D5). Interrupted runs re-derive
state from this check on the next invocation, mirroring 166's
catalog-derived window states.

**Crash-window enumeration (review F002):** `refresh_continuous_aggregate`
cannot execute inside a transaction block, so steps 1–3 commit
**independently** — there is no per-window transaction. The failure modes and
their recovery, explicitly:

- *Kill after `drop_chunks`, before refresh completes:* the window's cagg
  region is **empty** and, with `materialized_only = true`, consumers are
  served zero rows for it until repair re-runs. Parity check reads
  0 ≠ raw count → window rebuilt on next invocation. Bounded data-unavailability,
  never wrong-data.
- *Kill after refresh, before `compress_chunk`:* window is correct but
  uncompressed; parity passes, and the compression pass (or the columnstore
  policy, post-045) picks the chunk up. No rebuild needed.
- *Kill mid-refresh:* the refresh's own internal transaction rolls back or
  completes per TimescaleDB's semantics; either way parity decides on
  re-run.

**Rejected — global `TRUNCATE` + full refresh:** simpler bookkeeping, but the
cagg would serve *nothing* until the multi-hour sweep completes. Per-window
bounds the serving impact instead — but honestly stated (review F003): during
each window's drop→refresh interval, consumers of that cagg see **zero
coverage for that one 70-day window** (worse than today's ~21% for that
window, for seconds-to-minutes), and 162's coverage queries plus the 182 bars
path do read these caggs live. The availability property is therefore
*bounded per-window gaps*, not 166's never-broken-intermediate-state (which
came from stage-under-lock, deliberately not used here per this decision's
own reasoning). Operators wanting zero serving impact run the sweep outside
market hours; the trailing (correct) region and all already-repaired windows
remain served throughout. Rejected — `merge_chunks` roll-up: same adjacency
restriction as on raw (cannot merge across empty ranges; market-hours gaps
guarantee them — journal).

### D2 — Chunk interval: 70 days, from the wall-clock rule

Journal rule: interval = wall-clock span ÷ target chunk count, never data
volume. 22.5 y at 70 days → ~117 chunks per cagg (matches the healthy daily
caggs at 70 d). New constant `MINUTE_CAGG_CHUNK_INTERVAL = timedelta(days=70)`
in `constants.py` (single source of truth, citing the journal rule), rendered
into migration 044. On a **cold start** this is naturally satisfied:
TimescaleDB sizes a new cagg's mat hypertable at 10× the source interval, and
post-043 `minute_ohlcv` is 7 days → 70 days automatic; migration 044's
`set_chunk_time_interval` is then a verified no-op.

### D3 — Columnstore compression on the four mat hypertables is mandatory, not optional

The disk math forces it: full materialization uncompressed is ~300 GB total
(table above) against a cluster currently around ~200 GB — infeasible.
Migration 045 enables columnstore on all four minute caggs
(`segmentby = symbol`, `orderby = time_bucket DESC`, mirroring the raw table
and migration-042 precedent) plus a columnstore policy per cagg
(`compress_after` defined as a `constants.py` value, chosen > refresh
`start_offset` so the policy never compresses inside the actively-refreshed
head). The repair sweep compresses each window's chunk immediately after
refresh (**compress-behind-frontier**), bounding peak uncompressed footprint
to roughly one window per cagg; dropping the old ~63 GB of wrong
materialization is reclaimed progressively as windows are dropped. At the raw
table's measured ~17 bytes/row compressed floor, expect order-of ~30–40 GB
total end-state — i.e. complete-and-compressed should end *smaller* than
today's incomplete-and-uncompressed (recorded as estimate, verified in the
walkthrough).

Pre-flight includes a **disk-headroom check** on the DB host before starting
(refuse, don't warn — the 166 pre-flight pattern).

### D4 — Job-pause discipline (correctness control, per journal)

Per-cagg, for the duration of that cagg's sweep: pause its refresh policy
**and** (once 045 lands) its columnstore policy — a policy firing against a
window mid-drop/refresh is exactly the silent-loss collision in the journal.
Job IDs resolved from `timescaledb_information.jobs` at runtime; pre-flight
refuses to run if the target cagg's jobs are not paused; after resume, verify
`last_run_status = 'Success'`. The **daemon may keep running**: raw writes
only append invalidation entries, which the trailing policy heals after
resume (sweep end → resume gap is minutes; policy window is 1 day). Raw-table
jobs (e.g. columnstore 1009) are untouched — this slice never restructures
raw.

### D5 — Operator surface: `verify` and `repair` under the existing `mt data caggs` group

Slice 154 already shipped the `mt data caggs` group (`refresh`, `status`);
this slice **extends that group** with two subcommands rather than adding a
new near-collision group (review F001). Relationship to the existing
subcommands: `caggs refresh` remains the plain re-materialization wrapper for
routine use; `repair` is the restructuring sweep (drop + refresh + compress,
parity-derived state) for corruption and re-chunk scenarios; `caggs status`
gains nothing here but `verify` complements it with source-parity depth.
Per the 20260720 journal discipline, both new subcommands run every prod
query under an explicit `statement_timeout` and cancel the server-side
backend on client interrupt (review F005).

- `mt data caggs verify [--granularity 5m|15m|1h|4h|all]` — read-only parity
  report: per-year (or per-window with `--detail`) cagg `SUM(minute_count)`
  vs raw `COUNT(*)`, plus chunk-count and interval summary. This is the
  standing **detector** for the self-hiding corruption class the journal
  describes ("only a direct cagg-vs-source comparison can detect it") — it
  did not exist, which is why ~79% corruption sat unnoticed in prod.
- `mt data caggs repair [--granularity ...] [--dry-run]` — pre-flight
  (jobs paused, interval = constant via catalog, disk headroom), then the D1
  sweep. `--dry-run` prints planned windows and per-window parity states
  without mutation. Resumable and safe to kill mid-window — by **parity-derived
  state, not transactionality** (see D1's crash-window enumeration:
  `refresh_continuous_aggregate` cannot run inside a transaction block, so the
  three steps commit independently; a kill between them leaves a state the
  next invocation detects and rebuilds).

**Standing operational rule** (documented in both commands' help and the
design): after **any** raw `minute_ohlcv` chunk restructuring — including the
scheduled ~2026-07-23 `mt data rechunk` re-run for trailing legacy chunks —
run `mt data caggs verify` and, if parity fails, `mt data caggs repair`. The
parity-based done-check makes repair exactly incremental: it rebuilds only
the windows the restructuring invalidated. Sequencing of this slice vs the
07-23 re-run is therefore free (whichever runs second, verify/repair closes
the loop).

### D6 — Refresh policies unchanged; historical-write paths inherit the verify/repair rule

The trailing `start_offset = 1 day` policies are correct for steady state
(daemon writes land in the trailing day) and stay as-is — widening them to
cover history on every tick is infeasible. The structural residual: any code
path that writes *historical* raw bars (refetch, CA-drift recompute, future
backfills) creates invalidations the policies will never process. Rather than
speculative automation, this slice documents the rule (D5) and provides the
detector; wiring automatic post-write refreshes into those paths is future
work if operational experience shows the manual rule erodes.

## Data Flow (repair sweep, one cagg)

```
pre-flight: jobs paused? interval == MINUTE_CAGG_CHUNK_INTERVAL? disk headroom?
        │ (refuse on any failure)
        ▼
for each 70-day grid window, oldest → newest:
    parity check: cagg SUM(minute_count) == raw COUNT(*) over window?
        ├── equal → skip (DONE)
        └── differs:
              drop_chunks(cagg, window)          ─┐ old 1.67d chunks + slices gone
              refresh_continuous_aggregate(       │ rebuilds from raw
                  cagg, window, force => true)   ─┘ → one fresh 70-day chunk
              compress_chunk(new chunk)             (behind-frontier)
        ▼
resume jobs → verify last_run_status = 'Success'
`mt data caggs verify` → full parity report
```

## Migration Plan

- **044_minute_cagg_chunk_interval_70d** — `set_chunk_time_interval` on the
  four minute caggs' mat hypertables (resolved by view name from the catalog),
  interval rendered from `MINUTE_CAGG_CHUNK_INTERVAL`. Idempotent; cold-start
  no-op (D2). Follows 043's pattern.
- **045_minute_cagg_columnstore** — enable columnstore + add compression
  policy on the four minute caggs (D3). Follows 042's pattern;
  `requires_autocommit` as needed for policy DO-blocks.
- Migration count 46 → 48; unit test count assertions updated; cold-start
  integration run proves a fresh DB yields 70-day mat intervals and
  compression settings without the repair tool ever running.
- **Consumers:** none change — cagg names, columns, and semantics are
  unchanged; consumers simply start receiving complete data.
- The repair itself is **operational tooling** (`mt data caggs repair`), not a
  migration — same separation as 166's `mt data rechunk`.

## Cross-Slice Dependencies and Interfaces

- **Depends on [152]** (caggs exist), **[166]** (raw table healthy; 7-day
  interval is what makes 70-day cold-start sizing automatic; per-window
  rewrite pattern and journal entries this design builds on).
- **Blocks [167]** — cagg-backed `data_status` needs full materialization;
  167's hierarchical coverage cagg will hang off the re-chunked 4h cagg
  (default mat interval 10×70 d — a handful of chunks; no special handling).
- **Interfaces [162]** — coverage queries read the 4h cagg; identical results
  post-repair required (they currently read the corrupted cagg — expect
  *changed-for-the-better* results where 162's reads touched corrupted
  regions; verify explicitly).
- **Interfaces [164, 182]** — serving API and bounded-read convention inherit
  sub-100 ms single-symbol cagg reads and correct aggregates.

## Success Criteria

1. All four minute caggs at `chunk_time_interval = 70 days`, chunk count
   ~4,236 → low hundreds each (measured and recorded).
2. `mt data caggs verify` reports **full parity** (cagg `SUM(minute_count)` ==
   raw `COUNT(*)`) for every year and every granularity, within the
   trailing refresh-lag bound (≤ policy `start_offset`).
3. Single-symbol `minute_4hour_ohlcv` query: before/after EXPLAIN captured;
   ~2 s → sub-100 ms order.
4. All four mat hypertables compressed; total minute-cagg footprint recorded
   (expected order ~30–40 GB, not ~300 GB uncompressed).
5. Repair is resumable: killing mid-sweep and re-running skips completed
   windows (parity-derived) and finishes cleanly.
6. Jobs resumed with `last_run_status = 'Success'`; daemon uninterrupted
   throughout.
7. Cold start of a throwaway DB yields correct intervals + compression from
   migrations alone.
8. 162's coverage query returns correct (complete-data) results post-repair.

Effort: 3/5 (raised from the plan's 2/5 — the folded repair adds the full
re-materialization sweep, compression enablement, and the verify/repair CLI).

### Criteria audit (2026-07-25)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | 70-day intervals, chunks → low hundreds | **met** | All four at `time_interval = 70 days`; ~4,239 → **119 chunks** each (36×) |
| 2 | Full parity every year/granularity, within lag bound | **met** | 23 of 24 years digit-for-digit exact per cagg; 2026 shortfall confined to the open window with closed-window delta `0` (R5) |
| 3 | 4h single-symbol query ~2 s → sub-100 ms | **met** | ~5.2 s → **~95 ms**; plan nodes 12,721 → 238 |
| 4 | All mat hypertables compressed; footprint recorded | **met** | 119/119 compressed per cagg; total **41 GB** (expected ~30–40 GB) |
| 5 | Repair resumable | **met** | Twice: deliberate 4h kill (resumed at window 7), and an *unplanned* 5m `statement_timeout` failure at window 103/119 — 102 windows survived, no orphaned backend, re-run resumed at 103 |
| 6 | Jobs resumed with `last_run_status = 'Success'`; daemon uninterrupted | **partial** | All eight resumed and verified `scheduled = t`; daemon ran throughout. Next-scheduled-run status confirmation is next-trading-day work (D6) |
| 7 | Cold start yields correct intervals + compression from migrations alone | **met** | `test_apply_migrations_brings_schema_to_current` **passed** against a real throwaway DB: all four caggs at `70 days` with `compression_enabled` and one columnstore policy each, repair tool never invoked. Assertion proven live by mutation (expected value → 99 days ⇒ test fails) |
| 8 | 162 coverage query returns complete results | **met** | 22,687,666 symbol-days / 11,625 symbols, `ColumnarScan` plan |

Seven of eight met. Only **6** remains open, and only for its time-gated half: all
eight jobs are resumed and verified `scheduled = t`, but confirming
`last_run_status = 'Success'` after their next scheduled fire is next-trading-day work.

**Running the cold-start test requires loading `.env` explicitly.** Nothing in the
pytest path populates `os.environ` from it, so `MT_TIMESCALE_TEST_URL` is unset and the
suite **skips** — and pytest exits **0** on skips, so an automated runner reports
success while verifying nothing. Do not `source .env`: the password contains `$_`, which
the shell expands, producing an authentication failure. Use:

```bash
uv run python -c "
from dotenv import load_dotenv; import os, subprocess, sys
load_dotenv()
sys.exit(subprocess.call(['python','-m','pytest','test/integration/test_cold_start.py','-q'], env=os.environ))"
```

Expected: **2 passed, 1 skipped** (the skip is `TestMigration036WithMarketDB`, which
needs a reachable MarketDB and is unrelated to this slice).

## Verification Walkthrough (as executed, 2026-07-24/25)

Executed against prod `trading` (TimescaleDB 2.23.0 / PostgreSQL 17.7,
192.168.1.144:5432). **The acquisition daemon ran uninterrupted throughout** — no
contention was observed in `pg_stat_activity` wait events across all four sweeps.
Full evidence log: `user/notes/163-baseline-verify-20260724.md`.

Environment notes that cost time if unknown:

- Always `export PGCONNECT_TIMEOUT=10`; bare `psql` connects to this host stall
  intermittently even though the DB is healthy.
- Every ad-hoc prod query needs an explicit `SET statement_timeout`.
- The postgres MCP `execute_sql` tool hangs against this DB — use `psql`.
- The 1h cagg's view name is `minute_hourly_ohlcv`, **not** `minute_1hour_ohlcv`.

1. **Baseline capture.** `mt data caggs verify` → parity failures across every year
   and granularity (4h at 20.8% coverage, 4,238 chunks). Saved with the before-EXPLAIN.

2. **Migrations.** Applied 044/045.
   **045 failed on first apply**: it interpolated a bare `7 days` into
   `CALL add_columnstore_policy(..., after => ...)` → `syntax error at or near "days"`.
   Unit tests asserted the constant, never the rendered SQL, so they passed. Fixed to a
   typed `INTERVAL` (`_interval_seconds_sql`), regression test added, and the
   idempotency guards let the corrected re-run finish over the half-applied state.
   After: all four caggs `time_interval = 70 days`, `compression_enabled = t`.

3. **Dry run.** `repair --granularity 4h --dry-run` → 119 windows, 0 at parity, 119
   would rebuild; chunk count unchanged afterwards (zero mutation proven).

4. **Pre-flight refusal.** Ran with jobs unpaused → refused, naming both the refresh
   and columnstore job IDs with paste-ready `alter_job(..., scheduled => false)`.

5. **Repair 4h, with a deliberate interrupt.** Killed after 5 windows: no orphaned
   backend in `pg_stat_activity`, and the re-run's first action was window 7 (1–6
   skipped by parity). Full sweep: 119 windows, 6 already at parity, 113 rebuilt.

6. **The win.** Single-symbol 4h query: ~5.2 s → **~95 ms** (~55×); plan nodes
   12,721 → 238; scans became `Custom Scan (ColumnarScan)`.

7. **Remaining granularities**, each with its own job pair paused and
   **job 1003 (4h refresh) deliberately left scheduled** — see the incident below:

   | Cagg | Windows | Wall clock | Chunks after |
   |---|---|---|---|
   | 4h | 119 (113 rebuilt) | ~50 min | 119, compressed |
   | 1h | 119/119 | ~1h 22m | 119, compressed |
   | 15m | 119/119 | 3.36 h | 119, compressed |
   | 5m | 119/119 (over two runs — see below) | 4.27 h + resume | 119, compressed |

   Total minute-cagg footprint: **41 GB** (design expected ~30–40 GB).

   Per-window cost scales with raw volume **and** bucket density
   (4h: 17 s → 62 s; 1h: 59 s → 181 s; 5m: 44 s → 385 s+). Any ETA extrapolated from
   the early sparse years under-estimates substantially; scale the whole curve.

   **The 5m sweep hit our own `statement_timeout` at window 103/119** — a single
   windowed `refresh_continuous_aggregate` exceeded the 300 s
   `MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT` and the refresh `INSERT` was cancelled:

   ```
   Error: canceling statement due to statement timeout
   CONTEXT: INSERT INTO _materialized_hypertable_3 SELECT * FROM _partial_view_3 ...
   ```

   Per-window cost had climbed 268.8 s → 287.5 s → 305.7 s as the sweep reached the
   dense 2023 windows. 300 s had been sized from the 4h cagg (17–62 s/window) and
   survived 1h (max 181 s) and 15m (max ~200 s) — the ceiling was never re-checked
   against the finest granularity over the densest years. Raised to **1800 s**;
   the very next window ran 385.1 s, so the sweep would have failed a second time
   without the change.

   **Recovery required no special handling** — the failure exercised three safety
   properties at once: 102 completed windows survived, the backend-cancel-on-interrupt
   path left **no orphaned backend**, and re-running skipped straight to window 103.
   This is stronger evidence for criterion 5 than the deliberate D3 kill test, because
   it was unplanned.

   **Rule:** before a sweep, check the projected worst-case *single window* against
   `statement_timeout`, not just the projected total against the clock.

8. **Resume jobs; steady state.** All eight jobs back to `scheduled = t`.

9. **162 regression.** The coverage-index query returns 22,687,666 symbol-days across
   11,625 symbols with a `ColumnarScan` plan. Pre-repair this silently returned a
   fraction of true coverage. (23 s execution — amortized once per daemon cycle, and
   the workload slice 167 replaces; a pre-167 baseline, not a performance claim.)

10. **Post-rechunk rule rehearsal.** Deferred to the scheduled raw rechunk re-run.

### Incident encountered mid-walkthrough (and the rule it produced)

Pausing the **4h** refresh job for step 5 and leaving it paused afterwards froze the
cagg the minute daemon's coverage index reads, so the daemon re-seeded and re-pulled
recent sessions every cycle across ~349 symbols. Silent and non-corrupting, but
perpetual.

**Resuming the job was not sufficient** — all four refresh policies use
`start_offset => '1 day'`, so a resumed job heals only the most recent day and strands
the rest permanently. A manual catch-up
`refresh_continuous_aggregate('minute_4hour_ohlcv', <pause_start - 1d>, <now + 1d>)`
was required; verified closed via a per-symbol/per-day coverage diff returning zero rows.

Codified as `user/runbooks/cagg-maintenance-pausing.md` (R1–R5) and enforced by a
pre-flight check that refuses cross-granularity repair while the coverage-index cagg's
refresh policy is paused.

### Reading `verify`'s exit code

`verify` **exits 2 on any shortfall**, including benign trailing refresh lag, and cannot
distinguish lag from corruption. Criterion 2 is "parity within the trailing refresh-lag
bound", not a clean exit code. The discriminator (runbook R5) is that a lag shortfall is
confined to the open trailing window — sum parity over all *closed* windows and require
exactly `0`. Measured: 4h 84 bars, 1h 84 bars, 15m 1,211 bars, all confined to the open
window, all with closed-window delta `0`.

## Verification Walkthrough (original draft — superseded by the above)

1. **Baseline capture:** `mt data caggs verify` → shows ~21%-coverage parity failures
   across years/granularities (the corruption made visible for the first
   time). Save output.
2. **Migrations:** apply 044/045; confirm
   `SELECT ... FROM timescaledb_information.dimensions` shows 70 days for the
   four mat hypertables and compression settings exist.
3. **Dry run:** `mt data caggs repair --dry-run` → planned windows per cagg,
   parity states, no mutation.
4. **Pre-flight refusal:** run with jobs unpaused → refuses with actionable
   message. Pause jobs (IDs printed by the tool), re-run.
5. **Repair one granularity first:** `mt data caggs repair --granularity 4h`
   (smallest, ~9 GB full) — watch per-window progress; kill mid-run once
   (Ctrl-C), re-run, observe skip-to-resume; on completion
   `mt data caggs verify --granularity 4h` shows full parity.
6. **Prove the win:** EXPLAIN ANALYZE single-symbol 4h query before (saved in
   step 1 era) and after — chunk fan-out and latency collapse; chunk count
   low hundreds.
7. **Remaining granularities:** repeat for 1h, 15m, 5m; disk monitored;
   footprint recorded.
8. **Resume jobs; steady state:** jobs `Success`; next morning
   `mt data caggs verify` still at parity (trailing policy healing works).
9. **162 regression:** re-run 162's coverage query — correct results.
10. **Post-07-23 rule rehearsal:** after the scheduled raw rechunk re-run,
    `mt data caggs verify` (expect bounded parity failures in the rewritten
    window) → `mt data caggs repair` (rebuilds only those windows) → parity.
