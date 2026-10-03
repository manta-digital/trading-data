---
docType: note
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: ingest workers 1, 2, 4 (slice 226)

Step `workers`, started 2026-10-03 20:36:28 UTC by `scripts/proof_226_tick.py`.

MemAvailable at start: 100.18 GiB; tick cluster RSS at start: 4.49 GiB

| workers | wall s | Σ decode s | Σ write s | peak host CPU | peak tick cluster RSS | units | failed |
|---|---|---|---|---|---|---|---|
| 1 | 92.5 | 2.5 | 89.7 | 9 % | 4.93 GiB | 78 | 0 |
| 2 | 56.9 | 3.5 | 108.4 | 11 % | 5.37 GiB | 78 | 0 |
| 4 | 44.8 | 8.3 | 165.1 | 15 % | 5.34 GiB | 78 | 0 |

Cluster RSS is the summed VmRSS of the cluster's processes (shared buffers counted once per process that touched them: an upper bound).

**Verdict:** 4 workers are 1.27× faster than 2 (< 1.5×): keep 2 (provisional until Section 7).

Proof database reloaded at 2 workers: 27,691,412 rows.
