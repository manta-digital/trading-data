---
docType: notes
project: trading-data
slice: 170
dateCreated: 20260811
dateUpdated: 20260811
---

# Slice 170 — `daily_ohlcv` rechunk execution record (2026-08-11)

Production run on `192.168.1.144`, PostgreSQL 17.7 / TimescaleDB 2.23.0.
Baselines captured per task C3, results per Phase D. Every prod query ran
with an explicit `statement_timeout`.

## Pre-flight (C1)

- Daemon stopped and **verified**, not assumed: `daemon_heartbeat` empty,
  newest `acquisition_state.last_attempt_ts` = 2026-08-08 16:37:39 (i.e. no
  activity in the preceding 15 minutes), zero non-idle backends on `trading`
  besides the verifying session.
- Backup: operator cold-copied the data directory with PostgreSQL **stopped**,
  then restarted the service. (The earlier 2026-08-10 copy was taken against a
  running server and is not a valid backup — see slice 915.)
- Migration ledger before: 52 applied, 1 pending (050).

## Baselines (C3) → results (D1/D2)

| Measure | Before | After | Note |
|---|---|---|---|
| Chunks (total / compressed) | 3,372 / 3,370 | **341 / 339** | 9.9x reduction |
| `SELECT MAX(time)` | 4.92 s | **0.157 s** | 31x faster; Criterion 2 met |
| 31k-symbol `ANY` EXPLAIN (plan only) | **>120 s, could not finish** | **7.70 s** | Criterion 3 met |
| `count(*)` | 65,652,505 | **65,652,505** | exact match; Criterion 4 met |
| Dimension interval | 7 days | **70 days** | migration 050 |

### Per-symbol integrity (C3.3 → D2.2)

Bound as `timestamptz` (binding a `date` defeats chunk exclusion). Window
`2015-01-01` ≤ `time` < `2026-01-01`. All four matched **exactly** on both
count and volume sum:

| Symbol | count | sum(volume) |
|---|---|---|
| AAPL | 2,766 | 308,130,610,600 |
| MSFT | 2,766 | 78,239,020,262 |
| SPY | 2,766 | 238,100,213,300 |
| IBM | 2,766 | 13,257,218,779 |

## The C2.3 stop condition — window count 338, not ~118

The dry run reported **338 windows**, 2.9x the design's expectation. Task C2.3
makes this an explicit stop-and-report condition, so the run was halted and
the cause diagnosed before proceeding.

**The grid assumption was sound; the design's span input was wrong.**

- Actual chunk span: **1961-12-27 .. 2026-08-12 = 23,604 days = 64.6 years**
- Actual data span: 1962-01-01 .. 2026-08-05
- 23,604 / 70 = 337.2 → **338 windows**. The arithmetic is exactly right.
- The design assumed 22.6 years, reusing `minute_ohlcv`'s 2004-onward EODHD
  horizon. Daily history goes back to **1962**.
- All 3,372 chunks were exactly 7 days wide (one distinct width), so the
  70 = 10 × 7 nesting property held perfectly.

Decision (PM, go): proceed at 70 days. It delivers the ~10x reduction and
preserves the exact nesting that makes the rewrite provably safe. Re-deriving
to ~200 days to hit the original ~120-chunk target would **not** nest into
7-day chunks (200 = 28.57 × 7), reintroducing the grid-alignment hazard
slice 166 warned about — a bad trade for a marginal planner gain.

**Row count was also stale in the design**: 65.65 M actual vs ~34.7 M stated,
nearly 2x. Both figures should be corrected wherever they are restated.

## Job pause/resume (C4, C6)

Job IDs resolved from the catalog at runtime — **the runbook's table is stale**:
there is no job 1003. The 4h minute refresh is now **job 1124**. This is
exactly why C4.1 forbids trusting the documented IDs.

Paused at **2026-08-11T22:23:08Z** (5 jobs), resumed after the run:

| Job | Policy | Target |
|---|---|---|
| 1010 | columnstore | `daily_ohlcv` |
| 1108 | refresh | `daily_coverage` |
| 1125 | refresh | `daily_weekly_ohlcv` |
| 1126 | refresh | `daily_monthly_ohlcv` |
| 1127 | refresh | `daily_quarterly_ohlcv` |

The daily caggs' mat hypertables carry **no** columnstore policies, so that
clause of D5 resolved to nothing. All 10 minute-family jobs stayed scheduled
throughout (R1 held) and continued running successfully during the window.
Post-resume: **zero** jobs left unscheduled (R4 satisfied).

## The run (C5)

`mt data rechunk --table daily`, exit **0**. Runtime **~16 minutes**
(16:19 → 16:35 local).

- 338 windows: **337 rewritten**, 0 compress-only, **1 skipped** (trailing
  uncompressed, 2026-07-16..2026-09-24, 4 chunks), 0 already done.
- **Every window collapsed to exactly 1 chunk**: 336 windows from 10 chunks,
  1 window from 8 (the earliest partial window). Zero exceptions.
- 65,458,765 rows moved; the 193,740 difference from the total is the skipped
  trailing window.
- The staged==reinserted guard passed on every window (any failure would have
  aborted that window's transaction and stopped the run).

## Cagg refresh (C6.2/C6.3) — large under-materialization healed

Full-span `force => true` refresh, ~7 minutes total:

| Cagg | Refresh time | Rows before | Rows after | Delta |
|---|---|---|---|---|
| `daily_weekly_ohlcv` | 229.3 s | 7,307,828 | 13,882,972 | **+6,575,144** |
| `daily_monthly_ohlcv` | 94.0 s | 1,701,508 | 3,243,857 | **+1,542,349** |
| `daily_quarterly_ohlcv` | 65.1 s | 576,756 | 1,106,603 | **+529,847** |
| `daily_coverage` | 17.3 s | 153,329 | 300,919 | **+147,590** |

These deltas are the point, not a side effect: the daily caggs were roughly
**half-materialized** before this run. Their refresh policies look back at most
270 days, so a scheduled run can never heal 64 years of history — the slice 163
lesson, confirmed again. `daily_coverage` used the R2a form (NULL bounds;
365-day buckets reject narrow windows).

## Acceptance gate (D3) — R5 closed-window parity

Per design D6, acceptance is the R5 discriminator, **not** `mt data caggs
verify` exit codes. Sum parity strictly before the newest bucket boundary:

| Cagg | Parity | Verdict |
|---|---|---|
| `daily_weekly_ohlcv` | **0** | PASS |
| `daily_monthly_ohlcv` | **0** | PASS |
| `daily_quarterly_ohlcv` | **0** | PASS |

Criterion 5 met.

`mt data caggs verify` reports FAIL rows — but that command checks the
**minute** family, which this slice never touched. Its 99.9–100% figures are
the known pre-existing trailing-lag/under-materialization condition that
**slice 169** addresses. Confirmed unrelated: `minute_ohlcv` still has a 7-day
interval and 1,207 chunks (1,203 post-166 plus 4 new daemon-created chunks),
and its refresh jobs ran successfully throughout this window.

## Minute-path regression check (D4) — Criterion 7

- `mt data rechunk --dry-run` (no `--table`) still plans **`minute_ohlcv`**:
  1,180 windows, 1,176 already done, 2 to rewrite + 2 to compress-only. Those
  4 are new daemon-created chunks — the trailing-window follow-up slice 166
  left open, not a regression.
- `minute_ohlcv` dimension interval unchanged at 7 days.

## Caveats

- **`approximate_row_count('daily_ohlcv')` is badly wrong**: 1,443,446,308 vs
  exact 65,652,505 (**+2,099%**), measured immediately after a successful
  `ANALYZE` (3.1 s). This is the same known TimescaleDB defect already recorded
  for `minute_ohlcv` (+68% there), not something this rechunk caused. **Do not
  use it for verification**; use exact counts.
- The 1 skipped window (2026-07-16..2026-09-24) will be rewritten on a later
  re-run once its chunks compress — the same trailing-window pattern as 166.
  A re-run is a safe no-op for every completed window.
- `daily_coverage` content staleness was healed here as a side effect, but the
  **policy defect remains** (the current 365-day bucket is never refreshed), so
  staleness re-accrues. **Slice 169 is still required.**
