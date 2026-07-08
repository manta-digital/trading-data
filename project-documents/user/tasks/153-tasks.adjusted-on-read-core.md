---
docType: tasks
slice: 153-adjusted-on-read-core
project: trading
lld: user/slices/153-slice.adjusted-on-read-core.md
dependencies:
  - 152-slice.consolidation
projectState: >
  Slice 152 complete: adj_* columns dropped, adjustment package reduced to
  k_factor.py + ingest.py, splits/dividends tables in TimescaleDB, 7 raw
  caggs installed. No adjusted-on-read function exists yet; both DB read
  methods return raw bars only. Branch: 153-slice.adjusted-on-read-core
  (create from main).
dateCreated: 20260505
dateUpdated: 20260505
status: complete
---

## Context Summary

Slice 153 fills the adjusted-on-read gap left by 152's demolition:

- `Granularity` StrEnum + `GRANULARITY_SOURCE` mapping in `constants.py`
- `src/manta_trading/data/adjustment.py` — pure `adjusted()` function (~80 lines)
- `src/manta_trading/market/timescale_daily_db.py` — new `TimescaleDailyDataDB`
- `TimescaleMinuteDataDB`: `AGGREGATION_VIEWS` keys updated to canonical tokens;
  `adjusted: bool = True` kwarg added to `get_minute_data`

Key constraints:
- `adjusted()` accepts and returns `pd.DataFrame` (no `Bar` type)
- `compute_k_factor` lives in `data/adjustment/k_factor.py` — import, don't reimplement
- `adjusted()` raises `KeyError` on missing `prev_close` — do not swallow
- `TimescaleDailyDataDB.get_daily_data` raises `ValueError` for minute-grain tokens
- `AGGREGATION_VIEWS` key rename (`"5min"` → `"5m"`, etc.) requires a grep audit first

---

## Tasks

- [x] **T01 — Branch setup**
  - [x] Confirm current branch is `main`: `git branch --show-current`
  - [x] Create branch: `git checkout -b 153-slice.adjusted-on-read-core`
  - [x] Success: branch exists from clean main

- [x] **T02 — Add `Granularity` enum and `GRANULARITY_SOURCE` to `constants.py`**
  - [x] Add `Granularity(StrEnum)` with exactly 9 members:
        `M1="1m"`, `M5="5m"`, `M15="15m"`, `H1="1h"`, `H4="4h"`,
        `D1="1d"`, `W1="1w"`, `MO1="1mo"`, `Q1="1q"`
  - [x] Add `GRANULARITY_SOURCE: dict[Granularity, str]` using the token
        table from the slice design (9 entries, one per token)
  - [x] No granularity string appears anywhere in the file except in these
        two definitions
  - [x] Success: `from manta_trading.constants import Granularity, GRANULARITY_SOURCE`
        works; `pyright` clean

- [x] **T03 — Test: `Granularity` enum**
  - [x] Create `test/test_granularity.py`
  - [x] Test: exactly 9 members present; all 9 expected string values present
  - [x] Test: `GRANULARITY_SOURCE` has an entry for every `Granularity` member
  - [x] Test: no duplicate string values across all members
  - [x] Success: `uv run pytest test/test_granularity.py -v` passes without DB

- [x] **T04 — Implement `src/manta_trading/data/adjustment.py`**
  - [x] Locate `compute_k_factor`, `CaSnapshot`, `Split`, `Dividend` in
        `src/manta_trading/data/adjustment/k_factor.py` — confirm signatures
        before writing
  - [x] Create `src/manta_trading/data/adjustment.py` (top-level module,
        sibling to the `adjustment/` package directory)
  - [x] Implement `adjusted(df, symbol, conn, *, ca_snapshot=None) -> pd.DataFrame`:
    - [x] Returns `df` unchanged if `df.empty`
    - [x] If `ca_snapshot` is `None`, calls `_load_snapshot(symbol, start_date,
          end_date, conn)` to fetch splits, dividends, prev_closes from TimescaleDB
    - [x] Returns `df` unchanged if snapshot has no splits and no dividends
    - [x] Computes `k_by_date` dict: one `compute_k_factor` call per unique
          date in `df.index` using `ca_snapshot=snap`
    - [x] Builds `k_series` aligned to `df.index` by extracting `.date()` from
          each UTC timestamp
    - [x] Multiplies `open`, `high`, `low`, `close` columns by `k_series`;
          volume is left unchanged
    - [x] Returns a new DataFrame (`.copy()` — does not mutate input)
  - [x] Implement `_load_snapshot(symbol, start_date, end_date, conn) -> CaSnapshot`:
    - [x] Query `splits` table: `WHERE symbol = %s` (all dates — backward
          adjustment requires future CAs)
    - [x] Query `dividends` table: `WHERE symbol = %s` (all dates)
    - [x] For each dividend ex_date, query `daily_ohlcv` for `close` on the
          most recent trading day before ex_date (this is `prev_close`)
    - [x] Construct `CaSnapshot(symbol, splits=(...), dividends=(...),
          prev_closes={...}, snapshot_id=compute_snapshot_id(...))`
    - [x] `KeyError` for missing `prev_close` propagates from `compute_k_factor`
          — no additional handling needed here
  - [x] File stays ≤ 80 non-blank lines
  - [x] Success: module importable; `pyright` strict clean

- [x] **T05 — Test: `adjusted()`**
  - [x] Create `test/test_adjustment_fn.py`
  - [x] Unit test: synthetic DataFrame + `CaSnapshot` with one split (4-for-1);
        assert each OHLC column is divided by 4 for bars before ex_date,
        unchanged for bars on/after ex_date
  - [x] Unit test: symbol with no CAs → returned DataFrame equals input
        (same values, different object)
  - [x] Unit test: missing `prev_close` for a dividend → `KeyError` raised,
        not swallowed
  - [x] Unit test: `ca_snapshot` kwarg provided → `_load_snapshot` is NOT
        called (patch/mock `_load_snapshot` and assert zero calls)
  - [x] Unit test: `df.empty` input → returns immediately without DB call
  - [x] Integration test (`skipif MT_TIMESCALE_DB_URL` unset): AAPL with
        real splits/dividends; assert adjusted close on 2020-08-28 ≈
        raw close / 4 (AAPL 4-for-1 split on 2020-08-31)
  - [x] Success: all tests pass; integration test skipped cleanly without DB

- [x] **T06 — Commit checkpoint: enum + adjusted()**
  - [x] `uv run pyright` — zero errors
  - [x] `uv run pytest test/` — all pass
  - [x] Stage and commit: `feat: add Granularity enum and adjusted() function`
  - [x] Success: clean build on branch

- [x] **T07 — Implement `src/manta_trading/market/timescale_daily_db.py`**
  - [x] Create `TimescaleDailyDataDB` class mirroring `TimescaleMinuteDataDB`
        structure: `ConnectionPool`, `_configure_connection` static method,
        `_init_pool`, `_ensure_pool`, `close`
  - [x] Define module-level sets:
        `_DAILY_GRAINS = {Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1}`
        `_MINUTE_GRAINS = {Granularity.M1, Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4}`
  - [x] Implement `get_daily_data(symbol, start, end, granularity, *, adjusted=True) -> pd.DataFrame`:
    - [x] Raise `ValueError` if `granularity in _MINUTE_GRAINS`
    - [x] Look up source name via `GRANULARITY_SOURCE[granularity]`
    - [x] Route `D1` → `SELECT date AS trade_date, open, high, low, close, volume FROM daily_ohlcv WHERE symbol=%s AND date>=%s AND date<=%s ORDER BY date`
    - [x] Route `W1/MO1/Q1` → `SELECT time_bucket AS trade_date, open, high, low, close, volume FROM "{view}" WHERE symbol=%s AND time_bucket>=%s AND time_bucket<=%s ORDER BY time_bucket`
    - [x] View name is validated against `GRANULARITY_SOURCE` whitelist before
          use in the f-string query — include comment noting this is safe
    - [x] Call `_rows_to_dataframe(rows)` to build DataFrame
    - [x] If `adjusted=True` and DataFrame is not empty: borrow a connection
          from `self._pool` and call `adjusted_fn(df, symbol, conn)`
  - [x] Implement `_rows_to_dataframe(rows) -> pd.DataFrame` (static method):
        columns `trade_date`, `open`, `high`, `low`, `close`, `volume`;
        set `trade_date` as DatetimeIndex (UTC); cast OHLC to float64,
        volume to int64
  - [x] Import `adjusted` from `manta_trading.data.adjustment` as `adjusted_fn`
        to avoid name collision with the `adjusted: bool` parameter
  - [x] Success: class importable; `pyright` strict clean

- [x] **T08 — Test: `TimescaleDailyDataDB`**
  - [x] Create `test/test_timescale_daily_db.py`
  - [x] Unit test: `ValueError` raised for each of the 5 minute-grain tokens
  - [x] Unit test: `GRANULARITY_SOURCE` routes `D1` to `"daily_ohlcv"`,
        `W1` to `"daily_weekly_ohlcv"`, `MO1` to `"daily_monthly_ohlcv"`,
        `Q1` to `"daily_quarterly_ohlcv"`
  - [x] Integration test (`skipif MT_TIMESCALE_DB_URL` unset):
    - [x] `D1` with `adjusted=False` returns non-empty DataFrame for AAPL
          with expected columns
    - [x] `W1` with `adjusted=False` returns non-empty DataFrame (rows from
          `daily_weekly_ohlcv`)
    - [x] `adjusted=False` close for AAPL on 2020-08-28 matches
          `daily_ohlcv` directly (raw value ~$499)
    - [x] `adjusted=True` close for AAPL on 2020-08-28 ≈ raw close / 4
  - [x] Success: all tests pass; integration tests skipped cleanly without DB

- [x] **T09 — Extend `TimescaleMinuteDataDB`: update keys + adjusted kwarg**
  - [x] Before editing, run the grep audit:
        `grep -rn '"5min"\|"15min"\|"1hour"\|"4hour"' src/ tests/`
        and list every file that uses the old keys
  - [x] Update `AGGREGATION_VIEWS` keys:
        `"5min"` → `"5m"`, `"15min"` → `"15m"`,
        `"1hour"` → `"1h"`, `"4hour"` → `"4h"`
  - [x] Update `aggregation` parameter type annotation on `get_minute_data`
        to `str | Granularity | None`
  - [x] Update any internal callers of `get_minute_data` or `_get_aggregated_data`
        identified by the grep audit to pass canonical keys
  - [x] Add `adjusted: bool = True` kwarg to `get_minute_data`
  - [x] When `adjusted=True` and result DataFrame is not empty: borrow a
        connection from `self._pool` and call `adjusted_fn(df, symbol, conn)`
  - [x] When `adjusted=False`: return raw DataFrame without calling `adjusted_fn`
  - [x] Import `adjusted` from `manta_trading.data.adjustment` as `adjusted_fn`
  - [x] Success: `pyright` strict clean; existing tests still pass

- [x] **T10 — Test: `get_minute_data` adjusted kwarg**
  - [x] Add to `test/test_timescale_minute_db.py` (or create if absent):
  - [x] Unit test: `AGGREGATION_VIEWS` keys match the 4 canonical `Granularity`
        token values (`M5`, `M15`, `H1`, `H4`) — no old-style strings present
  - [x] Unit test: `adjusted=False` returns raw DataFrame without calling
        `adjusted_fn` (patch `manta_trading.market.timescale_minute_db.adjusted_fn`
        and assert zero calls)
  - [x] Integration test (`skipif MT_TIMESCALE_DB_URL` unset):
    - [x] `adjusted=True` for AAPL around the 2020-08-31 split returns a
          DataFrame where close on 2020-08-28 ≈ raw close / 4
  - [x] Success: all tests pass

- [x] **T11 — Final build and commit**
  - [x] `uv run pyright` — zero errors across all new and modified files
  - [x] `uv run pytest test/` — all pass
  - [x] Stage all changes and commit:
        `feat: add TimescaleDailyDataDB; adjusted=True on minute reader`
  - [x] Success: branch ready for PM review
