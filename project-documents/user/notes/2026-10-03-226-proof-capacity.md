---
docType: notes
project: trading-data
slice: 226-slice.proof-on-existing-data
dateCreated: 20261003
dateUpdated: 20261003
status: complete
---

# Proof: capacity projection for ES and GC (slice 226)

`mt data tick estimate --stype parent --start 2025-10-03 --end 2026-10-03` (free metadata calls), run 2026-10-03 for `ES.FUT` and `GC.FUT`: the latest year the dataset holds (available to 2026-10-03). **A projection**: the provider's record count for one year times the bytes per row measured on the 2024 proof set, not a measurement of that year.

Compressed bytes per row: layout A, `user/notes/2026-10-03-226-proof-layouts.md` (trades 19.26, tbbo 28.62). Archive bytes per row: `user/notes/2026-10-03-226-proof-size.md` (trades 15.2, tbbo 21.0).

| symbols | tier | records / year | billable bytes | list cost USD | stored GB / year (projected) | archive GB / year (projected) |
|---|---|---|---|---|---|---|
| ES.FUT | trades | 129,148,028 | 6,199,105,344 | 161.65 | 2.49 | 1.96 |
| ES.FUT | tbbo | 129,148,028 | 10,331,842,240 | 269.42 | 3.70 | 2.71 |
| GC.FUT | trades | 35,035,188 | 1,681,689,024 | 43.85 | 0.67 | 0.53 |
| GC.FUT | tbbo | 35,035,188 | 2,802,815,040 | 73.09 | 1.00 | 0.74 |

At tbbo, ES and GC together: 4.70 GB/year stored and 3.45 GB/year archived; twenty years 94 GB and 69 GB, against `/data`'s 1,339 GB free (2026-10-03, provisioning log). List cost is shown for scale only: under the Standard plan both tiers are included, whether at $0 is verified at subscription (architecture).
