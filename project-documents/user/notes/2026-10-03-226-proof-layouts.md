---
docType: notes
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: compression layouts A and B (slice 226)

Step `layouts`, started 2026-10-03 20:44:51 UTC by `scripts/proof_226_tick.py`.

## Layout A

segmentby `instrument_id`, orderby `ts_event, sequence, sequence_ordinal`

| tier | compressed B/row |
|---|---|
| tbbo | 28.62 |
| trades | 19.26 |
| whole table | 25.22 |

| tier | query | planning ms | execution ms | buffer hit | rows | bound |
|---|---|---|---|---|---|---|
| tbbo | Q1 | 0.31 | 108.7 | 100.0% | 668000 | ok |
| tbbo | Q2 | 0.33 | 188.8 | 100.0% | 1320 | ok |
| tbbo | Q3 | 0.19 | 4.9 | 100.0% | 8 | ok |
| tbbo | Q4 | 1.46 | 0.1 | 100.0% | 1 | ok |
| tbbo | Q5 | 0.78 | 1140.7 | 100.0% | 24523 | — |
| trades | Q1 | 0.32 | 95.7 | 100.0% | 687287 | ok |
| trades | Q2 | 0.34 | 194.7 | 100.0% | 1260 | ok |
| trades | Q3 | 0.17 | 5.1 | 100.0% | 7 | ok |
| trades | Q4 | 1.35 | 0.1 | 100.0% | 1 | ok |
| trades | Q5 | 0.79 | 1139.1 | 100.0% | 19866 | — |

Supersession delete on compressed chunks (input to the lock-timeout rule, 9.1): **0.10 s**

## Layout B

segmentby `(none)`, orderby `instrument_id, ts_event, sequence, sequence_ordinal`

| tier | compressed B/row |
|---|---|
| tbbo | 28.68 |
| trades | 19.31 |
| whole table | 25.28 |

| tier | query | planning ms | execution ms | buffer hit | rows | bound |
|---|---|---|---|---|---|---|
| tbbo | Q1 | 0.35 | 489.2 | 100.0% | 668000 | ok |
| tbbo | Q2 | 0.34 | 217.4 | 100.0% | 1320 | ok |
| tbbo | Q3 | 0.20 | 7.5 | 100.0% | 8 | ok |
| tbbo | Q4 | 2.00 | 559.4 | 100.0% | 1 | ok |
| tbbo | Q5 | 0.87 | 632.4 | 100.0% | 24523 | — |
| trades | Q1 | 0.37 | 202.5 | 100.0% | 687287 | ok |
| trades | Q2 | 0.36 | 108.0 | 100.0% | 1260 | ok |
| trades | Q3 | 0.32 | 127.5 | 100.0% | 7 | ok |
| trades | Q4 | 1.68 | 41.1 | 100.0% | 1 | ok |
| trades | Q5 | 0.89 | 1549.8 | 100.0% | 19866 | — |

Supersession delete on compressed chunks (input to the lock-timeout rule, 9.1): **0.11 s**

## Decision (TD4)

**A: lower compressed bytes per row**
