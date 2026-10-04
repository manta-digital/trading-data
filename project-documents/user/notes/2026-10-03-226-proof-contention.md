---
docType: notes
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: contention against the Kalshi pass (slice 226)

Step `contention`, started 2026-10-03 20:48:44 UTC by `scripts/proof_226_tick.py`.

## The week before (pass_runs, kalshi)

168 runs; median / p95 238.0 / 275.5 s; min 132.728175 s, max 324.470982 s

## Kalshi firings

| firing | started (UTC) | duration s |
|---|---|---|
| overlapped | 21:20:07 | 298 |
| overlapped | 22:20:04 | 286 |
| solo | 23:20:05 | 286 |

## Series (median / p95; 1888 samples, 16 tick-loop iterations)

| series | overlapped | solo | tick alone |
|---|---|---|---|
| CPU busy % | 9.8 / 15.4 | 2.1 / 10.2 | 7.8 / 13.1 |
| iowait % | 0.0 / 0.2 | 0.0 / 0.1 | 0.0 / 0.1 |
| MemAvailable GiB | 99.1 / 99.3 | 99.5 / 99.6 | 99.4 / 99.8 |
| Committed_AS / CommitLimit % | 112.5 / 112.9 | 109.6 / 109.7 | 110.1 / 110.6 |
| production commits/s | 8.0 / 13.4 | 7.0 / 12.6 | 3.0 / 8.6 |
| production tuples/s | 132.7 / 4536.3 | 136.5 / 4704.1 | 0.0 / 1.2 |
| tick rows/s | 473787.9 / 581051.1 | 0.0 / 0.0 | 472731.4 / 598104.8 |
| nvme0n1 read MiB/s | 0.0 / 0.1 | 0.0 / 0.1 | 0.0 / 0.0 |
| nvme1n1 read MiB/s | 0.0 / 0.0 | 0.0 / 0.0 | 0.0 / 0.0 |
| nvme0n1 write MiB/s | 3.3 / 62.5 | 3.8 / 13.1 | 0.1 / 2.8 |
| nvme1n1 write MiB/s | 147.9 / 609.0 | 0.0 / 5.4 | 154.0 / 399.7 |

## Guards

No guard tripped.

**Verdict:** none measured at 2 ingest workers. This is not a clearance for the minute pass: Kalshi writes little and often, the minute pass walks 13,083 symbols, and 232 measures that overlap.
