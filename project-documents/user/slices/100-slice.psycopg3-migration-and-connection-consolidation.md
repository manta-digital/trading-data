---
docType: slice
parent: user/architecture/100-slices.data-storage.md
project: trading
sliceIndex: 100
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

# Slice Design: psycopg3 Migration and Connection Consolidation

## Overview

Migrate all database access from psycopg2 and SQLAlchemy to psycopg3 (`psycopg`) with `psycopg_pool.ConnectionPool`. This is the foundation slice for Initiative 100 (Data Storage) -- every subsequent slice depends on the unified DB access layer established here.

**Scope:** Two production DB modules (`MarketDB`, `TimescaleMinuteDataDB`), two Settings URL fields, dependency swap, consumer updates, test migration. The `data/base/` modules (`InstrumentRegistry`, `TradingCalendar`) are *not* migrated here -- they have no working DB tables yet and will be written directly for psycopg3 in slices 103/104.

## Value

- **Removes two redundant dependencies** (psycopg2-binary, sqlalchemy) in favor of one modern stack (psycopg + psycopg_pool)
- **Establishes the connection pattern** all future data storage slices build on
- **Fixes silent failure bugs** in `_create_market_db` and `MarketDB.__enter__`
- **Simplifies COPY bulk writes** -- psycopg3's `cursor.copy()` is first-class, replacing the SQLAlchemy-to-raw-connection drill-through

## Dependencies

- **Depends on:** Initiative 900 (complete) -- CLI framework, Settings, logging
- **Blocks:** All other slices in Initiative 100 (101-105)
- **No external blockers:** Both DB hosts verified reachable from dev machine

## Technical Scope

### In Scope

1. **MarketDB migration** (psycopg2 -> psycopg3)
2. **TimescaleMinuteDataDB migration** (SQLAlchemy -> psycopg3 + psycopg_pool)
3. **Settings dual-URL fields** (`market_db_url`, `timescale_db_url`)
4. **Consumer updates** (CLI data commands, MarketService, news.py, backtest/bt.py, HistoricalMinuteService, csv_export_service, timescale_minute_coverage, timescale_init)
5. **Dependency swap** in pyproject.toml
6. **Test migration** for both modules
7. **Fix `_create_market_db` silent exit** in CLI data commands
8. **Remove psycopg2 imports from `data/base/` modules** -- `InstrumentRegistry` and `TradingCalendar` have top-level `import psycopg2` / `from psycopg2.extras import RealDictCursor` that will break at import time when `psycopg2-binary` is removed. Their DB methods are dead code (no tables exist). Remove the psycopg2 imports and stub or delete the DB-dependent methods, preserving the dataclasses (`Instrument`, `Holiday`, `SessionHours`, etc.) and any pure logic that other modules import. Full psycopg3 rewrites happen in slices 103/104.

### Out of Scope

- `InstrumentRegistry` and `TradingCalendar` full psycopg3 rewrites (slices 103/104) -- this slice only removes psycopg2 imports and dead DB methods
- Schema changes (slice 102)
- Coverage analysis rewrite (slice 101)
- `TimescaleMonitor` changes (deferred to Initiative 140)

## Technical Decisions

### 1. Settings: Dual URL Fields

Replace the single `db_url` field with two explicit fields. Both use the `MT_` prefix already established by pydantic-settings.

```python
# src/manta_trading/config/__init__.py
class Settings(BaseSettings):
    # ... existing fields ...
    market_db_url: str | None = None      # env: MT_MARKET_DB_URL
    timescale_db_url: str | None = None   # env: MT_TIMESCALE_DB_URL
```

The existing `db_url` field is currently used only by `_create_market_db` in the CLI. It will be replaced by `market_db_url`. No backwards-compatibility shim -- just change the field and update the one consumer.

The `.env` file currently has `MARKET_PSQL_*` and `TRADING_PSQL_*` individual params. Users will add `MT_MARKET_DB_URL` and `MT_TIMESCALE_DB_URL` as connection strings. The old env vars are not removed from `.env` (user's file) but are no longer read by any application code.

### 2. MarketDB Migration Strategy

MarketDB is the larger surface area (~890 lines, ~20 methods). Key API mappings:

| psycopg2 pattern | psycopg3 equivalent |
|---|---|
| `psycopg2.pool.SimpleConnectionPool(min, max, **params)` | `psycopg_pool.ConnectionPool(conninfo, min_size=N, max_size=N)` |
| `pool.getconn()` / `pool.putconn(conn)` | `pool.getconn()` / `pool.putconn(conn)` or `pool.connection()` context manager |
| `conn.cursor()` | `conn.cursor()` (compatible) |
| `cur.execute(sql, (params,))` with `%s` | `cur.execute(sql, (params,))` with `%s` (compatible) |
| `psycopg2.extras.execute_values(cur, sql, data, page_size=N)` | `cur.executemany(sql, data)` or `cur.copy()` for bulk |
| `cur.fetchone()` / `cur.fetchall()` | Same API (compatible) |
| `psycopg2.extensions.quote_ident(name, conn)` | `psycopg.sql.Identifier(name)` |
| `psycopg2.extensions.new_type()` / `register_type()` (DEC2FLOAT adapter) | Not needed -- psycopg3 returns Python floats for numeric by default via `FloatLoader`, or configure via adapter |
| `conn.autocommit = True` | `conn.autocommit = True` (compatible) |
| `conn.commit()` / `conn.rollback()` | Same API (compatible) |

**Constructor change:** MarketDB currently takes individual params (`_dbname`, `_user`, `_password`, `_host`, `_port`). Change to accept a connection URL string:

```python
class MarketDB:
    def __init__(self, conninfo: str, batch_size: int = 500):
        self._conninfo = conninfo
        self._batch_size = batch_size
        self._pool: ConnectionPool | None = None
```

**Connection management:** Replace the manual `connect()`/`close()` pattern with psycopg_pool's `pool.connection()` context manager where possible. The current pattern of `self.conn` / `self.cur` as instance state is fragile (shared mutable state, no concurrency safety). Each method should acquire and release its own connection:

```python
def read_daily_ohlcv(self, symbol: str, date_from: str, date_to: str | None = None):
    with self._pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            return cur.fetchall()
```

**Context manager:** `__enter__`/`__exit__` should open/close the pool, not manage a single connection. The fake async context manager (`__aenter__`/`__aexit__`) should be removed -- it wraps sync operations and provides no async benefit.

**execute_values replacement:** The `writeDailyOHLCVAdjusted` method uses `psycopg2.extras.execute_values` for bulk inserts with `ON CONFLICT DO NOTHING`. Replace with `cur.executemany()`. For this table's volume (daily data, hundreds of rows per write), `executemany` is sufficient -- no COPY needed.

**DEC2FLOAT adapter removal:** The `useFloatAdapter` static method registers a global psycopg2 type adapter to convert DECIMAL to float. psycopg3 handles numeric->float differently. Use a `configure` callback on the connection pool or set the appropriate loader. Alternatively, since the table columns are `NUMERIC` and the code immediately converts to float via pandas anyway, this may not need explicit configuration.

**createSecuritiesDatabase:** This method connects directly (not via pool) to create the database if it doesn't exist. Use `psycopg.connect(conninfo, autocommit=True)` directly -- pool is not appropriate for DDL against the system database. Update `quote_ident` usage to `psycopg.sql.Identifier`.

**Silent failure fixes:**
- `createConnectionPool()` currently returns `None` on error -- change to raise the exception
- `__enter__` currently logs and continues with `None` connection -- change to raise
- `_create_market_db` in CLI currently does `raise typer.Exit(1)` with no message -- add `print_error("MT_MARKET_DB_URL not configured...")` before exit

### 3. TimescaleMinuteDataDB Migration Strategy

Simpler migration -- SQLAlchemy is used purely as a connection pool around raw SQL.

| SQLAlchemy pattern | psycopg3 equivalent |
|---|---|
| `create_engine(url, poolclass=QueuePool, pool_size=N, ...)` | `ConnectionPool(conninfo, min_size=N, max_size=N, ...)` |
| `engine.begin()` context manager | `pool.connection()` context manager |
| `conn.execute(text(sql), params)` | `cur.execute(sql, params)` |
| `raw_conn.cursor(); cur.copy_expert(sql, file)` | `cur.copy(sql)` context manager with `copy.write(data)` |
| `pd.read_sql_query(query, engine, params=...)` | `cur.execute(sql, params)` + `pd.DataFrame(cur.fetchall(), columns=...)` |
| `engine.dispose()` | `pool.close()` |

**Constructor change:**

```python
class TimescaleMinuteDataDB:
    def __init__(self, conninfo: str):
        self._conninfo = conninfo
        self._pool = ConnectionPool(
            conninfo,
            min_size=4,
            max_size=10,
            max_lifetime=3600.0,
            kwargs={"autocommit": True},
            configure=self._configure_connection,
        )
```

**Connection configuration:** The current SQLAlchemy engine passes `connect_args` with timezone, work_mem, and statement_timeout. Move these to a `configure` callback on the pool:

```python
def _configure_connection(self, conn: Connection) -> None:
    conn.execute("SET timezone = 'UTC'")
    conn.execute("SET work_mem = '512MB'")
    conn.execute("SET statement_timeout = '300s'")
    conn.execute("SET max_parallel_workers_per_gather = 8")
    conn.execute("SET enable_partitionwise_aggregate = on")
    conn.commit()
```

**COPY migration:** The `write_minute_data_bulk` method currently uses `copy_expert()` through SQLAlchemy's raw connection. psycopg3's COPY support is first-class:

```python
with self._pool.connection() as conn:
    with conn.cursor() as cur:
        with cur.copy("COPY minute_ohlcv (time, symbol, open, high, low, close, volume) FROM STDIN WITH (FORMAT CSV)") as copy:
            for row in csv_data:
                copy.write_row(row)
```

Alternatively, continue using the StringIO/CSV approach and write the entire buffer:

```python
with cur.copy("COPY minute_ohlcv (...) FROM STDIN WITH (FORMAT CSV, NULL '')") as copy:
    copy.write(csv_buffer.getvalue())
```

**pd.read_sql_query replacement:** Currently uses `pd.read_sql_query(query, engine, params=...)` which depends on SQLAlchemy. Replace with direct cursor execution:

```python
with self._pool.connection() as conn:
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [desc[0] for desc in cur.description]
        data = cur.fetchall()
        df = pd.DataFrame(data, columns=columns)
        if 'time' in df.columns:
            df.set_index('time', inplace=True)
        return df
```

**Transaction behavior:** psycopg3 connections default to `autocommit=False` with implicit transactions. For read queries, this is fine (auto-rollback on context exit). For writes, explicitly commit. The current code uses `engine.begin()` which auto-commits on context exit -- replicate with `with conn.transaction():` blocks for write operations, or set `autocommit=True` on the pool and manage transactions explicitly.

### 4. Consumer Updates

All consumers that construct `MarketDB` or `TimescaleMinuteDataDB` must be updated:

**MarketDB consumers (4 locations):**

| File | Current pattern | New pattern |
|---|---|---|
| `cli/commands/data.py:47` (`_create_market_db`) | Parses `settings.db_url` into individual params | Use `settings.market_db_url` directly as conninfo |
| `news/news.py:240` | Reads `MARKET_PSQL_*` env vars individually | Use `settings.market_db_url` or env var `MT_MARKET_DB_URL` |
| `backtest/bt.py:48` | Reads `MARKET_PSQL_*` env vars individually | Use `settings.market_db_url` or env var `MT_MARKET_DB_URL` |
| `marketdb.py:892` (`__main__`) | Reads `MARKET_PSQL_*` env vars | Update or remove `__main__` block |

**TimescaleMinuteDataDB consumers (4 locations):**

| File | Current pattern | New pattern |
|---|---|---|
| `market/timescale_minute_coverage.py:13` | Receives `TimescaleMinuteDataDB` instance | No change needed (receives instance) |
| `data/historical_minute/service.py:29` | Receives `TimescaleMinuteDataDB` instance | No change needed (receives instance) |
| `market/csv_export_service.py:15` | Receives `TimescaleMinuteDataDB` instance | No change needed (receives instance) |
| `market/timescale_init.py:12` | Uses `TimescaleDBConfig` to create engine | Use `settings.timescale_db_url` or env var `MT_TIMESCALE_DB_URL` |

**TimescaleDBConfig consumers:**

`timescale_init.py` uses `TimescaleDBConfig` to get connection params and engine config. After migration, it should use `settings.timescale_db_url` directly. The `TimescaleDBConfig` class and `market/config.py` can be removed or reduced to only the `ChunkingConfig` portion (if still used).

### 5. Dependency Swap

In `pyproject.toml`:

- Remove: `psycopg2-binary>=2.9.9`, `sqlalchemy>=2.0.43`
- Add: `psycopg[binary]>=3.2.0`, `psycopg_pool>=3.2.0`

The `[binary]` extra installs `psycopg-binary` which provides a self-contained binary build (no libpq dependency), matching the current `psycopg2-binary` behavior.

### 6. Test Migration

**testmarketdb.py (169 lines):**
- Currently uses `unittest.IsolatedAsyncioTestCase` (unnecessary -- all tests are sync except `test_aclose_method`)
- Reads `MARKET_PSQL_*` env vars in setUp -- change to construct URL from `MT_MARKET_DB_URL`
- Integration tests that hit real DB -- keep as-is but add skip decorator when DB unavailable
- Remove `test_aclose_method` (async wrapper being removed)
- Update mock patterns: `psycopg2.pool.SimpleConnectionPool` -> `psycopg_pool.ConnectionPool`

**testtimescaleminutedatadb.py (674 lines):**
- Reads `TRADING_PSQL_*` env vars -- change to `MT_TIMESCALE_DB_URL`
- Mocks `create_engine` from SQLAlchemy -- change to mock `ConnectionPool`
- Tests that verify SQLAlchemy-specific config (pool_size, QueuePool) -- update to verify ConnectionPool config
- `pd.read_sql_query` usage in production code changes, so test assertions around query patterns need updating

**Shared fixture pattern:** Add a `conftest.py` fixture for DB availability:

```python
@pytest.fixture
def market_db_url():
    url = os.getenv("MT_MARKET_DB_URL")
    if not url:
        pytest.skip("MT_MARKET_DB_URL not set")
    return url
```

## Implementation Notes

### Migration Order

1. **Settings fields** -- add `market_db_url` and `timescale_db_url`, remove `db_url`
2. **MarketDB** -- migrate class, update tests
3. **TimescaleMinuteDataDB** -- migrate class, update tests
4. **Consumer updates** -- CLI commands, services, news, backtest
5. **Dependency swap** -- pyproject.toml, `uv sync`
6. **Cleanup** -- remove `TimescaleDBConfig` (or reduce to `ChunkingConfig`), remove `__main__` block from marketdb.py if no longer useful

### Risk: pd.read_sql_query Removal

`TimescaleMinuteDataDB.get_minute_data()` and `_get_aggregated_data()` use `pd.read_sql_query()` which handles DataFrame construction, type inference, and timezone parsing automatically. The manual replacement (fetchall -> DataFrame) must preserve:
- Correct timezone handling on the `time` column
- Proper numeric types (float64 for OHLC, int64 for volume)
- DatetimeIndex with UTC timezone

Test with real data to verify the DataFrame structure matches.

### Risk: COPY Performance

The current COPY path achieves 13k+ rows/sec. The psycopg3 `cursor.copy()` API should be equivalent or better (eliminates SQLAlchemy overhead), but validate with a performance test against real minute data.

## Success Criteria

1. `MarketDB` uses psycopg3 + psycopg_pool exclusively -- no psycopg2 imports
2. `TimescaleMinuteDataDB` uses psycopg3 + psycopg_pool exclusively -- no SQLAlchemy imports
3. `Settings` has `market_db_url` and `timescale_db_url` fields, `db_url` removed
4. `psycopg2-binary` and `sqlalchemy` removed from pyproject.toml dependencies
5. `psycopg[binary]` and `psycopg_pool` added to pyproject.toml dependencies
6. All existing CLI commands work: `mt data daily update`, `mt data daily sync`, `mt data daily migrate`
7. All existing tests pass (updated for new driver)
8. `_create_market_db` prints an error message when `market_db_url` is not configured
9. No silent failure on connection errors -- `MarketDB.__enter__` raises on failed connection
10. COPY bulk writes in TimescaleMinuteDataDB function correctly

## Verification Walkthrough

Verified during implementation (2026-04-02):

```bash
# 1. Dependency check -- psycopg2 and sqlalchemy are gone
uv pip list | grep -i psycopg
# Actual: psycopg 3.3.3, psycopg-binary 3.3.3, psycopg-pool 3.3.0
uv pip list | grep -i sqlalchemy
# Actual: no output ✓

# 2. Settings fields work
python -c "from manta_trading.config import Settings; s = Settings(); print(f'market={s.market_db_url}, timescale={s.timescale_db_url}')"
# Actual: market=None, timescale=None ✓

# 3. CLI daily commands require MT_MARKET_DB_URL (verified via import, not live DB)
# mt data daily update AAPL --output-size compact
# Requires MT_MARKET_DB_URL set to live DB

# 4. Silent exit fixed -- _create_market_db now prints error message
# Verified in source: print_error(...) before raise typer.Exit(1) ✓

# 5. Run tests
uv run python -m pytest test/unit/testmarketdb.py -v
# Actual: 8 passed, 5 skipped ✓
uv run python -m pytest test/unit/testtimescaleminutedatadb.py -v
# Actual: 20 passed, 2 skipped ✓
uv run python -m pytest test/unit/ -v
# Actual: 472 passed, 7 skipped, 0 failed ✓

# 6. Verify no psycopg2/sqlalchemy imports remain in src/
grep -r "import psycopg2" src/   # no output ✓
grep -r "from psycopg2" src/     # no output ✓
grep -r "from sqlalchemy" src/   # no output ✓
grep -r "import sqlalchemy" src/ # no output ✓
grep -r "TimescaleDBConfig" src/ # no output ✓
```

**Caveats:**
- Integration tests (DB-dependent) skip when `MT_MARKET_DB_URL` / `MT_TIMESCALE_DB_URL` not set
- CLI e2e testing (`mt data daily update`) requires live database access
- `motor` dev dependency needed for news-related test collection

## Effort

4/5 -- Largest slice in Initiative 100. Two major module migrations, multiple consumer updates, comprehensive test updates. Not a find-and-replace: different transaction semantics, COPY API, and DataFrame construction require careful testing against real data.
