---
docType: note
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: rebuild from the archive (slice 226)

Step `rebuild`, started 2026-10-03 20:28:34 UTC by `scripts/proof_226_tick.py`.

## Steps

- connect to the proof database: 0.010 s
- `mt data init --database tick`: exit 0, 1.0 s
- `mt data tick adopt --job-id GLBX-20240930-USM7UXXJBA --source /data/tick-archive/GLBX-20240930-USM7UXXJBA`: exit 0, 74.5 s
- `mt data tick adopt --job-id GLBX-20250123-XT4GD5UM6C --source /data/tick-archive/GLBX-20250123-XT4GD5UM6C`: exit 0, 92.3 s
- `mt data tick adopt --job-id GLBX-20260930-DLDYL5DM8Q --source /data/tick-archive/GLBX-20260930-DLDYL5DM8Q`: exit 0, 51.9 s
- `mt data tick adopt --job-id GLBX-20260930-HVGRLYKHRN --source /data/tick-archive/GLBX-20260930-HVGRLYKHRN`: exit 0, 52.7 s
- `mt data tick adopt --job-id GLBX-20260930-MBERAR6R7T --source /data/tick-archive/GLBX-20260930-MBERAR6R7T`: exit 0, 3.0 s
- `mt data tick adopt --job-id GLBX-20260930-VDPHT5ESUC --source /data/tick-archive/GLBX-20260930-VDPHT5ESUC`: exit 0, 61.0 s
- `mt data tick pass --estimate-only`: exit 0, 3.3 s
- estimate plans nothing (no request)
- `mt data tick ingest`: exit 0, 64.9 s

## Ingest

| measure | seen | bound | verdict |
|---|---|---|---|
| tier units ingested | 78 | 78 | ok |
| units failed | 0 | 0 | ok |
| tick_trade rows (exact count) | 27691412 | 27691412 | ok |
| slowest unit (unit 67, tbbo 2024-12-18) | 3.863 s | ≤ 120 s | ok |
| whole set (ingest phase) | 63.9 s | ≤ 7200 s | ok |

| unit | day | schema | records | seconds |
|---|---|---|---|---|
| 67 | 2024-12-18 | tbbo | 889373 | 3.863 |
| 69 | 2024-12-20 | tbbo | 684806 | 3.71 |
| 7 | 2024-09-06 | trades | 720552 | 3.556 |
| 45 | 2024-11-22 | tbbo | 507827 | 3.516 |
| 68 | 2024-12-19 | tbbo | 780160 | 3.39 |
| 17 | 2024-09-18 | trades | 756899 | 2.912 |
| 11 | 2024-09-11 | trades | 665592 | 2.808 |
| 43 | 2024-11-20 | tbbo | 517400 | 2.698 |
| 39 | 2024-11-15 | tbbo | 532267 | 2.674 |
| 63 | 2024-12-13 | tbbo | 502480 | 2.583 |
| 75 | 2024-12-27 | tbbo | 496709 | 2.533 |
| 13 | 2024-09-13 | trades | 493671 | 2.459 |
| 77 | 2024-12-30 | tbbo | 529860 | 2.457 |
| 30 | 2024-11-05 | tbbo | 354122 | 2.42 |
| 31 | 2024-11-06 | tbbo | 635703 | 2.381 |
| 66 | 2024-12-17 | tbbo | 502530 | 2.381 |
| 65 | 2024-12-16 | tbbo | 435913 | 2.375 |
| 15 | 2024-09-16 | trades | 475326 | 2.368 |
| 12 | 2024-09-12 | trades | 557758 | 2.338 |
| 19 | 2024-09-20 | trades | 435076 | 2.335 |
| 42 | 2024-11-19 | tbbo | 485559 | 2.318 |
| 37 | 2024-11-13 | tbbo | 447332 | 2.312 |
| 16 | 2024-09-17 | trades | 610653 | 2.286 |
| 5 | 2024-09-04 | trades | 540115 | 2.207 |
| 18 | 2024-09-19 | trades | 617943 | 2.201 |
| 44 | 2024-11-21 | tbbo | 662785 | 2.163 |
| 6 | 2024-09-05 | trades | 517640 | 2.106 |
| 4 | 2024-09-03 | trades | 511965 | 2.101 |
| 32 | 2024-11-07 | tbbo | 365990 | 2.061 |
| 71 | 2024-12-23 | tbbo | 428169 | 2.057 |
| 48 | 2024-11-26 | tbbo | 393014 | 1.934 |
| 9 | 2024-09-09 | trades | 439061 | 1.892 |
| 78 | 2024-12-31 | tbbo | 457761 | 1.889 |
| 10 | 2024-09-10 | trades | 461949 | 1.863 |
| 25 | 2024-09-27 | trades | 355854 | 1.798 |
| 47 | 2024-11-25 | tbbo | 486225 | 1.797 |
| 33 | 2024-11-08 | tbbo | 323551 | 1.735 |
| 49 | 2024-11-27 | tbbo | 347557 | 1.701 |
| 1 | 2024-08-30 | trades | 411633 | 1.663 |
| 27 | 2024-11-01 | tbbo | 452849 | 1.638 |
| 57 | 2024-12-06 | tbbo | 288658 | 1.618 |
| 29 | 2024-11-04 | tbbo | 405736 | 1.589 |
| 36 | 2024-11-12 | tbbo | 381286 | 1.588 |
| 41 | 2024-11-18 | tbbo | 391125 | 1.565 |
| 38 | 2024-11-14 | tbbo | 451250 | 1.56 |
| 62 | 2024-12-12 | tbbo | 421843 | 1.548 |
| 59 | 2024-12-09 | tbbo | 332112 | 1.519 |
| 61 | 2024-12-11 | tbbo | 298078 | 1.471 |
| 23 | 2024-09-25 | trades | 318991 | 1.293 |
| 60 | 2024-12-10 | tbbo | 318306 | 1.272 |
| 24 | 2024-09-26 | trades | 402357 | 1.245 |
| 53 | 2024-12-02 | tbbo | 280881 | 1.241 |
| 74 | 2024-12-26 | tbbo | 287733 | 1.22 |
| 21 | 2024-09-23 | trades | 335533 | 1.178 |
| 22 | 2024-09-24 | trades | 342551 | 1.171 |
| 54 | 2024-12-03 | tbbo | 265996 | 1.132 |
| 35 | 2024-11-11 | tbbo | 287936 | 1.095 |
| 55 | 2024-12-04 | tbbo | 279107 | 1.032 |
| 56 | 2024-12-05 | tbbo | 272204 | 1.008 |
| 72 | 2024-12-24 | tbbo | 173854 | 0.938 |
| 51 | 2024-11-29 | tbbo | 193787 | 0.843 |
| 3 | 2024-09-02 | trades | 44546 | 0.651 |
| 28 | 2024-11-03 | tbbo | 10203 | 0.605 |
| 50 | 2024-11-28 | tbbo | 36463 | 0.186 |
| 64 | 2024-12-15 | tbbo | 4102 | 0.167 |
| 73 | 2024-12-25 | tbbo | 3079 | 0.15 |
| 76 | 2024-12-29 | tbbo | 5178 | 0.148 |
| 8 | 2024-09-08 | trades | 11313 | 0.135 |
| 70 | 2024-12-22 | tbbo | 7845 | 0.119 |
| 40 | 2024-11-17 | tbbo | 6273 | 0.118 |
| 46 | 2024-11-24 | tbbo | 5286 | 0.099 |
| 14 | 2024-09-15 | trades | 7657 | 0.095 |
| 20 | 2024-09-22 | trades | 4361 | 0.085 |
| 26 | 2024-09-29 | trades | 6985 | 0.085 |
| 34 | 2024-11-10 | tbbo | 5080 | 0.082 |
| 58 | 2024-12-08 | tbbo | 3384 | 0.051 |
| 52 | 2024-12-01 | tbbo | 5513 | 0.046 |
| 2 | 2024-09-01 | trades | 3191 | 0.033 |


## Coverage

- tbbo 2024-11-01 → 2025-01-01: exit 0 (ok), 19.9 s
- trades 2024-08-30 → 2024-09-30: exit 0 (ok), 6.9 s
