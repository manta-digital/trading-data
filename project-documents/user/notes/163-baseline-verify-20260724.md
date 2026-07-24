---
docType: notes
layer: project
project: trading-data
slice: minute-cagg-chunk-re-sizing
audience: [human, ai]
description: Slice 163 pre-repair cagg-vs-raw parity baseline and single-symbol 4h EXPLAIN, captured read-only from prod before the repair sweep
dateCreated: 20260724
dateUpdated: 20260724
status: complete
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
