---
docType: reference
project: trading-data
slice: off-tape-prints
dateCreated: 20261016
dateUpdated: 20261016
---

# Off-tape detector fixtures (slice 171)

Whole ET days of production `minute_ohlcv`, cut on 2026-10-16 by
`scripts/cut_off_tape_fixtures.py` (rerun it to reproduce them byte for byte).
Columns: `time` (UTC), `symbol`, `open`, `high`, `low`, `close`, `volume`, as
stored. No synthetic fixtures.

| File | Why it exists |
|---|---|
| `SPY_2026-09-08.csv` | Known off-tape values: 07:35–07:38 ET at 770.19 (a prior official close), 16:01–16:04 ET highs 770.3516, 16:52 and 17:26 ET lows 724.228 |
| `SPY_2026-09-11.csv` | Known off-tape value: 17:33 ET high 787.5169 |
| `AVGO_2026-06-03.csv` | Known off-tape value: 16:15 ET low 295.4665, 38% below the market, inside the earnings window; also an earnings day |
| `MSFT_2026-08-05.csv` | Known off-tape value: 16:13 ET low 384.28 |
| `MSFT_2026-07-29.csv`, `AAPL_2026-07-30.csv`, `SMCI_2026-07-21.csv`, `PLTR_2026-08-03.csv`, `TSLA_2026-07-22.csv`, `MU_2026-06-24.csv`, `LULU_2026-09-03.csv`, `AMZN_2026-07-30.csv`, `CRM_2026-08-26.csv`, `AMD_2026-08-04.csv`, `NFLX_2026-07-16.csv`, `UPST_2026-08-04.csv`, `META_2026-07-29.csv` | Earnings days: nothing may be marked within three bars of the after-hours jump |
| `MSFT_2026-06-01.csv` | Dense off-tape prints after 16:40 ET: the scale must not collapse |
| `SPY_2026-01-07.csv` | Winter day: after-hours bars run to 01:00 UTC on 2026-01-08 and belong to the 01-07 ET day |
| `SPY_2026-09-22.csv` | Quiet day: zero marks at the starting calibration, the only such SPY day from 2026-08-31 to 2026-10-09 |
| `ARRY_2026-10-08.csv` | Tick-bound $3.75 stock: the 15:34 ET low 3.750 is the bottom of a slow one-tick dip, marked without the $0.02 threshold floor (`OFF_TAPE_MIN_ABSOLUTE`) and not with it |
