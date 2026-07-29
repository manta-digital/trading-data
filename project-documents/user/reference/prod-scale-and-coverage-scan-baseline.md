---
docType: reference
project: trading-data
dateCreated: 20260728
dateUpdated: 20260728
status: active
---

# Production Scale & Coverage-Scan Baseline (audited 2026-07-28)

Ground truth measured directly against the production `trading` DB
(192.168.1.144:5432/trading) on 2026-07-28, during slice 165 verification.
Purpose: eliminate guessing and re-derivation of database scale, cagg
topology, and coverage-scan cost. Supersedes any figure inferred from
`trading_test` or from `approximate_row_count`.

## Row counts (exact, with measured query time)

| Object | Exact rows | Count time | Notes |
|---|---:|---:|---|
| `minute_ohlcv` (raw) | 4,414,650,928 | 1.54 s | was 4,405,379,285 at the ~2026-07-22 audit; +9.3M from ongoing backfill |
| `minute_5min_ohlcv` (cagg) | 1,359,008,945 | 0.20 s | |
| `minute_4hour_ohlcv` (cagg) | 68,207,735 | 0.16 s | coverage-scan source |
| `minute_coverage` (cagg-over-cagg) | 102,770 | 0.07 s | year-grain; see below |
| distinct symbols in 4h cagg | 11,625 | 2.44 s | |
| coverage-scan output (symbol, day) pairs | 22,687,901 | 18.46 s | see baseline section |

- **`COUNT(*)` on `minute_ohlcv` completes in ~1.5 s.** The slice 166 rechunk
  win (25,256 → 1,205 chunks) is intact. If this count ever takes minutes or
  times out again, something has genuinely broken — that is the regression
  signal, not slow coverage scans.
- **`approximate_row_count('minute_ohlcv')` returned 7,402,614,812 — +68%
  over reality.** Catalog stats are stale/inflated. Do not use approximate
  counts for this table; the exact count is cheap.

## Chunk topology (healthy as of this audit)

| Hypertable | Chunks |
|---|---:|
| `minute_ohlcv` | 1,205 |
| `_materialized_hypertable_3/4/5/6` (5min/15min/hourly/4h caggs) | 119 each |
| `_materialized_hypertable_222` (`minute_coverage`) | 24 |
| `_materialized_hypertable_223` (`daily_coverage`) | 66 |
| `_materialized_hypertable_7/8/9` (daily weekly/monthly/quarterly) | 337/336/258 |

All four minute caggs are real, finalized TimescaleDB continuous aggregates
(`timescaledb_information.continuous_aggregates`, `finalized = t`), all with
`materialized_only = t` (real-time aggregation OFF — queries never touch raw).
All refresh policies were running on schedule with `last_run_status = Success`
and 0 recent failures (jobs 1002/1003/1007/1008/1107/1108).

Note: TimescaleDB caggs do NOT appear in `pg_matviews` — that catalog lists
only classic Postgres materialized views. Checking `pg_matviews` proves
nothing about caggs; use `timescaledb_information.continuous_aggregates`.

## Cagg grains and purposes

- `minute_4hour_ohlcv`: `time_bucket('04:00:00')` over raw `minute_ohlcv`.
  OHLCV + `minute_count` per symbol per 4h bucket. Coverage-scan source.
- `minute_coverage` (slice 167): `time_bucket('8760:00:00')` — **1-year
  buckets** — over `minute_4hour_ohlcv` (cagg-over-cagg). Columns: bars,
  first_bucket, last_bucket per symbol-year. Built for sub-second
  `data_status`. Refresh policy start_offset = 750 days.
  **It is year-grain and CANNOT substitute for the day-grain coverage index
  that gap seeding needs.** There is no day-grain materialization anywhere;
  day-grain coverage exists only by grouping the 4h cagg at query time.
- `daily_coverage` (slice 167): same year-grain shape over `daily_ohlcv`.

## Coverage-scan baseline (the important one)

The query (`build_minute_coverage_index`, `data/gaps/minute_coverage.py`):

```sql
SELECT symbol, date_trunc('day', time_bucket)
FROM minute_4hour_ohlcv
GROUP BY symbol, date_trunc('day', time_bucket)
```

Run under `SET LOCAL statement_timeout = '30s'`
(`MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT`), once per daemon minute cycle,
and (slice 165) once per `mt data pull 1m` invocation.

| When | Runtime | Output pairs | Context |
|---|---:|---:|---|
| slice-162 prep (~2026-07-15) | ~3.05 s | 2,425,433 | recorded in 162 design §"Measured on the production DB" |
| 2026-07-28 (this audit) | 18.46 s | 22,687,901 | **server-side aggregation only** (`COUNT(*)` wrapper via psql) |
| 2026-07-28 (this audit) | **152.2 s** | 22,687,901 | **end-to-end app path**: aggregation + streaming all rows to psycopg + Python dict build |
| 2026-07-28 07:09 | >30 s (timed out) | — | inside `mt data pull 1m`, pre-165 timeout |
| 2026-07-28 18:18 | >90 s (timed out at 91.2 s) | — | daemon path after a first 90s timeout bump — see below |

**This is NOT a regression.** Output cardinality grew 9.4× (2.4M → 22.7M
symbol-day pairs) because the minute backfill has been filling decades of
history continuously since slice 162 shipped. The hash-aggregate's output
cardinality drives both the server cost AND — the larger term — the wire
transfer.

**Sizing trap (learned the hard way, same day):** `statement_timeout`
counts until the server finishes *sending* rows, not just computing them.
A `COUNT(*)`-wrapped benchmark (18.46s) measures aggregation only; the real
`build_minute_coverage_index` call transfers every pair and measured
**152.2s end-to-end**. A first timeout bump to 90s — sized from the 18.46s
number — failed in production at 91.2s. Timeout values for this query must
be sized from the end-to-end measurement. Slice 165 set
`MINUTE_COVERAGE_INDEX_STATEMENT_TIMEOUT = 300s` (measured 152.2s + plateau
~1.5× pair growth + load variance).

**Trajectory:** growth converges (history bounded at 2004, universe ~11.6k
symbols; post-backfill growth ≈ 11.6k pairs/day). Plateau estimate ~30–40M
pairs → ~200–270s end-to-end; 300s covers it. On timeout, both the daemon
cycle and `pull 1m` fall back safely per the slice 162 fail-safe (ERROR
log, `None` index, no coverage-aware seeding that cycle) — safe but means
coverage-aware seeding silently stops happening.

**Follow-up options (issue #3 tracks this):**
1. ~~Raise the timeout~~ — done in slice 165 (30s → 300s, measured-based).
2. Day-grain coverage cagg over `minute_4hour_ohlcv`: fixes the aggregation
   term but **NOT the transfer term**, which dominates (134 of 152 s). Only
   worth doing combined with (3) or (4).
3. Per-symbol day-grain reads (slice 165 added `build_symbol_minute_coverage`
   for `run_minute_refetch`; the daemon loop could adopt the same shape —
   162's ~2.0s/symbol rejection predates the 163 rechunk and is stale).
4. Compact wire format for the universe build (e.g.
   `array_agg(DISTINCT ...::date) GROUP BY symbol` → 11.6k rows with binary
   date arrays instead of 22.7M rows) — attacks the transfer term directly.

## trading_test is NOT representative — do not infer from it

Verified 2026-07-28: `trading_test` has ~10 symbols vs 11,625; 26.8M minute
rows vs 4.41B; its `minute_*_ohlcv` "caggs" are **plain SQL views** (zero
rows in cagg catalog), so `assert_cagg_fresh` probes fail and every
coverage-aware code path falls back. Its `instruments` table is empty. Any
performance number, plan shape, or behavioral conclusion drawn from
`trading_test` is meaningless for production. Use it only for "does the SQL
parse / does the code path execute" smoke checks.

## Asides observed during the audit (unverified causes)

- `acquisition_state` daily granularity: latest `updated_at` 2026-07-24 —
  daily acquisition apparently idle for ~4 days while minute backfill runs
  (53 minute-symbol updates in the hour before this audit). May be expected
  (steady-state bulk mode) — not investigated.
