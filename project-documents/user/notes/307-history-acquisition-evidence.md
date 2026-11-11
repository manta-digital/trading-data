---
docType: notes
project: trading-data
slice: 307-slice.futures-history-acquisition
dateCreated: 20261109
dateUpdated: 20261109
status: not_started
---

# History acquisition evidence (slice 307)

Filled in by Phase 7 from the narration log of
`scripts/cutover_304_first_purchase.py --slice 307` (`cutover-307-<tag>-<stamp>.log`
beside this file). Every section is empty until the purchase runs; no figure is
recorded before then.

## Reconciliation

Per request: estimate, manifest actual and the charge Databento lists for the
job, with the total and the delta (output of `scripts/reconcile_tick_spend.py`).

## Per product and family

For ES and GC `tbbo`, `ohlcv-1m` and `statistics`: the `mt data health` row and
the accounting line: sessions complete over sessions judged, source edge, caught up.

## Not-caught-up sessions

Every judged session that is not complete and not a provider hole: product,
schema, session date, the `coverage` bucket (verbatim), and the reason (the
manifest `failure_reason` verbatim, `publication after want end`, or
`acquisition failed` with the provider's error).

## Cross-tier

The `traded_vs_cleared` counts (`agree`, `under`, `over`, `no_trades`) per product
and volume family, and each `over` session by name.

## Session placement

The `verify_cme_sessions.py` result per product, with that product's calendar, and
every record outside any session.

## Scale

Request and unit counts, pass and ingest durations, and `tick_ohlcv_1m`,
`tick_statistics` and `tick_definition` rows before and after.

## Second-run result

The second estimate after the purchase: `nothing_to_buy` at 0 USD.

## Open items

The December invoice follow-up issue (D7) and issue #70 (the close-minute bar check).
