---
docType: notes
project: trading-data
slice: 303-slice.statistics-and-the-open-interest-roll-rule
dateCreated: 20261104
dateUpdated: 20261104
status: in_progress
---

# Statistics evaluation (slice 303, D11)

The real purchase of ES and GC `statistics` and the definitions they need, run on
throwaway databases, and what it shows. Sections are filled as the tasks that
produce them run (8.2 to 8.5, 14.4, 15.1).

## Purchase attempt, 2026-11-04 (task 8.2)

**Setup (8.1).** A primary and a tick database were created on the test cluster
(`mt_303_primary_20261104163444`, `mt_303_tick_20261104163444`), migrated by
`mt data init` (primary 69 migrations) and `mt data init --database tick` (11
migrations, through `tick_010_statistics`). The tick database holds a copy of
production's manifest and availability rows (read from `trading_tick`
read-only: 10 requests, 258 units, 571 definitions, 24,437 ledger rows, the
dataset edge, 106 day conditions), so the tier days are owned and the pass plans
no `tbbo`. The wrapper universe keeps each entry's production tier range and adds
`definition` and `statistics` wants over 2024-08-30 to 2025-01-01.

**Estimate.** `pass --estimate-only --json`: 16 requests, `planned_usd`
0.064747896046, per-pass ceiling 0.50, trailing 30-day spend 8.43457497283817.
Statistics: ES 0.0127 and GC 0.0242, as the design says. Definitions came to
about 0.028 rather than the design's October-only 0.0143: GC has no definitions
before November in the manifest, so its August to September days (about 0.0125)
are planned as well, and one-day ES/GC edges (2024-08-30, 2024-09-30,
2024-11-01) add about 0.001. The plan is inside the 0.50 per-pass ceiling.

**Guard refusal.** `pass --json` exits 5, nothing bought: verdict `refused`,
reason "both MT_TICK_SPEND_CEILING_USD and MT_TICK_SPEND_30D_CEILING_USD must be
set to buy; absent: MT_TICK_SPEND_30D_CEILING_USD". The development `.env` sets
only the per-pass ceiling. The runbook (`100-production-operations.md`, step 7:
"The PM sets ... No agent edits them") reserves the 30-day figure to the PM, and
the trailing spend (8.43) already exceeds the `.env_sample` example of 5.00, so
there is no existing value to reuse. The refusal is recorded and not forced.

To resume: set `MT_TICK_SPEND_30D_CEILING_USD` (at least trailing spend plus the
0.0647 plan) in the environment of `cli303.sh`, then rerun `pass --json` and
`ingest --json` through the wrapper; the throwaway databases and the scratch
archive directory (`~/scratch-303/archive`) are kept for it.
