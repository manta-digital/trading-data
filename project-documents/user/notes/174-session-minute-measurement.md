---
docType: notes
project: trading-data
dateCreated: 20261120
dateUpdated: 20261120
status: complete
---

# 174 session minute measurement (design D6)

Sets the default of `MAX_SESSION_MINUTES_PER_REQUEST` (`MT_API_MAX_SESSION_MINUTES`)
from a measurement on production.

## Procedure

- A one-off script, not committed, run with `uv run python` from the dev checkout
  against production (`Settings().timescale_db_url`) on 2026-11-20, before migration
  068 reached production. One connection with `default_transaction_read_only=on` and
  the serving session settings (`API_SERVING_SESSION`: `statement_timeout=20s`,
  `work_mem=64MB`). Nothing was written.
- Symbol: the one with the most minute rows over the last year in `minute_coverage`
  (all minutes, extended hours included; the coverage cagg does not split phases)
  among NYSE/NASDAQ instruments, indices excluded: **SPY**.
- Read: `read_session_bars(SPY, 1h, regular, adjusted=true, prints=eligible)`, what
  `get_session_bars` (and so the route with `session=regular` and its default
  `adjusted`/`prints`) runs, over windows of 3, 6, 12, 24 and 36 months ending at the
  latest session SPY holds (2026-11-13: production's minute pass fires on Saturdays,
  so the week of 2026-11-16 lands with the 2026-11-21 firing). 20 runs per window. A run cancelled by the
  statement timeout would count as over 5 s (none was).
- Minutes per window: `estimate_minutes` over the window's regular phases.

## Results

| Window | Sessions | Regular minutes | Bars | Median | p95 | Max |
|---|---|---|---|---|---|---|
| 3 months (2026-08-14..2026-11-13) | 65 | 25,350 | 455 | 0.264 s | 0.534 s | 1.275 s |
| 6 months | 128 | 49,740 | 893 | 0.365 s | 0.379 s | 0.393 s |
| 12 months | 252 | 97,740 | 1,755 | 0.939 s | 1.041 s | 1.050 s |
| 24 months | 503 | 195,090 | 3,496 | 2.570 s | 2.648 s | 2.700 s |
| 36 months | 755 | 293,010 | 5,254 | 5.127 s | 5.284 s | 5.345 s |

## Default chosen

**195,000 minutes.** The rule (task 10.1): the largest measured minute count whose
p95 is within 5 s, a quarter of the 20 s timeout, rounded down. The 24-month window
(195,090 minutes, p95 2.65 s) qualifies; the 36-month window (p95 5.28 s) does not.
It admits the 12-month window (97,740 minutes), whose p95 of 1.04 s is under the
2 s load-test bound.

At 390 regular minutes per full session that is about 500 sessions (two years) of
`regular` reads. An `extended` session counts 960 minutes, so the same ceiling
admits about 203 extended sessions (ten months). Extended minutes are sparser than
regular ones, so counting them at full weight errs toward refusing.
