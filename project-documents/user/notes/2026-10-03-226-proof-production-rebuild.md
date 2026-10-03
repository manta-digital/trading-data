---
docType: note
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: the production tick database rebuilt from the archive (slice 226)

Task 10.3, run 2026-10-03 23:51–23:57 UTC on manta9000 against `trading_tick`
(cluster `17/tick`, port 5433), from the checkout's `.env`. Slice 227 reads the
total as the rebuild cost.

| command | exit | wall time |
|---|---|---|
| `mt data migrate apply --track tick` (tick_001 → tick_007) | 0 | 1.0 s |
| `mt data tick adopt` GLBX-20240930-USM7UXXJBA | 0 | 28.0 s |
| `mt data tick adopt` GLBX-20250123-XT4GD5UM6C | 0 | 46.3 s |
| `mt data tick adopt` GLBX-20260930-DLDYL5DM8Q | 0 | 22.8 s |
| `mt data tick adopt` GLBX-20260930-HVGRLYKHRN | 0 | 26.4 s |
| `mt data tick adopt` GLBX-20260930-MBERAR6R7T | 0 | 2.0 s |
| `mt data tick adopt` GLBX-20260930-VDPHT5ESUC | 0 | 26.0 s |
| `mt data tick pass --estimate-only` | 0 (no request planned) | 2.1 s |
| `mt data tick ingest` | 0 (78 ingested, 0 failed, 27,691,412 records) | 57.9 s |
| **total** | | **212.5 s** |

A first adopt attempt passed a malformed job id (a shell `ls` alias);
the provider answered 404 and nothing was written.
