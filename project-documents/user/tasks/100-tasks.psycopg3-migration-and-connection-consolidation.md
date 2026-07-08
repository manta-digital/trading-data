---
docType: tasks
slice: psycopg3-migration-and-connection-consolidation
project: trading
lld: user/slices/100-slice.psycopg3-migration-and-connection-consolidation.md
dependencies: [900-foundation-cleanup]
projectState: Initiative 900 complete. MarketDB uses psycopg2, TimescaleMinuteDataDB uses SQLAlchemy. Settings has single db_url field. Both DB hosts reachable from dev machine.
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

## Context Summary

- Working on slice 100: psycopg3 Migration and Connection Consolidation
- This is the foundation slice for Initiative 100 (Data Storage) -- all subsequent slices (101-105) depend on it
- Two DB modules to migrate: `MarketDB` (psycopg2) and `TimescaleMinuteDataDB` (SQLAlchemy)
- Two DB hosts: `.95` (PG 16, daily OHLCV) and `.144` (PG 17 + TimescaleDB, minute data)
- Settings needs dual URL fields replacing the single `db_url`
- psycopg2 imports in `data/base/` modules must be removed to allow dependency removal
- Consumers across CLI, services, news, and backtest modules need updating
- See slice design for full technical decisions and API mapping tables
- Next slice: 101 (Coverage Analysis and Data Inventory)

## Tasks

### 1. Branch and Dependency Setup

- [x] **1.1 Create slice branch and swap dependencies**
  - [x] Verify on `main`, create branch `100-slice.psycopg3-migration-and-connection-consolidation`
  - [x] In `pyproject.toml`: replace `psycopg2-binary>=2.9.9` with `psycopg[binary]>=3.2.0`
  - [x] In `pyproject.toml`: replace `sqlalchemy>=2.0.43` with `psycopg_pool>=3.2.0`
  - [x] Run `uv sync` to install new dependencies
  - [x] Verify `psycopg` and `psycopg_pool` are importable: `python -c "import psycopg; from psycopg_pool import ConnectionPool; print('ok')"`
  - [x] Do NOT run tests yet -- existing code still imports psycopg2/sqlalchemy
  - [x] Commit: `package: swap psycopg2-binary and sqlalchemy for psycopg3 and psycopg_pool`

### 2. Remove psycopg2 from data/base Modules

Must happen immediately after dependency swap -- these modules have top-level `import psycopg2` that will fail at import time (and break transitive imports from other modules).

- [x] **2.1 Remove psycopg2 imports from InstrumentRegistry**
  - [x] In `src/manta_trading/data/base/instrument_registry.py`: remove `import psycopg2` and `from psycopg2.extras import RealDictCursor`
  - [x] Remove or stub all methods that use `psycopg2` connections (these are dead code -- no DB tables exist). Preserve the `Instrument` dataclass and any pure logic methods.
  - [x] If methods are removed, add a module-level comment or docstring noting that DB methods will be implemented in slice 103
  - [x] Success: `python -c "from manta_trading.data.base.instrument_registry import Instrument"` works

- [x] **2.2 Remove psycopg2 imports from TradingCalendar**
  - [x] In `src/manta_trading/data/base/trading_calendar.py`: remove `import psycopg2` and `from psycopg2.extras import RealDictCursor`
  - [x] Remove or stub all methods that use `psycopg2` connections (dead code). Preserve the `Holiday`, `SessionHours` dataclasses and pure logic (session classification, bar count calculations, etc.)
  - [x] Preserve `SessionType` imports and re-exports used by other modules
  - [x] If methods are removed, add a module-level comment or docstring noting that DB methods will be implemented in slice 104
  - [x] Success: `python -c "from manta_trading.data.base.trading_calendar import TradingCalendar"` works
  - [x] Commit: `refactor: remove psycopg2 imports from data/base modules`

### 3. Settings Dual-URL Fields

- [x] **3.1 Add market_db_url and timescale_db_url to Settings**
  - [x] In `src/manta_trading/config/__init__.py`: remove `db_url: str | None = None`
  - [x] Add `market_db_url: str | None = None` (env var: `MT_MARKET_DB_URL`)
  - [x] Add `timescale_db_url: str | None = None` (env var: `MT_TIMESCALE_DB_URL`)
  - [x] Success: `Settings()` accepts both new fields, old `db_url` is gone
  - [x] Commit: `feat: add dual database URL fields to Settings`

### 4. Test Infrastructure

- [x] **4.1 Add shared DB fixtures to conftest.py**
  - [x] Create or update `test/conftest.py` with fixtures for DB availability
  - [x] `market_db_url` fixture: reads `MT_MARKET_DB_URL` env var, skips if not set
  - [x] `timescale_db_url` fixture: reads `MT_TIMESCALE_DB_URL` env var, skips if not set
  - [x] Success: fixtures importable and skip correctly when env vars absent
  - [x] Commit: `test: add shared DB URL fixtures to conftest`

### 5. MarketDB Migration

- [x] **5.1 Migrate MarketDB constructor and connection management**
  - [x] Change constructor to accept `conninfo: str` and `batch_size: int = 500`
  - [x] Replace `psycopg2.pool.SimpleConnectionPool` with `psycopg_pool.ConnectionPool(conninfo, min_size=1, max_size=5)`
  - [x] Replace all `import psycopg2` / `from psycopg2` imports with `import psycopg` / `from psycopg` / `from psycopg_pool`
  - [x] `__enter__`/`__exit__`: open/close the pool (not a single connection)
  - [x] Remove `__aenter__`/`__aexit__`/`aclose` (fake async wrappers)
  - [x] Remove `connect()`/`close()` instance methods that manage `self.conn`/`self.cur`
  - [x] Remove `self.conn`, `self.cur` instance variables
  - [x] Pool creation must raise on failure, not return None
  - [x] `__enter__` must raise on failed pool creation, not log and continue
  - [x] Success: MarketDB initializes with a conninfo string, manages pool lifecycle via context manager

- [x] **5.2 Migrate MarketDB read methods**
  - [x] Each read method acquires its own connection via `with self._pool.connection() as conn:`
  - [x] Methods to migrate: `readDailyOHLCVAdjusted`, `readLRUSymbolList`, `readLastUpdatedDay`, `readLastUpdatedDayBatch`, `readSymbolsAtDate`, `getErrorInfo`, `columnExists`
  - [x] `%s` placeholders are compatible -- no change needed for query strings
  - [x] `cur.fetchone()` / `cur.fetchall()` are compatible -- no change needed
  - [x] Success: all read methods work with per-method connection acquisition

- [x] **5.3 Migrate MarketDB write methods**
  - [x] `writeDailyOHLCVAdjusted`: replace `psycopg2.extras.execute_values` with `cur.executemany()`. Remove the `execute_values` import. Update the INSERT query to use standard `VALUES (%s, ...)` syntax (not the `VALUES %s` template that `execute_values` uses). Keep `ON CONFLICT DO NOTHING`.
  - [x] `writeSymbolList`: already uses `cur.executemany()` -- just update connection acquisition pattern
  - [x] `incrementErrorCount`, `resetErrorCount`: update connection acquisition pattern, ensure explicit `conn.commit()`
  - [x] Success: all write methods work with psycopg3 connection pool

- [x] **5.4 Migrate MarketDB DDL and utility methods**
  - [x] `createSecuritiesDatabase`: use `psycopg.connect(conninfo, autocommit=True)` directly (not pool). Replace `psycopg2.extensions.quote_ident(name, conn)` with `psycopg.sql.Identifier(name)` and `psycopg.sql.SQL` for the CREATE DATABASE statement.
  - [x] `createTableSymbolList`, `createTableDailyOHLCVAdjusted`, `createTableObjectsLastUpdated`, `updateTableSymbolList`: update connection acquisition pattern
  - [x] `verifyDatabase`: no logic change, just inherits updated methods
  - [x] Remove `useFloatAdapter` static method and the `DEC2FLOAT` type adapter -- psycopg3 handles numeric types natively. If NUMERIC columns need to return float instead of Decimal, configure via pool's `configure` callback or handle in the calling code (pandas already converts).
  - [x] Remove `showError` method (uses psycopg2-specific diagnostics). Verify no callers: `grep -r "showError" src/`
  - [x] Remove or update the `__main__` block at the bottom of the file (uses old env vars and constructor pattern)
  - [x] Remove the `async cleanup()` method (fake async). Verify no callers: `grep -r "\.cleanup\(\)" src/`
  - [x] Verify removed async methods have no callers: `grep -r "__aenter__\|__aexit__\|\.aclose" src/`
  - [x] Success: DDL methods work, no psycopg2 imports remain in marketdb.py, no dangling references to removed methods

- [x] **5.5 Update MarketDB tests**
  - [x] In `test/unit/testmarketdb.py`: change setUp to use `MT_MARKET_DB_URL` env var and pass conninfo string
  - [x] Use the `market_db_url` fixture from conftest (or skip if env var not set)
  - [x] Convert from `unittest.IsolatedAsyncioTestCase` to `unittest.TestCase` (or pytest class)
  - [x] Remove `test_aclose_method` (async wrapper removed)
  - [x] Update mock patterns: mock `psycopg_pool.ConnectionPool` instead of `psycopg2.pool.SimpleConnectionPool`
  - [x] Update `test_readSymbolsAtDate` mocks to match new connection pattern (no more `self.cur` instance var)
  - [x] All integration tests (test_tables_exist, test_read/write_daily_ohlcv, test_write_symbol_list, test_error_tracking) should use real DB when available, skip when not
  - [x] Success: `pytest test/unit/testmarketdb.py -v` passes (or skips DB tests if DB unavailable)
  - [x] Commit: `refactor: migrate MarketDB from psycopg2 to psycopg3`

### 6. TimescaleMinuteDataDB Migration

- [x] **6.1 Migrate TimescaleMinuteDataDB constructor and connection management**
  - [x] Change constructor to accept `conninfo: str` instead of `db_config: dict`
  - [x] Replace SQLAlchemy engine creation with `ConnectionPool(conninfo, min_size=4, max_size=10, max_lifetime=3600.0, configure=self._configure_connection)`
  - [x] Replace all `from sqlalchemy import ...` imports
  - [x] Add `_configure_connection` method that sets timezone, work_mem, statement_timeout, and TimescaleDB-specific session parameters
  - [x] Replace `engine.dispose()` in `close()` with `pool.close()`
  - [x] Success: TimescaleMinuteDataDB initializes with a conninfo string, no SQLAlchemy imports

- [x] **6.2 Migrate TimescaleMinuteDataDB write method (COPY)**
  - [x] Replace the `write_minute_data_bulk` method: use `pool.connection()` + `cursor.copy()` instead of `engine.begin()` + `raw_conn.cursor()` + `copy_expert()`
  - [x] Keep the pandas CSV generation approach (StringIO buffer) -- write the buffer content via `copy.write(csv_buffer.getvalue())` inside psycopg3's `cursor.copy()` context manager
  - [x] Ensure proper transaction handling: commit on success, rollback on failure
  - [x] Data validation and type conversion logic stays the same
  - [x] Success: COPY bulk writes function correctly with psycopg3

- [x] **6.3 Migrate TimescaleMinuteDataDB read methods**
  - [x] `get_minute_data`: replace `pd.read_sql_query(query, engine, params=...)` with `cur.execute(sql, params)` + manual DataFrame construction. Ensure DatetimeIndex with UTC timezone, float64 for OHLC, int64 for volume.
  - [x] `_get_aggregated_data`: same pattern as `get_minute_data`. Keep the `aggregation_views` dict and view name whitelist. Replace `text()` wrapper with plain SQL string (psycopg3 uses `%s` params directly, not SQLAlchemy's `:param` syntax). Note: view name is validated against a whitelist and inserted via f-string -- this is safe because the whitelist is hardcoded.
  - [x] `get_coverage_analysis`: replace `engine.begin()` + `text()` with `pool.connection()` + cursor. Replace `fetchone()` attribute access (SQLAlchemy Row) with tuple indexing or `row_factory=dict_row`.
  - [x] `get_system_metrics`: same pattern as `get_coverage_analysis`
  - [x] Success: all read methods return correct data types and structures

- [x] **6.4 Update TimescaleMinuteDataDB tests**
  - [x] In `test/unit/testtimescaleminutedatadb.py`: change setUp to use `MT_TIMESCALE_DB_URL` env var
  - [x] Use the `timescale_db_url` fixture from conftest (or skip if env var not set)
  - [x] Update mocks: `create_engine` -> `ConnectionPool`, `text()` -> plain SQL
  - [x] Update connection pool parameter assertions (min_size/max_size instead of pool_size/max_overflow)
  - [x] Update query execution assertions to match psycopg3 cursor patterns
  - [x] Remove SQLAlchemy import from test file
  - [x] Success: `pytest test/unit/testtimescaleminutedatadb.py -v` passes (or skips DB tests if DB unavailable)
  - [x] Commit: `refactor: migrate TimescaleMinuteDataDB from SQLAlchemy to psycopg3`

### 7. Consumer Updates

- [x] **7.1 Update CLI data commands**
  - [x] In `src/manta_trading/cli/commands/data.py`: update `_create_market_db` to use `settings.market_db_url` directly as conninfo (remove URL parsing into individual params)
  - [x] Fix silent exit: add `print_error("MT_MARKET_DB_URL not configured. Set the environment variable or add it to your .env file.")` before `raise typer.Exit(1)`
  - [x] Success: `mt data daily update AAPL` works with `MT_MARKET_DB_URL` set; prints error message when not set

- [x] **7.2 Update news.py MarketDB consumer**
  - [x] In `src/manta_trading/news/news.py:240`: replace `MARKET_PSQL_*` env var construction with `MT_MARKET_DB_URL` env var (or Settings instance if available in context)
  - [x] Pass URL string directly to `MarketDB(conninfo=...)` constructor
  - [x] Success: news module constructs MarketDB with URL-based connection

- [x] **7.3 Update backtest/bt.py MarketDB consumer**
  - [x] In `src/manta_trading/backtest/bt.py:48`: replace `MARKET_PSQL_*` env var construction with `MT_MARKET_DB_URL`
  - [x] Pass URL string directly to `MarketDB(conninfo=...)` constructor
  - [x] Success: backtest module constructs MarketDB with URL-based connection

- [x] **7.4 Update timescale_init.py**
  - [x] In `src/manta_trading/market/timescale_init.py`: replace `TimescaleDBConfig` usage with direct `conninfo` string from `MT_TIMESCALE_DB_URL` env var or Settings
  - [x] Update `TimescaleMinuteDataDB` construction to pass conninfo string
  - [x] Remove `from manta_trading.market.config import TimescaleDBConfig` import
  - [x] Success: timescale_init constructs TimescaleMinuteDataDB with URL-based connection
  - [x] Commit: `refactor: update all DB consumers to use URL-based connection`

### 8. Config Cleanup

- [x] **8.1 Remove TimescaleDBConfig from market/config.py**
  - [x] In `src/manta_trading/market/config.py`: remove `TimescaleDBConfig` class and the `Config` wrapper class
  - [x] Keep `ChunkingConfig` class (actively used by `chunking_strategy.py`)
  - [x] Verify no remaining imports of `TimescaleDBConfig` across the codebase
  - [x] Success: `config.py` contains only `ChunkingConfig`, no psycopg2 or SQLAlchemy references
  - [x] Commit: `refactor: remove TimescaleDBConfig, keep ChunkingConfig`

### 9. Final Verification

- [x] **9.1 Verify no old driver imports remain**
  - [x] Run: `grep -r "import psycopg2" src/` -- expected: no output
  - [x] Run: `grep -r "from psycopg2" src/` -- expected: no output
  - [x] Run: `grep -r "from sqlalchemy" src/` -- expected: no output
  - [x] Run: `grep -r "import sqlalchemy" src/` -- expected: no output
  - [x] Run: `grep -r "TimescaleDBConfig" src/` -- expected: no output
  - [x] Success: zero matches for all old imports

- [x] **9.2 Run full test suite**
  - [x] Run: `pytest test/ -v`
  - [x] All tests pass or skip (DB-dependent tests skip when env vars not set)
  - [x] No import errors from any module in the package

- [x] **9.3 Verify CLI commands work end-to-end**
  - [x] Requires `MT_MARKET_DB_URL` set to the daily DB (.95)
  - [x] Run: `mt data daily update AAPL --output-size compact` -- should fetch and write data
  - [x] Run: `mt --version` -- should print version (sanity check nothing broke)
  - [x] Run: `mt config list` -- should show `market_db_url` and `timescale_db_url` fields

- [x] **9.4 Verify dependency state**
  - [x] Run: `uv pip list | grep -i psycopg` -- should show psycopg, psycopg-binary, psycopg-pool (no psycopg2)
  - [x] Run: `uv pip list | grep -i sqlalchemy` -- should show no output
  - [x] Commit any remaining changes
  - [x] Success: all verification checks pass, slice is complete
