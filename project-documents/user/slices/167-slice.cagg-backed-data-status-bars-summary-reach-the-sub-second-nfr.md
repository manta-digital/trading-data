---
docType: slice-design
slice: cagg-backed-data-status-bars-summary-reach-the-sub-second-nfr
project: trading-data
parent: user/architecture/140-slices.data-quality-operations.md
dependencies: [166, 163]
interfaces: [147, 182]
dateCreated: 20260720
dateUpdated: 20260720
status: not_started
---

# Slice Design: Cagg-backed `data_status` bars summary — reach the sub-second NFR

## Overview

Slice 166 re-chunked `minute_ohlcv` and brought a single-symbol MIN/MAX from
10m47s to 0.68s, but the full-universe `data_status` read only improved
117.2s → 7.8s — still ~8× over the 140-arch NFR ("View latency stays
sub-second at full-universe scope"). The residual cost is **structural**: the
view's `bars_summary` CTE scans and groups the entire raw `minute_ohlcv`
hypertable (plus `daily_ohlcv`) on every read. No amount of raw-table
chunk tuning removes a full per-symbol aggregate over ~4.4B minute rows.

This slice rewrites `bars_summary` to derive `first_bar_ts / last_bar_ts /
bars_stored` per symbol from **continuous aggregates** instead of the raw
tables, preserving the view's exact column contract so `mt data status` and
every other consumer is unchanged, and documents the resulting cagg-lag
staleness bound.

## Value

**Operational:** `mt data status` at full-universe scope becomes usable
interactively (sub-second, not ~8s). Every operator status check and
verification walkthrough benefits.

**Architectural:** closes the last leg of the 140-arch `data_status` NFR that
has been open since slice 142. Makes the view's latency **structurally
independent** of how the underlying bar tables are chunked — a durable fix,
not a tuning that can silently regress.

## Measured Baseline (captured 2026-07-20, prod `trading` DB, design phase)

Environment: PostgreSQL 17.7, TimescaleDB 2.23.0.

| Fact | Value |
|---|---|
| `data_status` full-universe read (post-166) | 7.8 s |
| NFR target | sub-second |
| Raw `minute_ohlcv` authoritative row count | **4,405,379,285** exact (see note) |
| Raw `daily_ohlcv` row count | 34,223,492 |
| `minute_4hour_ohlcv` cagg row count | 7,761,587 (5,871 symbols) |
| Minute `bars_summary` via 4h cagg group-by | 5.7 s |
| Daily `bars_summary` raw group-by | 3.8 s |

**Row-count note (settled by exact count, 2026-07-20):** the raw count is
**4,405,379,285** — exact `SELECT count(*)`, metadata-assisted, ~1.3 s
post-166. Three earlier figures were all wrong, each from a source that
looked authoritative: ~7.27B was `approximate_row_count` post-ANALYZE (still
~66% high on this compressed hypertable); ~918M was `SUM(minute_count)` over
the corrupted cagg (the ~21% materialization artifact, see §Critical
prerequisite); ~1.2B was an operator estimate extrapolated from the 5-min
cagg — poisoned by the same under-materialization.
Corrected compressed floor: 78 GB ÷ 4.405B ≈ 17 bytes/row. Standing rule
(journal 20260720): exact `count(*)` is the only authoritative row-scale
source; once slice 163 repairs the caggs and parity is verified,
`SUM(minute_count)` becomes a valid fast cross-check.

## Critical prerequisite (discovered 2026-07-20): minute caggs are ~79% under-materialized

> **This finding was discovered during 167's design phase and materially
> changes the slice. It is documented here per PM instruction; the slice will
> return to it. The cagg *repair* itself is being folded into slice 163 (cagg
> re-chunking), which must run before 167.**

While measuring the cagg-backed approach, the design phase found that **all four
minute continuous aggregates** (`minute_5min_ohlcv`, `minute_15min_ohlcv`,
`minute_hourly_ohlcv`, `minute_4hour_ohlcv`) are materialized with only ~21%
of the raw bars they should contain (9.5–21% across measured 2019+ years,
~28% pre-2019 by subtraction), spanning the entire 2004–2026 range:

| Year | Raw `minute_ohlcv` | Cagg `SUM(minute_count)` | Coverage |
|---|---|---|---|
| 2019 | 208,673,609 | 43,440,140 | 20.8% |
| 2021 | 280,079,556 | 46,267,456 | 16.5% |
| 2024 | 362,186,695 | 55,358,082 | 15.3% |
| 2025 | 442,655,155 | 59,833,368 | 13.5% |
| 2026 | 247,389,640 | 23,483,264 | 9.5% |

All four caggs report the *identical* materialized count per period, confirming
a common cause.

**Root cause:** slice 166's rechunk (`drop_chunks` + reinsert of the entire raw
`minute_ohlcv`) invalidated every materialized cagg region. The refresh
policies use `start_offset => INTERVAL '1 day'` (trailing), so on each run they
only re-materialize the last day — they **cannot self-heal history**. This is
the exact failure mode recorded in the merge-chunks adjacency lesson: *cagg
refresh during restructuring silently loses materialized rows; repair only via
`refresh_continuous_aggregate(..., force => true)` over the full range.*
The refresh jobs are running successfully (job 1003, 1708 runs, 0 failures) —
they are simply not scoped to repair history.

**Impact:** this is a live production integrity issue **independent of 167** —
any consumer reading the 4h / hourly / 15m / 5m rollups today gets aggregates
computed over ~21% of the data. No data is being lost or mis-written (the *raw*
table is intact and is what the daemon and the current `data_status` view
read), but the caggs are silently wrong.

**Decision (PM, 2026-07-20): fold the repair into slice 163.**
Re-chunking a cagg invalidates and re-materializes it regardless, so a
standalone repair slice would run the full re-materialization now and slice 163 would
re-refresh them again during its re-chunk — paying the full materialization
twice. Folding the `refresh_continuous_aggregate(..., force => true)` repair
into 163 does it once, as an intrinsic part of correctly restructuring the
caggs. **167 therefore depends on [166, 163]:** 167 cannot back `bars_summary`
with the 4h cagg (directly or hierarchically) until 163 has repaired it to full
materialization. Slice 163's plan entry should be treated as now-urgent for
this reason.

## Technical decisions

### D1 — Structure: hierarchical coverage cagg (Option 1)

Pointing `bars_summary` at the existing `minute_4hour_ohlcv` and grouping by
symbol is **5.7 s** — still over the NFR — because that cagg is itself
over-chunked (4,235 chunks; the same proliferation slice 163 addresses). A
per-symbol `GROUP BY` must Append-scan all 4,235 chunks with a partial
HashAggregate each. Even `SELECT DISTINCT symbol` over it is 1.4 s.

**Decision:** introduce a **hierarchical continuous aggregate**
`minute_coverage` built *over* `minute_4hour_ohlcv` with a wide (1-year) time
bucket, plus an analogous `daily_coverage` over `daily_ohlcv`:

```
minute_coverage  (cagg over minute_4hour_ohlcv):
  time_bucket('1 year', time_bucket) AS yr_bucket,
  symbol,
  SUM(minute_count) AS bars,
  MIN(time_bucket)  AS first_bucket,
  MAX(time_bucket)  AS last_bucket
  GROUP BY yr_bucket, symbol

daily_coverage   (cagg over daily_ohlcv, analogous, SUM(day_count) / COUNT)
```

`minute_coverage` materializes to **~15,195 rows** (5,871 symbols × ~22
years). `bars_summary` then groups *that*:

```
bars_summary AS (
  SELECT 'minute' AS granularity, symbol,
         MIN(first_bucket) AS first_bar_ts,
         MAX(last_bucket)  AS last_bar_ts,
         SUM(bars)         AS bars_stored
  FROM minute_coverage GROUP BY symbol
  UNION ALL
  <analogous daily branch over daily_coverage>
)
```

Grouping ~15k rows is sub-millisecond, **regardless of the 4h cagg's chunk
count**. This is the durability argument: the NFR holds structurally, not
contingent on chunk-health tuning that can regress.

**Rejected — Option 2 (group `minute_4hour_ohlcv` directly):** smaller surface
(no new cagg, just a view CTE rewrite) but only sub-second *after* 163
re-chunks the 4h cagg, and re-couples the NFR to the exact chunk-proliferation
problem slice 166 spent its effort breaking. If the 4h cagg ever drifts back
toward over-chunking, `data_status` silently regresses past the NFR again.
Option 1 is immune to that.

### D2 — Column contract preserved exactly

The view's output columns are unchanged: `first_bar_ts`, `last_bar_ts`,
`bars_stored` keep their names, types, and positions. Only the *source* of the
three `bars_summary` columns changes (cagg-derived instead of raw-scan). All
consumers — `mt data status` (`status_queries.py`, `status_table.py`),
`migrate_cold_start.py`'s verification, and any external reader — see identical
shape. `status_table.py` renders `first_bar_ts`/`last_bar_ts` via `_fmt_date`
(**date-only**, `%Y-%m-%d`) and `bars_stored` via `_fmt_int`; nothing in the
health logic (`gap_count`, `has_retry_exhausted`, `last_attempt_ts`) touches
`bars_summary`, so the rewrite cannot alter any `health` classification.

### D3 — Timestamp fidelity and staleness semantics (to document in the view)

**Bucket truncation:** `first_bar_ts`/`last_bar_ts` derive from `MIN`/`MAX` of
the 4h cagg's `time_bucket`, so they are truncated to the **4-hour bucket
start**, not the true first/last raw bar timestamp. The 1-year coverage bucket
does *not* coarsen this — `MIN/MAX(time_bucket)` are carried through as
aggregates, so fidelity is identical to reading the 4h cagg directly. For US
equities the earliest session bar (~09:30 ET = 13:30/14:30 UTC) buckets to
12:00 UTC (same UTC date), so the **date-only display is unaffected** in
practice. This bound must be stated in the view's doc comment.

**Cagg lag:** a cagg can only *lag* raw, never lead it. Coverage may understate
the very latest bars by at most:
`(minute_4hour_ohlcv refresh interval) + (minute_coverage refresh interval)` —
a two-hop bound because the coverage cagg is hierarchical. With the 4h cagg
refreshing hourly and the coverage cagg on a daily (or hourly) policy, this
bounds understatement of `bars_stored` / `last_bar_ts` to well under a day for
the trailing edge only; all settled history is exact. **The exact numeric bound
and the refresh-policy intervals chosen must be documented in the view's doc
comment** so operators reading `mt data status` understand that a just-fetched
symbol may show slightly-stale coverage until the next refresh tick.

### D4 — Refresh policy for the coverage caggs

`minute_coverage` and `daily_coverage` each get an
`add_continuous_aggregate_policy`. Because they are hierarchical (built over
another cagg), their `start_offset` must be wide enough to re-materialize
recently-changed parent buckets — the trailing-1-day policy is what caused the
prerequisite corruption, so the coverage policy's `start_offset` must be chosen
deliberately (candidate: cover at least the parent's refresh window plus a
margin) and **must be re-verified against the merge-chunks/cagg-invalidation
lesson** before any future restructuring of the parent. Exact offsets are a
task-level decision, recorded here as a constraint, not yet fixed.

### D5 — Load-test tier (revisiting slice 166 D2's deferral)

Slice 166 recorded the deferral of the NFR load-test tier to whichever slice
lands the rewrite — this one. The NFR ("sub-second at full-universe scope") is
a latency assertion on a full-universe read, which the python rules place in
`tests/load/` (latency/throughput/resource bounds, not functional
correctness). **Decision to confirm with PM at task-breakdown:** add one load
test asserting full-universe `data_status` read latency < 1 s against a
realistic-scale fixture (or a gated prod-shaped tier), so the NFR has
regression coverage. Functional equivalence (output identical to raw-scan
modulo the documented lag bound) is covered by integration tests, not the load
tier.

## Data flow

```
raw minute_ohlcv ──(4h refresh policy)──▶ minute_4hour_ohlcv
                                                │
                              (coverage refresh policy)
                                                ▼
                                         minute_coverage  (~15k rows)
                                                │
raw daily_ohlcv ──▶ daily_coverage             │
                          │                     │
                          └────────┬────────────┘
                                   ▼
                       data_status.bars_summary (groups ~15k+ rows)
                                   ▼
                        mt data status  (unchanged output shape)
```

## Migration plan

- **Source of truth:** `MINUTE_OHLCV_CHUNK_INTERVAL` pattern from slice 166 —
  any interval/offset constants centralized in `constants.py`, not inlined.
- **New migration (044+):** create `minute_coverage` and `daily_coverage`
  continuous aggregates + their refresh policies (each `CREATE MATERIALIZED
  VIEW` its own `execute()`, `requires_autocommit: True`, following the
  established `034_create_daily_caggs` / policy-add pattern in `minute.py`).
- **View rewrite migration (045+):** `CREATE OR REPLACE VIEW data_status` with
  the cagg-backed `bars_summary`, via the existing
  `_build_data_status_view_sql(...)` builder (add a variant/flag rather than
  duplicating the SQL string), preserving all other CTEs and columns verbatim.
  Re-uses the migration-021 DO-block / `to_regclass` branching convention so
  cold-start and existing DBs converge.
- **Consumer updates:** none — the column contract is preserved (D2).
- **Behavior verification:** before/after full-universe read timing; row-by-row
  equivalence of `data_status` output (raw-scan vs cagg-backed) modulo the
  documented lag bound (D3); cold-start applies cleanly to the new migration
  count.

## Cross-slice dependencies and interfaces

- **Depends on [166]** — the raw-table re-chunk that this builds on.
- **Depends on [163]** — 163 repairs (force-refresh) and re-chunks the minute
  caggs; 167 cannot back `bars_summary` with a corrupted 4h cagg. 163 is now
  urgent (§Critical prerequisite).
- **Interfaces [147]** — `mt data status` reads `data_status`; contract
  preserved.
- **Interfaces [182]** — serving API's available-ranges / status surfaces read
  the same view; contract preserved.

## Success criteria

1. Full-universe `data_status` read is **sub-second** on prod `trading` DB.
2. `data_status` output is **identical** to the current raw-scan version for
   all settled history, and differs only within the documented cagg-lag bound
   for the trailing edge.
3. `mt data status` output shape (columns, formatting) is **unchanged**.
4. The view carries a doc comment stating the bucket-truncation and cagg-lag
   bounds and the chosen refresh intervals (D3).
5. Cold-start applies the new migrations cleanly and yields a sub-second view
   on a freshly-built DB.
6. A load test asserts full-universe read latency < 1 s and is CI-gated (D5).

## Verification walkthrough (draft — refined at Phase 6)

1. **Prove the prerequisite is met** (163 ran): confirm the 4h cagg is fully
   materialized — `SELECT SUM(minute_count) FROM minute_4hour_ohlcv` matches
   the raw count within the lag bound (not ~21%).
2. **Timing before/after:** `\timing on`; `SELECT count(*) FROM data_status;`
   — record sub-second vs the 7.8 s baseline.
3. **Equivalence:** diff a snapshot of `data_status` (all columns) taken
   against the raw-scan view vs the cagg-backed view for a sample of covered,
   partially-covered, and empty symbols; assert equality modulo trailing-edge
   lag.
4. **Contract unchanged:** `mt data status` and `mt data status --symbol AAPL`
   render identical column layout to pre-slice output.
5. **Cold-start:** throwaway DB → run all migrations → `data_status` returns
   rows and is sub-second.
6. **Lag bound honesty:** fetch new bars for a symbol, immediately read
   `data_status`, confirm coverage understates by at most the documented bound
   and converges after the next refresh tick.
