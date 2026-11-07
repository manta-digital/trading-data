---
docType: notes
project: trading-data
slice: 304-slice.first-real-purchase
dateCreated: 20261107
dateUpdated: 20261107
status: not_started
---

# First purchase evidence (slice 304)

Filled in by the Phase 7 run of `scripts/cutover_304_first_purchase.py`. Every
section is empty until the purchase runs; no figure is recorded before then.

## Reconciliation

Per request: estimate, manifest actual and the charge Databento lists for the
job, with the total and the delta (output of `scripts/reconcile_tick_spend.py`).

## Verdicts

The `status` result and each `coverage` result (ES `ohlcv-1m` 2024, ES and GC
`statistics` over the whole range).

## Findings by D6 class

One entry per unit that did not ingest: product, schema, UTC day or session,
failure reason, and the record that caused it.

## D7 results

Session placement of the 2024 ES bars; `tick_definition` rows per product
before and after; request and unit counts; pass and ingest durations, recorded
for slice 305.

## Second-run result

The verdict and planned figure of the second `pass --estimate-only`.

## Open items

Issue #69 (the month-end invoice comparison) and issue #70 (the close-minute
bar check).
