---
docType: notes
layer: project
project: trading-data
slice: minute-cagg-chunk-re-sizing
audience: [human, ai]
description: Slice 163 prod evidence log — pre-repair cagg-vs-raw parity baseline and 4h EXPLAIN, then Phase D migration/dry-run/repair execution results
dateCreated: 20260724
dateUpdated: 20260725
status: in_progress
---

# Slice 163 — Pre-repair parity baseline (prod trading DB)

Captured: 2026-07-24 (read-only `mt data caggs verify`).
Prod: TimescaleDB 2.23.0 / PostgreSQL 17.7 @ 192.168.1.144:5432/trading.
Daemon left running throughout (verify is read-only).

## verify --granularity 4h (per-year)

```
4h (minute_4hour_ohlcv) — 4238 chunks @ 1 day, 16:00:00 — overall coverage 20.8%
— PARITY FAILURE
┏━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┓
┃ Year ┃ Raw         ┃ Cagg       ┃ Coverage ┃ Parity ┃
┡━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━┩
│ 2003 │ 7,326,952   │ 3,032,011  │ 41.4%    │ FAIL   │
│ 2004 │ 64,958,577  │ 26,491,598 │ 40.8%    │ FAIL   │
│ 2005 │ 74,313,333  │ 28,266,436 │ 38.0%    │ FAIL   │
│ 2006 │ 105,571,108 │ 36,318,423 │ 34.4%    │ FAIL   │
│ 2007 │ 106,320,291 │ 32,499,293 │ 30.6%    │ FAIL   │
│ 2008 │ 118,463,487 │ 34,268,511 │ 28.9%    │ FAIL   │
│ 2009 │ 120,034,618 │ 34,540,858 │ 28.8%    │ FAIL   │
│ 2010 │ 123,251,339 │ 35,047,262 │ 28.4%    │ FAIL   │
│ 2011 │ 156,170,268 │ 43,253,916 │ 27.7%    │ FAIL   │
│ 2012 │ 126,784,176 │ 35,679,772 │ 28.1%    │ FAIL   │
│ 2013 │ 135,471,593 │ 36,344,946 │ 26.8%    │ FAIL   │
│ 2014 │ 145,643,950 │ 37,160,497 │ 25.5%    │ FAIL   │
│ 2015 │ 190,464,634 │ 46,083,899 │ 24.2%    │ FAIL   │
│ 2016 │ 165,125,825 │ 38,820,586 │ 23.5%    │ FAIL   │
│ 2017 │ 173,613,862 │ 39,052,298 │ 22.5%    │ FAIL   │
│ 2018 │ 188,803,840 │ 40,365,416 │ 21.4%    │ FAIL   │
│ 2019 │ 200,679,871 │ 41,742,544 │ 20.8%    │ FAIL   │
│ 2020 │ 296,919,943 │ 54,136,682 │ 18.2%    │ FAIL   │
│ 2021 │ 271,855,093 │ 44,534,359 │ 16.4%    │ FAIL   │
│ 2022 │ 288,843,212 │ 45,115,413 │ 15.6%    │ FAIL   │
│ 2023 │ 305,703,985 │ 48,506,430 │ 15.9%    │ FAIL   │
│ 2024 │ 346,954,164 │ 53,003,286 │ 15.3%    │ FAIL   │
│ 2025 │ 516,863,671 │ 69,346,294 │ 13.4%    │ FAIL   │
│ 2026 │ 181,309,928 │ 14,029,538 │ 7.7%     │ FAIL   │
└──────┴─────────────┴────────────┴──────────┴────────┘

STANDING RULE: after ANY raw minute_ohlcv chunk restructuring (e.g. `mt data 
rechunk`), run `mt data caggs verify`; if parity fails, run `mt data caggs 
repair` (rebuilds only the invalidated windows).
Error: Parity failure detected — run `mt data caggs repair`.
```

## Key findings (4h cagg, representative of all four — common cause)

- **Chunk shape:** 4238 chunks @ ~1.67 days (`1 day 16:00:00`) — the ~40x over-chunking defect.
- **Overall coverage: 20.8%** of raw (~79% under-materialized), matching the slice-163 design baseline exactly.
- Per-year coverage declines from ~41% (2003) to 7.7% (2026) as raw volume grew while the trailing 1-day refresh policy healed only the head.
- Every year FAILs parity. Exit code 2 (parity-failure detector fires).
- 2019: raw 200,679,871 vs design's 208,673,609 — minor drift is expected (the raw table has continued acquiring bars since the 2026-07-20 design snapshot; daemon still running).

## Notes

- Per-window raw `COUNT(*)` ~1.2s, cagg `SUM` ~60ms; full 4h verify ~2.5 min (117 windows).
- The other three caggs (5m/15m/1h) share the identical deficit (common cause: the slice-166 raw rechunk invalidated all four). Full four-cagg verify deferred to Phase D to bound prod query load pre-repair.

## EXPLAIN ANALYZE — single-symbol 4h read (BEFORE repair)

Query: `SELECT * FROM minute_4hour_ohlcv WHERE symbol='AAPL' ORDER BY time_bucket;`

Two runs captured (cold then warm cache); the **cost is dominated by planning**,
which the chunk fan-out inflates regardless of cache:

| Run | Plan nodes | Planning Time | Execution Time |
|---|---|---|---|
| Cold cache | ~12,721 | **3,201 ms** | 1,964 ms |
| Warm cache | ~12,721 | **1,434 ms** | 162 ms |

- **~12,721 plan nodes** — one index scan per 1.67-day chunk (thousands), the fan-out the slice eliminates. The top node is `Custom Scan (ChunkAppend) on _materialized_hypertable_6`.
- **Planning Time 1.4–3.2 s** dominates — the chunk-exclusion planner touches ~438k buffers just to plan. This is the number the 70-day rechunk collapses (from ~4,238 chunks to ~117).
- Execution Time is cache-sensitive (162 ms warm, ~2 s cold — the design's "~2 s" is the cold figure from 162 prep).
- Success criterion 3 target: after the rechunk, planning + execution collapse toward sub-100 ms order. Paired after-number captured in Phase D task D4.

---

# Phase D — prod execution (2026-07-25)

## D1: migrations 044/045 applied

All four minute caggs: `time_interval = 70 days`, `compression_enabled = t`.
Eight jobs present — 4 refresh (1002/1003/1007/1008) + 4 columnstore (1018-1021),
`compress_after = 168:00:00` (7 days, from MINUTE_CAGG_COMPRESS_AFTER).

**Defect found and fixed during apply** (commit 842e27a): migration 045 interpolated
`_interval_literal()` (bare `7 days`) into `CALL add_columnstore_policy(..., after => ...)`,
producing `after => 7 days` → `SyntaxError: syntax error at or near "days"`. Unit tests
asserted the constant's value but not the rendered SQL, so they passed. Fixed to use
`_interval_seconds_sql()` (typed `INTERVAL '604800 seconds'`); regression test added.
The failure left 045 partially applied (5m columnstore enabled, no policies); the
idempotency guards let the corrected re-run complete cleanly with no manual cleanup.

## D2: dry run + pre-flight refusal

- Dry run (4h): **119 windows, 0 at parity, 119 would rebuild**. Chunk count unchanged
  at 4,238 afterwards — zero mutations confirmed.
- Pre-flight refusal with jobs unpaused (walkthrough step 4):

```
Error: Pre-flight refused: refusing to repair minute_hourly_ohlcv: background
job(s) still scheduled — job 1020 (policy_compression); job 1002
(policy_refresh_continuous_aggregate). Pause them first: SELECT alter_job(1020,
scheduled => false); SELECT alter_job(1002, scheduled => false); (job ids: 1020, 1002)
```

  Correctly caught **both** job types, including the columnstore policy that only
  exists post-045 — job IDs resolved from the catalog at runtime, never hardcoded.

## D3: 4h repair — kill/resume exercise (success criterion 5)

- Sweep rate ~17-20 s/window on prod.
- Killed mid-sweep after 5 windows. **No orphaned backend** left in `pg_stat_activity`.
- Re-run's first action was **window 7** — windows 1-6 skipped via parity check with no
  manual intervention. Resumability is parity-derived, exactly per design D1.

### Interim chunk-shape confirmation (mid-sweep)

Chunk count falling 4,238 → 3,122 as windows collapse (~36 old chunks → 1 per window).
Oldest repaired chunks:

```
   start    |    end     |  width  | is_compressed
------------+------------+---------+---------------
 2003-12-03 | 2004-02-11 | 70 days | t
 2004-02-11 | 2004-04-21 | 70 days | t
 2004-04-21 | 2004-06-30 | 70 days | t
```

Both defects (over-chunking, under-materialization) fixed by the one sweep, and
compress-behind-frontier is working — chunks land compressed.

### Unexpected 47 GB DB shrink mid-sweep — explained (not caused by repair)

Database fell 148 GB → 101 GB while only the 4h sweep was running. Investigated
rather than assumed; the 4h cagg accounts for only ~0.2 GB of it. Breakdown:

| Cagg | Before Phase D | Mid-sweep | Change | Swept? |
|---|---|---|---|---|
| `minute_5min_ohlcv`  | 41 GB    | 7,075 MB | −34 GB | no |
| `minute_15min_ohlcv` | 15 GB    | 4,822 MB | −10 GB | no |
| `minute_hourly_ohlcv`| 4,859 MB | 2,550 MB | −2.3 GB | no |
| `minute_4hour_ohlcv` | 1,830 MB | 1,616 MB | −0.2 GB | yes |

**Cause:** migration 045 enabled columnstore and installed compression policies
(jobs 1018-1020) on all four caggs. Those three policies were left *scheduled* and
fired against the existing (still wrong-interval, still ~21%-materialized) chunks,
compressing them in place.

**Verified harmless:**
- Chunk counts unchanged for the three (4,239 each) — compression only, no restructuring.
- `minute_5min_ohlcv` 2019 `SUM(minute_count)` = **43,440,140**, byte-identical to the
  design's pre-repair baseline → compression is lossless, no data moved.
- Daemon still writing normally (last minute attempt 1.5 s old).

**Consequence:** incidental headroom gain, not a cost. Those chunks get dropped and
rebuilt by each cagg's own sweep regardless. Reinforces design D4: each cagg's
columnstore policy **must** be paused before its sweep — the pre-flight already
enforces this (it refused on job 1020 in D2).

Note the 4h cagg is *smaller* than before (1,616 MB vs 1,830 MB) while holding far
more materialized data — early evidence for success criterion 4.

## D3 result: 4h cagg repaired (119 windows — 6 at parity, 113 rebuilt)

Sweep runtime ~50 min. Per-window cost scales with raw volume: ~17 s (2003, 7 M rows)
→ ~62 s (2024, 73 M rows). The 6-at-parity count is the killed run's 5 completed
windows + 1 whose refresh committed before the kill — resume arithmetic exact.

### Post-repair verify (success criterion 2)

**23 of 24 years at exact 100.0% parity** — cagg `SUM(minute_count)` equals raw
`COUNT(*)` digit-for-digit (e.g. 2019: 200,679,871 = 200,679,871).

2026 reports 99.9% (raw 182,238,619 vs cagg 182,055,968, gap 182,651). Confirmed to be
**trailing refresh lag, not corruption** — the gap is isolated to the single currently-open
window; all earlier windows including three 2026 windows are exact:

```
 window_start |   raw    |   cagg
 2025-11-14   | 86876422 | 86876422   <- exact
 2026-01-23   | 99306357 | 99306357   <- exact
 2026-04-03   | 92163534 | 92163534   <- exact
 2026-06-12   | 37061683 | 36879032   <- open window, daemon still writing
```

Newest raw bar 2026-07-24 07:30 vs newest cagg bucket 2026-07-24 06:00 — one 4-hour
bucket behind. Heals when refresh job 1003 is resumed (D6). Raw 2026 also grew
181,309,928 → 182,238,619 since yesterday's baseline: **daemon ran uninterrupted
throughout the sweep**, as design D4 intends.

Success criterion 2 met: full parity every year *within the trailing refresh-lag bound*.

## D4: the query win (success criterion 1 + 3)

Same query as the before-baseline: `SELECT * FROM minute_4hour_ohlcv WHERE symbol='AAPL' ORDER BY time_bucket;`

| Metric | Before | After | Improvement |
|---|---|---|---|
| Chunks | 4,238 @ ~1.67 d | **119 @ 70 d** (118 compressed) | 36x fewer |
| Plan nodes | ~12,721 | **238** | 53x fewer |
| Planning Time | 1,434-3,201 ms | **66-74 ms** | ~20-43x |
| Execution Time | 162-1,964 ms | **21 ms** | ~8-92x |
| Cold-path total | ~5.2 s | **~95 ms** | **~55x** |

Scan nodes changed from per-chunk `Index Scan` to `Custom Scan (ColumnarScan)` over
compressed chunks. Chunk count 119 matches the design's ~117 prediction.

4h cagg size: 1,830 MB (21% materialized, uncompressed) → **~1.6 GB (100%, compressed)**
— complete-and-compressed is smaller than incomplete-and-uncompressed, per design D3.

## Incident: paused 4h refresh job caused a perpetual minute re-pull loop (2026-07-25)

**Discovered by the PM from daemon behavior**, not by our verification: the minute
daemon was re-pulling large chunk counts on symbols that should have been complete
(e.g. AUTL, 21 chunks).

### Root cause — ours

`build_minute_coverage_index` reads **`minute_4hour_ohlcv`**
(`src/manta_trading/data/gaps/minute_coverage.py`, via `GRANULARITY_SOURCE[Granularity.H4]`).
Job **1003** (4h refresh) was paused in D2 for the repair sweep and **left paused after
D3 completed**. With the cagg's leading edge frozen at its last run (2026-07-24 23:44)
while raw kept growing, `compute_missing_minute_sessions` saw the trailing days as
genuinely missing and re-seeded gap rows for them every cycle.

AUTL: raw had 2,032 distinct days, the 4h cagg only 2,028. The four missing days were
exactly 2026-07-21 → 07-24 — the paused-job window.

Universe-wide exposure before the fix:

```
    day     | symbols invisible to coverage index
------------+-------------------------------------
 2026-07-20 |    79
 2026-07-21 |   349
 2026-07-22 |   349
 2026-07-23 |   346
 2026-07-24 |   298
```

Bounded to ~349 of 4,198 symbols because 1003's final run materialized most of the
universe before going idle. **Non-corrupting** — bars re-insert via
`ON CONFLICT (symbol, time) DO NOTHING` — but perpetual, and growing one day per day.
The wasted cost is EODHD API calls.

### Second-order finding: resuming the job is NOT sufficient

All four minute refresh jobs use `start_offset => '1 day'`:

```
 job_id | schedule_interval |                         config
--------+-------------------+--------------------------------------------------------
   1002 | 01:00:00          | {"end_offset": "01:00:00", "start_offset": "1 day", ...}
   1003 | 01:00:00          | {"end_offset": "04:00:00", "start_offset": "1 day", ...}
   1007 | 00:05:00          | {"end_offset": "00:05:00", "start_offset": "1 day", ...}
   1008 | 00:15:00          | {"end_offset": "00:15:00", "start_offset": "1 day", ...}
```

The resumed schedule would have healed only 2026-07-24 and left 07-21 → 07-23
**stranded permanently** — the re-pull loop would have continued silently. Any cagg
pause longer than the job's `start_offset` requires a manual catch-up refresh.

### Remediation applied to prod

```sql
SELECT alter_job(1003, scheduled => true);
SELECT alter_job(1021, scheduled => true);
CALL refresh_continuous_aggregate('minute_4hour_ohlcv', '2026-07-19', '2026-07-25');
```

Verified: all 8 jobs `scheduled = t`; the raw-vs-cagg day-coverage diff returns
**0 rows** for every day since 2026-07-01. Re-seed loop closed.

### Standing constraint for D5 (new — not anticipated by the design)

**Job 1003 must remain scheduled throughout the 1h/15m/5m sweeps.** The coverage index
depends only on the 4h cagg, so pausing the other three pairs (1002+1020, 1008+1019,
1007+1018) is safe. Pausing 1003 during a long sweep would re-open this loop for the
sweep's full duration.

`preflight()` in `cagg_repair.py` currently has no guard against this — it refuses when
the *target* cagg's jobs are scheduled, but says nothing about 1003 being unscheduled
while a *different* granularity is repaired. Follow-up candidate.

### Also confirmed non-issues

- **Daemon symbol order wrapping Z→A is correct.** `iter_active_instruments` uses
  `most_stale_first`: `ORDER BY s.last_attempt_ts ASC NULLS FIRST, i.symbol ASC`
  (`src/manta_trading/data/acquisition/symbols.py`). Symbol is only the tiebreaker; a
  strict A→Z sweep would indicate the ordering was broken.
- **AUTL's re-pull itself was legitimate** — it fetched real missing sessions and
  correctly deleted all its gap rows afterward (0 rows remain, 477,141 bars).

## D5: 1h cagg repaired (2026-07-25)

Jobs 1002 + 1020 paused for the sweep; **job 1003 deliberately left scheduled**
(runbook R1 — it feeds the daemon coverage index). Daemon ran throughout.

`mt data caggs repair --granularity 1h --assume-headroom-gb 1050`

**119 windows — 0 already at parity, 119 rebuilt.** Exit 0, ~1h 22m wall clock
(115.7 min of logged per-window time).

Chunks: **~4,239 → 119, all 119 compressed** — same 36x reduction as the 4h cagg.

### Parity after repair

23 of 24 years digit-for-digit exact. 2026 shows an 84-bar shortfall, localized to
prove it is trailing lag rather than corruption:

```
window 2026-05-07..07-16 : raw 77,941,663  cagg 77,941,663  EXACT
window 2026-07-16..open  : raw  4,772,083  cagg  4,771,999  DIFF 84
```

Raw newest `2026-07-24 07:30` vs cagg newest bucket `2026-07-24 07:00` — one hourly
bucket behind while the daemon keeps writing. Same signature as the 4h run; heals now
that job 1002 is resumed.

**`verify` exits 2 on this.** That is correct — it cannot distinguish trailing lag from
corruption, so the operator must. Success criterion 2 is "parity within the trailing
refresh-lag bound", *not* a clean exit code. Note for the D8 criteria audit.

Jobs 1002 + 1020 resumed; all 8 back to `scheduled = t`.

### Per-window cost scales with raw volume

```
2020 windows ~59s   2022 ~87s   2024 ~122s   2026 ~181s
```

Same curve the 4h run showed (17s → 62s). Any ETA extrapolated from the early, sparse
years will under-estimate; scale the whole curve, not the front of it.

## D5: 15m cagg repaired (2026-07-25)

Jobs 1008 + 1019 paused; **1003 left scheduled** (runbook R1). Daemon ran throughout.

**119 windows — 0 already at parity, 119 rebuilt.** Exit 0, **3.36 h** of logged
per-window time. Chunks **~4,239 → 119, all 119 compressed**.

Parity: 23 of 24 years digit-for-digit exact. 2026 short by 1,211 bars
(182,445,457 raw vs 182,444,246 cagg) — trailing lag, larger than the 4h/1h runs'
84 bars only because finer buckets leave more of the open bucket uncovered.
**Closed-window delta verified = 0** (runbook R5), confirming every closed window exact.

### Chaining automation aborted on a shell parsing bug (not a data problem)

A script chaining 15m → verify → 5m aborted with
`closed-window delta = SET0`. `psql -tAc "SET statement_timeout=...; SELECT ..."`
echoes `SET` on its own stdout line; piping straight into `tr -d '[:space:]'` glues it
to the number, producing `SET0`, which fails an `== "0"` compare.

The gate **failed closed** — it refused to launch a 5-hour sweep on a value it could not
parse, which is the correct behavior. But the bug was avoidable and is the **same class**
as the migration 045 `INTERVAL` failure in D1: *code that parses real output was never
tested against real output*. The gate's SQL had been validated against the 1h cagg
(returned `0`); the shell parsing around it had not.

Fix: `| grep -vx 'SET' | tail -1 | tr -d '[:space:]'`. Verified to yield `0` and pass.

**Rule:** when scripting the R5 gate, strip the `SET` echo explicitly, or issue the
timeout via `PGOPTIONS` instead of an in-band statement.

## D5: 5m cagg repaired (2026-07-25) — and the statement_timeout failure

Jobs 1007 + 1018 paused; **1003 left scheduled** (runbook R1). Daemon ran throughout.

### Run 1 failed at window 103/119

```
minute_5min_ohlcv window 102/119 2023-04-13..2023-06-22 rebuilt (raw 57,532,937, 305.7s)
WARNING cagg_parity: cancelled server-side backend pid=274305 after interrupt
Error: Database error: canceling statement due to statement timeout
CONTEXT:  SQL statement "INSERT INTO _timescaledb_internal._materialized_hypertable_3
          SELECT * FROM _timescaledb_internal._partial_view_3 AS I
          WHERE I.time_bucket >= $1 AND I.time_bucket < $2 ;"
```

102 windows completed (4.27 h) before a single windowed
`refresh_continuous_aggregate` exceeded `MINUTE_CAGG_MAINTENANCE_STATEMENT_TIMEOUT`.

**Why the ceiling was wrong.** 300 s was sized from the 4h cagg (17–62 s/window) and
held for 1h (max 181 s) and 15m (max ~200 s). Per-window cost scales with raw volume
**and** bucket density, so the binding case is the *finest granularity over the densest
years* — which was never measured before the value was fixed. The approach curve was
visible in the log for three windows (268.8 → 287.5 → 305.7 s) and was read as an ETA
input rather than as a ceiling approach.

Raised to **1800 s** (commit `081225c`). Validated immediately: window 104 ran
**385.1 s**, so run 2 would have failed again at the very next window without the change.

### Recovery: three safety properties held simultaneously

- **102 windows survived** — parity-derived state, nothing to roll back
- **No orphaned backend** — `_TimeoutConnection`'s cancel-on-interrupt path fired
  (`cancelled server-side backend pid=274305`) and `pg_stat_activity` was clean
- **Resume skipped straight to window 103** — no operator input, no flags
- The chaining script **refused to run verify** on a sweep that exited non-zero

This is stronger evidence for success criterion 5 than the deliberate D3 kill, because
nothing about it was staged.

**Rule:** before a sweep, check the projected **worst-case single window** against
`statement_timeout` — not just the projected total against the clock.

### Post-fix steady state

| Window | Seconds | vs 15m |
|---|---|---|
| 103 (retried after cancel) | 817.5 | — (paid for discarded partial work) |
| 104 | 385.1 | 2.06x |
| 105 | 380.8 | — |
| 106 | 428.6 | — |
| 107 | 420.9 | — |

Steady ratio **2.07x** of the 15m run. Window 103's 817.5 s is a one-off: the cancelled
INSERT's partial work was discarded and the window rebuilt from scratch.

Projected worst remaining window (117, dense 2026): ~628 s against the 1800 s ceiling.

### D7 (partial): slice 162 coverage-query regression

Run against the **completed 4h cagg** while the 15m sweep was still in flight — this
path reads only the 4h cagg, so it did not need the other granularities.

Query is the exact statement `build_minute_coverage_index` issues
(`src/manta_trading/data/gaps/minute_coverage.py`):

```sql
SELECT symbol, date_trunc('day', time_bucket) FROM minute_4hour_ohlcv
GROUP BY symbol, date_trunc('day', time_bucket);
```

| Metric | Value |
|---|---|
| Planning Time | 57 ms |
| Execution Time | 22,969 ms |
| Index rows returned | 22,687,666 |
| Distinct symbols | 11,625 |

**Success criterion 8 (correct, complete results): met.** Pre-repair the 4h cagg was
~21% materialized, so this query silently returned a fraction of true coverage — the
direct cause of the daemon re-seeding phantom gaps. Results changed *for the better*
wherever prior reads touched under-materialized regions, exactly as the design
predicted.

Plan shape is the intended post-migration form: `Custom Scan (ColumnarScan)` over
compressed 70-day chunks under a `Parallel Append`, not per-chunk index scans.

**Caveat — do not read this as "the coverage path is fast."** 23 s is a universe-wide
aggregate over every 4h bucket, run once per daemon cycle (amortized, not per-symbol).
Criterion 8 is about correctness and completeness, not latency, so this passes. But
this workload is precisely what **slice 167** replaces with a hierarchical coverage
cagg for sub-second `data_status`. Treat the 23 s / 22.7 M rows / 11,625 symbols
figures as the **pre-167 baseline**.

### Granularity cost ratio: banded, not a stable multiplier

The 15m sweep re-runs the *identical* windows, so comparing the same window index
across runs isolates granularity cost with raw volume held constant.

The first 20 windows suggested a tight multiplier (2.08–2.15, aggregate 2.11). **That
did not hold.** Measured by 15-window block:

| Windows | 1h (s) | 15m (s) | Ratio |
|---|---|---|---|
| 1–15 | 329 | 694 | 2.11 |
| 16–30 | 457 | 969 | 2.12 |
| 31–45 | 520 | 1051 | 2.02 |
| 46–60 | 641 | 969 | **1.51** |
| 61–75 | 788 | 1201 | 1.52 |
| 76–90 | 972 | 1729 | **1.78** |

The ratio steps down around window 46 and back up by window 76 — it moves in a
**1.5–2.1 band with no reliable trend**. An interim reading of "the ratio compresses as
raw volume grows" was a story fitted to three declining points and was contradicted by
the next block.

**Lesson for future sweep planning:** cross-granularity extrapolation is not reliable
here. Projections made from the first N windows of a run drifted repeatedly (15m total
projected 4.08 h → 3.89 h → 3.44 h as blocks accumulated). Use the band for planning,
quote a range rather than a point, and re-fit from ~15 real windows of the *target*
granularity before committing to a number.
