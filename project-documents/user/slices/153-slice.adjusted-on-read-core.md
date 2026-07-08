---
title: "153 — Adjusted-on-read: core function and DB read layer"
slice: 153
initiative: 140
status: complete
phase: 6
type: feat
effort: 2
tags: [adjusted-on-read, adjustment, timescale, daily-db]
created: 20260505
dateUpdated: 20260505
implementedDate: 20260505
author: pm+claude
docType: slice-design
project: trading
dateCreated: 20260505
dependsOn: [152]
interfaces: [154]
parent: user/architecture/140-slices.data-quality-operations.md
---

# Slice 153 — Adjusted-on-read: core function and DB read layer

## Overview

Slice 152 deleted adjusted-on-write storage: the `adj_*` columns, the band
writer, and the CA-drift daemon hook are gone. This slice fills the gap: a
canonical `Granularity` enum, one pure `adjusted()` function that computes
adjusted prices on demand, a new `TimescaleDailyDataDB` reader for daily and
coarser granularities, and an `adjusted=True` kwarg on the existing
`TimescaleMinuteDataDB.get_minute_data`.

No CLI changes — those are slice 154. After this slice, programmatic callers
that import `adjusted()` or call the DB read methods get correct adjusted
prices by default.

## Value

**Developer-facing:** The two programmatic DB read APIs now return adjusted
bars by default. Any downstream consumer (CLI in slice 154, Jupyter
notebooks, backtest scaffolding in future initiatives) calls one method and
gets a ready-to-use adjusted series without writing adjustment logic itself.

**Architectural:** Establishes the single source of truth for granularity
tokens. Every consumer — CLI args, function kwargs, cagg view-name maps —
references the same enum. Changing a token means editing one definition.

## Technical Scope

**In scope:**
- `Granularity` StrEnum and `GRANULARITY_SOURCE` mapping in `constants.py`
- `src/manta_trading/data/adjustment.py` — top-level module with `adjusted()`
- `src/manta_trading/market/timescale_daily_db.py` — new `TimescaleDailyDataDB`
- `AGGREGATION_VIEWS` keys in `TimescaleMinuteDataDB` updated to canonical tokens
- `adjusted: bool = True` kwarg on `TimescaleMinuteDataDB.get_minute_data`
- Unit and integration tests for all of the above

**Out of scope:**
- CLI commands (slice 154)
- Daemon changes (slice 154)
- Ingest path changes
- Any new schema migrations

## Dependencies

### Prerequisites

- Slice 152 complete: `adj_*` columns dropped, `data/adjustment/` package
  reduced to `k_factor.py` + `ingest.py`, `splits` and `dividends` tables
  populated in TimescaleDB, 7 raw caggs installed.
- `compute_k_factor` lives at
  `src/manta_trading/data/adjustment/k_factor.py`; `CaSnapshot`, `Split`,
  `Dividend` are defined there and exported from the package `__init__`.

### Interfaces Required

- `splits` and `dividends` tables in TimescaleDB (created by migration 029
  in slice 152); columns: `symbol`, `ex_date`, `ratio_to / ratio_from`
  (splits) and `amount` (dividends).
- `daily_ohlcv` for `prev_close` lookups.
- Cagg names: `minute_5min_ohlcv`, `minute_15min_ohlcv`, `minute_hourly_ohlcv`,
  `minute_4hour_ohlcv`, `daily_weekly_ohlcv`, `daily_monthly_ohlcv`,
  `daily_quarterly_ohlcv` (created in slice 152).
- `psycopg.Connection` (psycopg3) for DB reads inside `adjusted()`.

## Architecture

### Component Structure

```
manta_trading/
  constants.py                    ← add Granularity, GRANULARITY_SOURCE
  data/
    adjustment.py                 ← NEW: adjusted(df, symbol, conn, ...)
    adjustment/
      __init__.py                 ← unchanged (stub pointing here)
      k_factor.py                 ← unchanged (compute_k_factor lives here)
      ingest.py                   ← unchanged
  market/
    timescale_minute_db.py        ← extend get_minute_data; update AGGREGATION_VIEWS
    timescale_daily_db.py         ← NEW: TimescaleDailyDataDB
```

`adjusted()` is a pure function: it receives a DataFrame and a live
connection (or a pre-fetched `ca_snapshot`), fetches CAs if needed, and
returns a new DataFrame with OHLC columns multiplied by the per-row k-factor.
It does not own or manage connections.

### Data Flow

**`get_minute_data` with `adjusted=True`:**

```
caller
  → TimescaleMinuteDataDB.get_minute_data(symbol, start, end, adjusted=True)
      → SELECT from minute_ohlcv (or cagg view via _get_aggregated_data)
      → raw DataFrame
      → adjusted(raw_df, symbol, conn)
          → SELECT splits, dividends WHERE symbol + ex_date in date range
          → SELECT prev_close from daily_ohlcv for each CA ex_date
          → compute_k_factor(symbol, bar_date, ca_snapshot=snap) per bar
          → return adjusted DataFrame
      → return adjusted DataFrame
```

**`get_daily_data` with `adjusted=True`:**

```
caller
  → TimescaleDailyDataDB.get_daily_data(symbol, start, end, Granularity.D1)
      → SELECT from daily_ohlcv
      → adjusted(raw_df, symbol, conn)
      → return adjusted DataFrame
```

**`adjusted()` when `ca_snapshot` is provided:**

```
adjusted(df, symbol, conn, ca_snapshot=snap)
  → skip DB reads (splits, dividends, prev_closes already in snap)
  → compute_k_factor per bar using snap
  → return adjusted DataFrame
```

### Granularity token table

| Token  | Enum member | Source table / cagg          | Reader              |
|--------|-------------|------------------------------|---------------------|
| `1m`   | `M1`        | `minute_ohlcv`               | `TimescaleMinuteDataDB` |
| `5m`   | `M5`        | `minute_5min_ohlcv`          | `TimescaleMinuteDataDB` |
| `15m`  | `M15`       | `minute_15min_ohlcv`         | `TimescaleMinuteDataDB` |
| `1h`   | `H1`        | `minute_hourly_ohlcv`        | `TimescaleMinuteDataDB` |
| `4h`   | `H4`        | `minute_4hour_ohlcv`         | `TimescaleMinuteDataDB` |
| `1d`   | `D1`        | `daily_ohlcv`                | `TimescaleDailyDataDB`  |
| `1w`   | `W1`        | `daily_weekly_ohlcv`         | `TimescaleDailyDataDB`  |
| `1mo`  | `MO1`       | `daily_monthly_ohlcv`        | `TimescaleDailyDataDB`  |
| `1q`   | `Q1`        | `daily_quarterly_ohlcv`      | `TimescaleDailyDataDB`  |

`GRANULARITY_SOURCE: dict[Granularity, str]` in `constants.py` encodes this
mapping. Every routing decision references this dict — no granularity string
is hardcoded outside the definition.

## Technical Decisions

### DataFrame as the bar container

`get_minute_data` returns `pd.DataFrame`. `TimescaleDailyDataDB` will also
return `pd.DataFrame` for consistency, with a UTC DatetimeIndex and float64
OHLC + int64 volume columns. `adjusted()` accepts and returns `pd.DataFrame`.

No `Bar` type is introduced. The codebase has no such type today; adding one
would require a parallel conversion layer with no short-term payoff.

### adjusted() signature

```python
def adjusted(
    df: pd.DataFrame,
    symbol: str,
    conn: psycopg.Connection,
    *,
    ca_snapshot: CaSnapshot | None = None,
) -> pd.DataFrame:
```

`conn` is a live psycopg3 connection. The caller opens/borrows the connection;
`adjusted()` does not manage connection lifecycle. When `ca_snapshot` is
provided the DB reads (splits, dividends, prev_closes) are skipped and the
snapshot is used directly — this matches the existing `compute_k_factor`
optional-snapshot pattern.

### k-factor application

`adjusted()` computes one `CaSnapshot` per call (via DB reads or the
provided snapshot), then calls `compute_k_factor(symbol, bar_date,
ca_snapshot=snap)` for each unique date in `df`. The k-factor series is then
broadcast across all rows on the same date. OHLC columns are multiplied by
k-factor; volume is left unchanged.

Using dates as the k-factor grouping key is correct for daily bars. For
sub-daily (minute) bars, the bar's `date` component is extracted from the
UTC timestamp. An intra-day bar before market open on an ex-date carries the
pre-ex-date k-factor (ex-date adjustments apply from market open on ex-date).
This matches EODHD's convention.

### TimescaleMinuteDataDB AGGREGATION_VIEWS key migration

Current keys: `"5min"`, `"15min"`, `"1hour"`, `"4hour"`.
New keys: `"5m"`, `"15m"`, `"1h"`, `"4h"` (canonical `Granularity` values).

The `aggregation` parameter to `get_minute_data` uses the old string keys
today; slice 154's CLI will pass `Granularity` enum values. This slice
updates `AGGREGATION_VIEWS` to use canonical keys and updates any internal
callers. External callers passing old strings will hit `ValueError` — but no
external callers exist yet (slice 154 is the first CLI consumer). The
`aggregation` parameter type annotation changes to `str | Granularity | None`
for the transition period; slice 154 passes enum values.

### No current_ca_snapshot helper in adjustment.py

`current_ca_snapshot` (defined in the slice 143 design but not ultimately
needed post-152) is NOT called by `adjusted()`. The function builds its own
lightweight snapshot inline from DB reads, which is simpler and avoids
re-importing a potentially-removed helper. The only reuse is
`CaSnapshot` and `compute_k_factor` from `k_factor.py`.

### Failure contract

`adjusted()` raises `KeyError` if `prev_close` is missing for a CA ex-date.
This is propagated from `compute_k_factor`, which already has this contract.
Do not swallow. Callers (CLI in slice 154) convert to user-facing error
messages.

`TimescaleDailyDataDB.get_daily_data` raises `ValueError` for granularity
tokens in the minute range (`M1`, `M5`, `M15`, `H1`, `H4`).

## Implementation Details

### adjustment.py outline (~80 lines)

```python
# src/manta_trading/data/adjustment.py

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd
import psycopg

from manta_trading.data.adjustment.k_factor import (
    CaSnapshot, Split, Dividend, compute_k_factor, compute_snapshot_id,
)

_MINUTE_GRAINS = {"1m", "5m", "15m", "1h", "4h"}


def adjusted(
    df: pd.DataFrame,
    symbol: str,
    conn: psycopg.Connection,
    *,
    ca_snapshot: CaSnapshot | None = None,
) -> pd.DataFrame:
    """Return df with OHLC prices adjusted for splits and dividends."""
    if df.empty:
        return df

    bar_dates: list[date] = sorted({ts.date() for ts in df.index})
    start_date, end_date = bar_dates[0], bar_dates[-1]

    if ca_snapshot is None:
        ca_snapshot = _load_snapshot(symbol, start_date, end_date, conn)

    if not ca_snapshot.splits and not ca_snapshot.dividends:
        return df

    k_by_date = {
        d: compute_k_factor(symbol, d, ca_snapshot=ca_snapshot)
        for d in bar_dates
    }

    result = df.copy()
    k_series = pd.Series(
        [k_by_date[ts.date()] for ts in result.index],
        index=result.index,
        dtype="float64",
    )
    for col in ("open", "high", "low", "close"):
        if col in result.columns:
            result[col] = result[col] * k_series

    return result


def _load_snapshot(
    symbol: str,
    start_date: date,
    end_date: date,
    conn: psycopg.Connection,
) -> CaSnapshot:
    # fetch splits, dividends, prev_closes from TimescaleDB
    # return CaSnapshot(...)
    ...
```

`_load_snapshot` queries `splits`, `dividends`, and `daily_ohlcv` for
`prev_close` values. The date window for CAs is open-ended on the right (all
future CAs affect past bars under backward-adjustment); it is bounded on the
left only loosely (a CA before `start_date` does not affect the bars in `df`
because `compute_k_factor` only multiplies CAs with `ex_date > target_date`).

### TimescaleDailyDataDB outline

```python
# src/manta_trading/market/timescale_daily_db.py

from manta_trading.constants import Granularity, GRANULARITY_SOURCE

_DAILY_GRAINS = {Granularity.D1, Granularity.W1, Granularity.MO1, Granularity.Q1}
_MINUTE_GRAINS = {Granularity.M1, Granularity.M5, Granularity.M15,
                  Granularity.H1, Granularity.H4}

class TimescaleDailyDataDB:
    def get_daily_data(
        self,
        symbol: str,
        start: date,
        end: date,
        granularity: Granularity,
        *,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        if granularity in _MINUTE_GRAINS:
            raise ValueError(
                f"Granularity {granularity!r} is minute-grain; "
                "use TimescaleMinuteDataDB instead"
            )
        source = GRANULARITY_SOURCE[granularity]
        df = self._query(source, symbol, start, end)
        if adjusted and not df.empty:
            with self._pool.connection() as conn:
                df = adjusted_fn(df, symbol, conn)
        return df
```

Connection management mirrors `TimescaleMinuteDataDB`: a `ConnectionPool`
initialized in `__init__`.

The time column for daily bars is `date` (from `daily_ohlcv`) and
`time_bucket` (a `date` for weekly/monthly/quarterly caggs created in slice
152). The `_rows_to_dataframe` static method will normalize whichever column
is present to a `date`-based DatetimeIndex.

### Cagg time column note

The slice-152 daily caggs project `time_bucket(...)` as a `date`. The query
for weekly/monthly/quarterly granularities uses:

```sql
SELECT time_bucket as trade_date, open, high, low, close, volume
FROM "{view_name}"
WHERE symbol = %s AND time_bucket >= %s AND time_bucket <= %s
ORDER BY time_bucket
```

`daily_ohlcv` uses `date` directly:

```sql
SELECT date AS trade_date, open, high, low, close, volume
FROM daily_ohlcv
WHERE symbol = %s AND date >= %s AND date <= %s
ORDER BY date
```

Both result sets share the same column alias; `_rows_to_dataframe` is shared.

## Integration Points

### Provides to Other Slices

- **Slice 154 (CLI surface):** `TimescaleDailyDataDB.get_daily_data` and the
  updated `get_minute_data` are the data access layer behind `mt data get`
  and `mt data pull`. The `Granularity` enum is the canonical type for CLI
  argument parsing.
- `adjusted()` is available as a library function for any future consumer
  (Jupyter, backtest initiative) needing adjusted bars without going through
  the CLI.

### Consumes from Other Slices

- Slice 152's schema: `splits`, `dividends`, `daily_ohlcv`, raw cagg names.
- Slice 143's `compute_k_factor` and `CaSnapshot` from `data/adjustment/k_factor.py`.

## Success Criteria

### Functional Requirements

- All 9 `Granularity` tokens are importable from `manta_trading.constants`.
- `GRANULARITY_SOURCE` has one entry per token; no token maps to a hardcoded
  string outside this dict.
- `adjusted(df, symbol, conn)` returns a DataFrame with OHLC values scaled by
  the correct per-date k-factor.
- `adjusted()` returns `df` unchanged when no CAs exist for the symbol.
- `adjusted()` raises `KeyError` when `prev_close` is missing for a CA date.
- `adjusted(df, symbol, conn, ca_snapshot=snap)` uses the snapshot and skips
  DB reads.
- `TimescaleDailyDataDB.get_daily_data` routes correctly:
  - `D1` → `daily_ohlcv`
  - `W1`, `MO1`, `Q1` → appropriate daily cagg
  - Minute tokens → `ValueError`
- `get_minute_data(adjusted=False)` returns raw bars without calling `adjusted()`.
- `get_minute_data(adjusted=True)` returns adjusted bars for a symbol with
  known CAs.

### Technical Requirements

- `uv run pyright` — zero errors (strict mode) on all new and modified files.
- `uv run pytest test/` — all tests pass.
- `data/adjustment.py` ≤ 80 lines (excluding blank lines); pure function,
  no side effects beyond returning a new DataFrame.
- `timescale_daily_db.py` mirrors the structure of `timescale_minute_db.py`:
  `ConnectionPool`, same configure-connection pattern, same error logging style.

### Verification Walkthrough

Steps 1–5 require no database. Steps 6–9 require `MT_TIMESCALE_DB_URL` set
with AAPL data including splits/dividends.

**Implementation caveat:** Python cannot have both `adjustment.py` and
`adjustment/` as siblings with the same name — the package always wins. The
`adjusted()` function was placed in `data/adjustment/_adjusted.py` and
re-exported from `data/adjustment/__init__.py`. The import path
`from manta_trading.data.adjustment import adjusted` works as specified.

The project uses `mypy` (not `pyright`) — substitute `uv run mypy --ignore-missing-imports`.

**1. New files exist**

```bash
ls src/manta_trading/data/adjustment/_adjusted.py
ls src/manta_trading/market/timescale_daily_db.py
```

Expected: both files listed. ✓ (verified 2026-05-05)

**2. Old `AGGREGATION_VIEWS` keys are gone**

```bash
grep -rn '"5min"\|"15min"\|"1hour"\|"4hour"' src/ test/
```

Expected: no output (only the test asserting old keys are absent may appear).
✓ (verified 2026-05-05 — only test/unit/market/test_timescale_minute_db.py contains
the old strings inside an assertion that they are absent from AGGREGATION_VIEWS)

**3. Imports are clean**

```bash
uv run python -c "from manta_trading.data.adjustment import adjusted; print('OK')"
uv run python -c "from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB; print('OK')"
```

Expected: both print `OK`. ✓ (verified 2026-05-05)

**4. `Granularity` enum — 9 tokens, 9 source mappings**

```bash
uv run python -c "
from manta_trading.constants import Granularity, GRANULARITY_SOURCE
print([g.value for g in Granularity])
print(len(Granularity), 'tokens,', len(GRANULARITY_SOURCE), 'source mappings')
"
```

Expected output:
```
['1m', '5m', '15m', '1h', '4h', '1d', '1w', '1mo', '1q']
9 tokens, 9 source mappings
```
✓ (verified 2026-05-05)

**5. Mypy and test suite clean**

```bash
uv run mypy --ignore-missing-imports \
    src/manta_trading/data/adjustment/_adjusted.py \
    src/manta_trading/data/adjustment/__init__.py \
    src/manta_trading/market/timescale_daily_db.py \
    src/manta_trading/constants.py
uv run pytest test/unit/ test/regression/ -q --tb=short
```

Mypy: `Success: no issues found` on all new files.
Pytest: `1250 passed, 12 skipped` ✓ (verified 2026-05-05)

Note: pre-existing mypy errors in `timescale_minute_db.py` lines 431–434
(`get_coverage_analysis`) are not introduced by this slice.

**6. Enum sanity check**

```python
from manta_trading.constants import Granularity, GRANULARITY_SOURCE

assert len(Granularity) == 9
assert all(g in GRANULARITY_SOURCE for g in Granularity)
# No duplicate string values:
vals = [g.value for g in Granularity]
assert len(vals) == len(set(vals))
```

**2. Raw vs adjusted close continuity across a known AAPL split**

AAPL had a 4-for-1 split on 2020-08-31. The raw close on 2020-08-28 was
~$499; the raw open on 2020-08-31 was ~$127. The adjusted close on 2020-08-28
should be ~$124.75 (÷4) — continuous with the post-split prices.

```python
import psycopg
from datetime import date
import pandas as pd
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.constants import Granularity

db = TimescaleDailyDataDB(conninfo)

raw  = db.get_daily_data("AAPL", date(2020, 8, 25), date(2020, 9, 4),
                          Granularity.D1, adjusted=False)
adj  = db.get_daily_data("AAPL", date(2020, 8, 25), date(2020, 9, 4),
                          Granularity.D1, adjusted=True)

# Adjusted close on 2020-08-28 should be ~1/4 of raw close:
assert abs(adj.loc["2020-08-28", "close"] - raw.loc["2020-08-28", "close"] / 4) < 0.01
# Continuity: adjusted close 2020-08-28 ≈ adjusted close 2020-08-31
# (no discontinuity across the split date):
assert abs(adj.loc["2020-08-28", "close"] - adj.loc["2020-08-31", "close"]) < 1.0
```

**3. ValueError on wrong granularity**

```python
import pytest
with pytest.raises(ValueError):
    db.get_daily_data("AAPL", date(2024,1,1), date(2024,1,31), Granularity.M1)
```

**4. get_minute_data adjusted kwarg**

```python
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB
from datetime import datetime, timezone

minute_db = TimescaleMinuteDataDB(conninfo)
start = datetime(2020, 8, 28, 9, 30, tzinfo=timezone.utc)
end   = datetime(2020, 8, 31, 16, 0, tzinfo=timezone.utc)

raw_m = minute_db.get_minute_data("AAPL", start, end, adjusted=False)
adj_m = minute_db.get_minute_data("AAPL", start, end, adjusted=True)
# Spot-check: close on 2020-08-28 should differ by ~4x between raw and adjusted
assert raw_m["close"].iloc[0] / adj_m["close"].iloc[0] == pytest.approx(4.0, abs=0.1)
```

**5. Unit tests pass without DB**

```
uv run pytest test/test_granularity.py test/test_adjustment.py -v
```

All tests pass with `MT_TIMESCALE_DB_URL` unset; integration tests are
skipped via `pytest.mark.skipif`.

## Implementation Notes

### Development Order

1. `Granularity` enum + `GRANULARITY_SOURCE` in `constants.py` (T02)
2. Tests for enum (T03)
3. `data/adjustment.py` (T04) — import `compute_k_factor` from the existing
   package; do not reimplement it
4. Tests for `adjusted()` (T05)
5. Commit checkpoint (T06)
6. `timescale_daily_db.py` (T07)
7. Tests for `TimescaleDailyDataDB` (T08)
8. Extend `get_minute_data`; update `AGGREGATION_VIEWS` keys (T09)
9. Tests for the kwarg (T10)
10. Final build and commit (T11)

### Testing Strategy

Unit tests use synthetic DataFrames and `CaSnapshot` fixtures — no DB needed.
Integration tests are gated on `MT_TIMESCALE_DB_URL` via
`pytest.mark.skipif(not os.getenv("MT_TIMESCALE_DB_URL"), ...)`.

The integration test for AAPL adjusted-close continuity across the
2020-08-31 split is the key real-world validation. If `splits` or `dividends`
are empty for AAPL after the slice 152 migration, the test will fail and that
indicates the one-shot copy in migration 036 did not run (check
`MT_MARKET_DB_URL` was set during the migrate-cold-start run).

### AGGREGATION_VIEWS key rename side effects

The rename from `"5min"` → `"5m"` etc. breaks callers that pass the old
string keys. Audit with:

```
grep -rn '"5min"\|"15min"\|"1hour"\|"4hour"' src/ tests/
```

before committing the rename. No production caller is expected; the only
known consumer is the `get_minute_data` internal path and the existing test
suite for that method.
