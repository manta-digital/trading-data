---
docType: notes
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: query set, uncompressed baseline (slice 226)

Step `queries`, started 2026-10-03 20:44:27 UTC by `scripts/proof_226_tick.py`.

## Targets (busiest instrument-session per tier)

| tier | instrument_id | session | records |
|---|---|---|---|
| tbbo | 5002 | 2024-12-20 | 668,000 |
| trades | 118 | 2024-09-06 | 687,287 |

## Uncompressed, 3 warm runs (medians)

| tier | query | planning ms | execution ms | buffer hit | rows | bound |
|---|---|---|---|---|---|---|
| tbbo | Q1 | 0.20 | 399.4 | 100.0% | 668000 | ok |
| tbbo | Q2 | 0.22 | 385.0 | 100.0% | 1320 | ok |
| tbbo | Q3 | 0.18 | 120.3 | 100.0% | 8 | ok |
| tbbo | Q4 | 0.44 | 0.0 | 100.0% | 1 | ok |
| tbbo | Q5 | 0.39 | 1908.0 | 100.0% | 24523 | — |
| trades | Q1 | 0.21 | 96.8 | 100.0% | 687287 | ok |
| trades | Q2 | 0.24 | 161.1 | 100.0% | 1260 | ok |
| trades | Q3 | 0.19 | 110.0 | 100.0% | 7 | ok |
| trades | Q4 | 0.44 | 0.1 | 100.0% | 1 | ok |
| trades | Q5 | 0.42 | 1475.2 | 100.0% | 19866 | — |

## Chunk interval

Chunks held now: 14.

7-day chunks over 20 years: 1044 chunks (within 1000–2000); worst Q1–Q4 planning 0.44 ms (bound 50 ms). **Validated.**
