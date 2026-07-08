---
docType: tasks
slice: schema-instrument-registry-and-trading-calendar-tables
project: trading
lld: user/slices/102-slice.schema-instrument-registry-and-trading-calendar-tables.md
dependencies: [100]
projectState: Slice 100 (psycopg3 migration) and slice 101 (coverage analysis) are complete. TimescaleMinuteDataDB uses psycopg3 ConnectionPool with _ensure_pool() pattern. Settings has timescale_db_url. No schema migration infrastructure exists yet. The data/base/ modules (InstrumentRegistry, TradingCalendar) define dataclasses but have no backing tables. python-dateutil is an existing dependency.
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

## Context Summary
- Working on slice 102: Schema - Instrument Registry and Trading Calendar Tables
- Slices 100 (psycopg3 migration) and 101 (coverage analysis) are complete
- This slice creates DB tables on the TimescaleDB host that slices 103, 104, and 105 depend on
- Tables: `instruments`, `provider_symbol_mapping`, `trading_calendars`, `trading_holidays`, `schema_migrations`
- Also adds nullable `instrument_id BIGINT` column to existing `minute_ohlcv` hypertable
- Seeds NYSE/NASDAQ calendar data (market hours + holidays 2020-2026)
- Delivers a migration runner on `TimescaleMinuteDataDB` and `mt data migrate` CLI command
- No application-layer code beyond the migration infrastructure
- Next planned slices: 103 (Instrument Registry Integration), 104 (Trading Calendar Integration)

---

## Tasks

### Task 1: Create `schema/` package with `__init__.py`

- [x] **Create `src/manta_trading/market/schema/` directory and `__init__.py`**
  - [x] Create `src/manta_trading/market/schema/__init__.py` (empty or minimal docstring)
  - [x] Success: package is importable — `from manta_trading.market.schema import migrations` will work after Task 2

### Task 2: Create migration definitions in `schema/migrations.py`

- [x] **Create `src/manta_trading/market/schema/migrations.py` with all 9 migration definitions**
  - [x] Define `MIGRATIONS` as an ordered list of dicts, each with `id`, `description`, and `sql` keys
  - [x] Migration `001_schema_migrations`: CREATE TABLE IF NOT EXISTS `schema_migrations` (migration_id VARCHAR(64) PK, applied_at TIMESTAMPTZ DEFAULT NOW(), description TEXT)
  - [x] Migration `002_instruments`: CREATE TABLE IF NOT EXISTS `instruments` with all columns and indexes per slice design (BIGSERIAL PK, canonical_id UNIQUE, symbol, asset_class, venue, currency, tick_size, lot_size, trading_calendar_id, adjustment_policy, active, metadata JSONB, created_at, updated_at, 3 indexes)
  - [x] Migration `003_provider_symbol_mapping`: CREATE TABLE IF NOT EXISTS with FK to instruments, partial unique index on (provider, provider_symbol) WHERE valid_to IS NULL, index on instrument_id
  - [x] Migration `004_trading_calendars`: CREATE TABLE IF NOT EXISTS with calendar_id VARCHAR(32) PK, exchange_name, timezone, market_open/close TIME, extended_open/close TIME, has_extended_hours, timestamps
  - [x] Migration `005_trading_holidays`: CREATE TABLE IF NOT EXISTS with FK to trading_calendars, unique index on (calendar_id, holiday_date), index on holiday_date
  - [x] Migration `006_minute_ohlcv_instrument_id`: ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS instrument_id BIGINT (nullable, no FK, no index)
  - [x] Migrations `007_seed_nyse_calendar` and `008_seed_nasdaq_calendar`: placeholder SQL strings — actual INSERT SQL will be generated from seed_calendar.py (Task 3). Use `INSERT ... ON CONFLICT DO NOTHING` for idempotency
  - [x] Migration `009_instruments_calendar_fk`: ALTER TABLE instruments ADD CONSTRAINT fk_instruments_calendar FOREIGN KEY (trading_calendar_id) REFERENCES trading_calendars(calendar_id)
  - [x] Each migration's SQL must be idempotent (IF NOT EXISTS, ON CONFLICT DO NOTHING, etc.)
  - [x] Success: `from manta_trading.market.schema.migrations import MIGRATIONS` returns a list of 9 dicts, each with `id`, `description`, `sql` keys, all SQL strings are syntactically valid

### Task 3: Create calendar seed data in `schema/seed_calendar.py`

- [x] **Create `src/manta_trading/market/schema/seed_calendar.py` with holiday generation logic**
  - [x] Define `NYSE_CALENDAR` and `NASDAQ_CALENDAR` dicts with calendar metadata (calendar_id, exchange_name, timezone, market hours, extended hours) per slice design
  - [x] Implement `compute_easter(year: int) -> date` using the Anonymous Gregorian algorithm (Butcher's algorithm) — do not add new dependencies; `dateutil.easter` is acceptable since `python-dateutil` is already a dependency
  - [x] Implement `generate_holidays(calendar_id: str, start_year: int, end_year: int) -> list[dict]` that returns holiday dicts with keys: `calendar_id`, `holiday_date`, `holiday_name`, `market_status`, `early_close_time`, `late_open_time`
  - [x] Handle all NYSE holiday types:
    1. [x] Fixed-date with weekend adjustment (New Year's Day, Juneteenth from 2022, Independence Day, Christmas Day) — observed Friday if Saturday, Monday if Sunday
    2. [x] Relative holidays (MLK Day = 3rd Mon Jan, Presidents' Day = 3rd Mon Feb, Memorial Day = last Mon May, Labor Day = 1st Mon Sep, Thanksgiving = 4th Thu Nov)
    3. [x] Good Friday (Friday before Easter Sunday)
    4. [x] Early close days: day before Independence Day at 13:00 (if weekday), Black Friday (day after Thanksgiving) at 13:00, Christmas Eve at 13:00 (if weekday and not weekend-adjacent holiday)
  - [x] Implement `generate_calendar_insert_sql(calendar: dict) -> str` — returns INSERT SQL for a trading_calendars row with ON CONFLICT DO NOTHING
  - [x] Implement `generate_holidays_insert_sql(holidays: list[dict]) -> str` — returns INSERT SQL for trading_holidays rows with ON CONFLICT DO NOTHING
  - [x] Wire the generated SQL into migrations 007 and 008 in `migrations.py` — either by importing and calling at module level, or by having the migration runner call these functions. Preferred: generate SQL at import time so `MIGRATIONS` is a static list
  - [x] Success: `generate_holidays("NYSE", 2020, 2026)` returns a list of ~80-90 holiday dicts covering all standard closures and early closes

### Task 4: Unit tests for calendar seed data

- [x] **Create `test/unit/test_seed_calendar.py` with tests for holiday generation**
  - [x] Test `compute_easter` (or equivalent) for known years: 2020 (Apr 12), 2021 (Apr 4), 2024 (Mar 31), 2025 (Apr 20), 2026 (Apr 5)
  - [x] Test Good Friday dates derived from Easter for the same years
  - [x] Test 2024 Thanksgiving = Nov 28 (4th Thursday of November)
  - [x] Test 2024 MLK Day = Jan 15 (3rd Monday of January)
  - [x] Test 2024 Memorial Day = May 27 (last Monday of May)
  - [x] Test weekend adjustment: when July 4 falls on Saturday (2020), observed = July 3 (Friday); when on Sunday (2021), observed = July 5 (Monday)
  - [x] Test Juneteenth not present before 2022, present from 2022 onward
  - [x] Test early close entries exist: Black Friday, day before July 4th, Christmas Eve (with `market_status='early_close'` and `early_close_time='13:00'`)
  - [x] Test `generate_calendar_insert_sql` produces valid SQL with ON CONFLICT DO NOTHING
  - [x] Test `generate_holidays_insert_sql` produces valid SQL with ON CONFLICT DO NOTHING
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/test_seed_calendar.py -v`

**Commit:** `feat: add schema migration definitions and calendar seed data`

### Task 5: Add `apply_schema_migrations()` to `TimescaleMinuteDataDB`

- [x] **Add migration runner method to `TimescaleMinuteDataDB` in `timescale_minute_db.py`**
  - [x] Import `MIGRATIONS` from `manta_trading.market.schema.migrations`
  - [x] Add `apply_schema_migrations(self) -> list[str]` method
  - [x] Bootstrap: check if `schema_migrations` table exists via `information_schema.tables`; if not, execute migration 001 SQL directly
  - [x] Read already-applied migration IDs: `SELECT migration_id FROM schema_migrations`
  - [x] For each migration in `MIGRATIONS` not already applied:
    1. [x] Execute the migration SQL within a transaction
    2. [x] INSERT into `schema_migrations` (migration_id, description)
    3. [x] Commit the transaction
  - [x] Return list of newly applied migration IDs
  - [x] Each migration runs in its own transaction — a failure mid-sequence leaves prior migrations committed
  - [x] Use existing pool pattern: `pool = self._ensure_pool(); with pool.connection() as conn:`
  - [x] Log each applied migration at INFO level
  - [x] Success: method exists, imports correctly, follows existing code patterns

### Task 6: Unit tests for migration runner

- [x] **Create `test/unit/test_schema_migrations.py` with tests for the migration runner**
  - [x] Test `apply_schema_migrations` with all migrations pending — mock connection to verify each migration's SQL is executed and recorded in schema_migrations
  - [x] Test `apply_schema_migrations` with all migrations already applied — mock returns all IDs from schema_migrations, verify no SQL executed, returns empty list
  - [x] Test `apply_schema_migrations` with partial state — some migrations applied, verify only pending ones are executed
  - [x] Test bootstrap path — schema_migrations table does not exist, verify migration 001 is executed first via information_schema check
  - [x] Test failure mid-sequence — mock a migration to raise an exception, verify prior migrations are committed and the failed one is not recorded
  - [x] Test `MIGRATIONS` list integrity: all entries have `id`, `description`, `sql` keys; IDs are unique; IDs are in sorted order
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/test_schema_migrations.py -v`

**Commit:** `feat: add schema migration runner to TimescaleMinuteDataDB`

### Task 7: Add `mt data migrate` CLI command

- [x] **Add migration CLI command to `cli/commands/data.py`**
  - [x] Add `migrate` command to `data_app` (top-level, not under daily_app or minute_app)
  - [x] Command creates `TimescaleMinuteDataDB` from settings via `_create_timescale_db(ctx)`
  - [x] Calls `db.apply_schema_migrations()`
  - [x] On success with applied migrations: print each applied migration ID and description
  - [x] On success with no migrations: print "Schema is up to date"
  - [x] Support `--json` flag for machine-readable output (list of applied migration IDs or empty list)
  - [x] Use try/finally to call `db.close()`
  - [x] Success: `mt data migrate --help` shows the command; command creates DB and calls migration runner

### Task 8: Unit tests for `mt data migrate` CLI command

- [x] **Add CLI tests to `test/unit/test_cli_data.py`**
  - [x] Test `mt data migrate` with migrations applied — mock `apply_schema_migrations` to return `["001_schema_migrations", "002_instruments"]`, verify output lists them
  - [x] Test `mt data migrate` with no pending migrations — mock returns empty list, verify "up to date" message
  - [x] Test `mt data migrate --json` — mock returns list, verify JSON output
  - [x] Test `mt data migrate` with missing `MT_TIMESCALE_DB_URL` — verify exit code 1 and error message
  - [x] Success: all tests pass with `uv run python -m pytest test/unit/test_cli_data.py -v`

**Commit:** `feat: add mt data migrate CLI command`

### Task 9: Integration tests for schema migrations

- [x] **Create `test/integration/test_schema_integration.py`**
  - [x] Skip all tests when `MT_TIMESCALE_DB_URL` is not set
  - [x] Test full migration run: create `TimescaleMinuteDataDB`, call `apply_schema_migrations()`, verify returns list of 9 migration IDs
  - [x] Test idempotency: call `apply_schema_migrations()` a second time, verify returns empty list
  - [x] Test `schema_migrations` table contains 9 rows with correct IDs
  - [x] Test `instruments` table exists with expected columns (check via `information_schema.columns`)
  - [x] Test `provider_symbol_mapping` table exists with expected columns
  - [x] Test `trading_calendars` table contains 2 rows (NYSE, NASDAQ) with correct market hours
  - [x] Test `trading_holidays` table contains expected holiday count (~80-90 rows for NYSE)
  - [x] Test specific holiday: 2024-11-28 = Thanksgiving, market_status='closed'
  - [x] Test specific early close: 2024-11-29 = Black Friday, market_status='early_close', early_close_time=13:00
  - [x] Test `minute_ohlcv` has `instrument_id` column (BIGINT, nullable)
  - [x] Test FK constraint: `instruments.trading_calendar_id` references `trading_calendars.calendar_id` — insert an instrument with invalid calendar_id, verify it raises an integrity error
  - [x] Test existing `minute_ohlcv` data is unaffected: run `mt data minute coverage` equivalent query, verify same results as before migrations
  - [x] Success: all integration tests pass when `MT_TIMESCALE_DB_URL` is set; all skip cleanly when not set

**Commit:** `test: add integration tests for schema migrations`

### Task 10: Full validation and documentation

- [x] **Run full test suite and update project documentation**
  - [x] Run `uv run python -m pytest test/unit test/integration --tb=short` — all tests pass
  - [x] Verify no changes to existing `TimescaleMinuteDataDB` read/write methods or continuous aggregates (grep for modified method signatures)
  - [x] Update `CHANGELOG.md` with slice 102 entries under appropriate section:
    - Added: schema migration infrastructure, instruments table, provider_symbol_mapping table, trading_calendars table, trading_holidays table, NYSE/NASDAQ calendar seed data (2020-2026), instrument_id column on minute_ohlcv, `mt data migrate` CLI command
  - [x] Update slice design verification walkthrough with actual results if needed
  - [x] Mark slice 102 as complete in `user/slices/102-slice.schema-instrument-registry-and-trading-calendar-tables.md` (status: complete, dateUpdated)
  - [x] Check off slice 102 in `user/architecture/100-slices.data-storage.md`
  - [x] Success: all tests pass, CHANGELOG updated, slice marked complete

**Commit:** `docs: mark slice 102 complete, update changelog`
