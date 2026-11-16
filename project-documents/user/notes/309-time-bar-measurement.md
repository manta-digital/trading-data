---
docType: notes
project: trading-data
dateCreated: 20261115
dateUpdated: 20261115
status: complete
---

# 309 time-bar measurement (design criterion 5)

Sets the default of `MT_API_MAX_TIME_BAR_TRADES` from a measurement on production.

## Procedure

- A one-off script, not committed, run with `uv run python` from the dev checkout.
  URLs from `Settings`: the production tick database (`trading_tick` on manta9000)
  and the calendar database.
- One read-only transaction (`default_transaction_read_only=on`) with jit off
  (`TICK_JIT_OPTION`), as every tick connection sets. Nothing was written.
- Built the plan the bars route builds: `product_plan` for ES, rule `c.0`, over
  2024-08-01..2025-01-01, family `TICK_TIER`. Kept the 64 sessions with a tick ledger
  row, each with its ledger record count.
- Timed `cut_time_bars` at `1m`, session by session in plan order, as the route
  reads. For every session, the sum of the cut's `trade_count` was checked against
  the ledger count.

## Results

| Measured | Sessions | Ledger trades | Time |
|---|---|---|---|
| (a) median session, 2024-11-27 ESZ4 | 1 | 346,615 | 0.179 s |
| (b) widest run at the limit, 2024-11-25..2024-12-03 | 7 | 1,995,794 (99.8% of 2,000,000) | 1.138 s, then 1.085 s |

- Run (b) was the largest sum among the runs of consecutive sessions summing to
  95-100% of 2,000,000 (sliding window over the ledger counts).
- Every session's `sum(trade_count)` equalled its ledger count: no mismatch.
- Median held ES session over the span: 349,112 trades.

## Default chosen

2,000,000. Run (b) completes in about 1.1 s, well inside the 10 s bound slice 308
used, so the limit is not lowered. The stop condition (a median session over 10 s, or
a limit under one median session) is far from reached.

The time is the database cut alone. The route adds building the response model (in
the executor) and serialization; the load test (`test/load/test_309_time_bars_nfr.py`)
holds the end-to-end bound.
