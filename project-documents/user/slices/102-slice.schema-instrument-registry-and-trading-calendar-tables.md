---
docType: slice-design
slice: schema-instrument-registry-and-trading-calendar-tables
project: trading
parent: user/architecture/100-slices.data-storage.md
dependencies: [100]
interfaces: [103, 104, 105]
dateCreated: 20260402
dateUpdated: 20260402
status: complete
---

# Slice Design: Schema - Instrument Registry and Trading Calendar Tables

## Overview

Create the database tables that slices 103-105 depend on: an instrument registry (`instruments` + `provider_symbol_mapping`), trading calendar (`trading_calendars` + `trading_holidays`), and a nullable `instrument_id` column on the existing `minute_ohlcv` hypertable. Seed US equity calendar data (NYSE/NASDAQ hours and holidays). All work is SQL migration scripts executed through the existing `TimescaleMinuteDataDB` infrastructure — no new application-layer code beyond the migration runner.

## Value

**Architectural enablement:** Unblocks three downstream slices:
- Slice 103 (Instrument Registry Integration) — needs the `instruments` and `provider_symbol_mapping` tables
- Slice 104 (Trading Calendar Integration) — needs `trading_calendars` and `trading_holidays` tables with seed data
- Slice 105 (Tick Event Hypertable) — needs the `instruments` table for `instrument_id` foreign keys

**For the operator:** After this slice, the schema exists and is queryable. The operator can inspect the calendar data, verify holiday coverage, and confirm the instrument table structure matches the `Instrument` dataclass in `data/base/instrument_registry.py`.

## Technical Scope

### In Scope
- Create `instruments` table on the TimescaleDB host
- Create `provider_symbol_mapping` table on the TimescaleDB host
- Create `trading_calendars` table on the TimescaleDB host
- Create `trading_holidays` table on the TimescaleDB host
- Add nullable `instrument_id` column to `minute_ohlcv` hypertable
- Seed US equity trading calendar data (NYSE/NASDAQ market hours)
- Seed US equity holidays (2020 through 2026, covering the data range we have)
- Migration runner method on `TimescaleMinuteDataDB` to apply schema changes idempotently
- Unit tests for migration logic; integration tests that verify schema against real DB

### Out of Scope
- Populating the `instruments` table with actual instrument data (slice 103)
- Populating `provider_symbol_mapping` with AlphaVantage mappings (slice 103)
- Rewriting `InstrumentRegistry` or `TradingCalendar` application classes (slices 103, 104)
- Backfilling `instrument_id` in `minute_ohlcv` (future work item in slice plan)
- Tick event hypertable creation (slice 105)
- Any changes to `MarketDB` or its tables on the PostgreSQL 16 host

## Dependencies

### Prerequisites
- Slice 100 (psycopg3 migration) — `TimescaleMinuteDataDB` with psycopg3 `ConnectionPool`, `_ensure_pool()` pattern
- TimescaleDB host (`<db-host>`) accessible with `MT_TIMESCALE_DB_URL`

### Interfaces Required
- `TimescaleMinuteDataDB` pool access pattern: `pool = self._ensure_pool(); with pool.connection() as conn:`
- Existing `verifyDatabase()` method in `timescale_minute_db.py` as pattern reference

## Architecture

### Component Structure

```
src/manta_trading/
  market/
    timescale_minute_db.py   ← ADD: apply_schema_migrations() method
    schema/
      migrations.py          ← NEW: migration definitions (SQL + metadata)
      seed_calendar.py       ← NEW: US equity calendar seed data

test/
  unit/
    test_schema_migrations.py    ← NEW: unit tests for migration logic
  integration/
    test_schema_integration.py   ← NEW: integration tests against real DB
```

### Data Flow

Schema migrations are applied via a single entry point:

```
TimescaleMinuteDataDB.apply_schema_migrations()
  → reads migration definitions from schema/migrations.py
  → checks schema_migrations table for already-applied migrations
  → applies pending migrations in order within transactions
  → records each applied migration
```

Calendar seed data is a migration step — it inserts rows via the same mechanism.

## Technical Decisions

### Migration Strategy

**Decision:** Simple, ordered migration table — not a full migration framework.

A `schema_migrations` tracking table records which migrations have been applied:

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);
```

Each migration is a Python dict with `id`, `description`, and `sql` (or `sql_up` for forward-only migrations). Migrations are applied in definition order and are idempotent (use `IF NOT EXISTS`, `IF NOT EXISTS` for columns, etc.). This is intentionally minimal — we don't need rollback, branching, or any framework features. If we ever do, we can adopt Alembic later without conflict since this table is a simple ledger.

### Table Placement: TimescaleDB Host

**Decision:** All new tables go on the TimescaleDB host (`<db-host>`), not the MarketDB host.

**Rationale:**
- `minute_ohlcv` gains `instrument_id` — the FK target must be on the same host
- Tick hypertable (slice 105) will reference `instruments` — same host
- `TradingCalendar` queries will be co-located with minute data for session-classified reads
- MarketDB (`<prototype-host>`) remains the AlphaVantage daily data host; its `symbol_list` is not modified

### instrument_id on minute_ohlcv

**Decision:** Add nullable `BIGINT` column with no FK constraint initially.

The column is added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. No FK constraint is added yet because:
1. The `instruments` table will be empty until slice 103 populates it
2. Existing rows have no `instrument_id` value to validate
3. The FK can be added in slice 103 after the backfill, or deferred further

The column is `BIGINT` to match the `instruments.instrument_id` PK (BIGSERIAL).

**Continuous aggregate safety:** Verified in slice 101 design — all existing continuous aggregates use explicit column lists (`time_bucket, symbol, open, high, low, close, volume`). Adding a column to the base hypertable does not affect them.

### Calendar Data Scope

**Decision:** Seed NYSE calendar with holidays from 2020 through 2026.

This covers:
- The full range of existing minute data (~2019-2024 based on slice 101 fleet summary)
- A buffer through 2026 for ongoing data collection
- All standard NYSE holidays: New Year's Day, MLK Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth (from 2022), Independence Day, Labor Day, Thanksgiving Day, Christmas Day
- Early close days: Day before Independence Day, Black Friday, Christmas Eve (when not on weekend)

NASDAQ follows the NYSE holiday schedule (they share the same closure dates). The `trading_calendars` table stores separate entries for NYSE and NASDAQ, but they reference the same holiday set via `calendar_id`.

### Minimal CLI Surface

**Decision:** One CLI command (`mt data migrate`) to make migration application scriptable. No commands for reading the new tables — those are added in slices 103 and 104.

## Implementation Details

### Database Schema

#### instruments

```sql
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id BIGSERIAL PRIMARY KEY,
    canonical_id VARCHAR(64) NOT NULL UNIQUE,
    symbol VARCHAR(32) NOT NULL,
    asset_class VARCHAR(32) NOT NULL,
    venue VARCHAR(32) NOT NULL,
    currency VARCHAR(8) NOT NULL DEFAULT 'USD',
    tick_size NUMERIC(12,6),
    lot_size INTEGER NOT NULL DEFAULT 1,
    trading_calendar_id VARCHAR(32),
    adjustment_policy VARCHAR(32) NOT NULL DEFAULT 'split_adjusted',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_instruments_symbol ON instruments (symbol);
CREATE INDEX IF NOT EXISTS ix_instruments_canonical_id ON instruments (canonical_id);
CREATE INDEX IF NOT EXISTS ix_instruments_calendar ON instruments (trading_calendar_id);
```

Column alignment with the existing `Instrument` dataclass (`data/base/instrument_registry.py`):

| Dataclass field | Table column | Notes |
|---|---|---|
| `instrument_id: int` | `instrument_id BIGSERIAL` | Auto-generated PK |
| `canonical_id: str` | `canonical_id VARCHAR(64) UNIQUE` | Business key |
| `symbol: str` | `symbol VARCHAR(32)` | Display symbol |
| `asset_class: str` | `asset_class VARCHAR(32)` | e.g. "equity", "option" |
| `venue: str` | `venue VARCHAR(32)` | e.g. "NYSE", "NASDAQ" |
| `currency: str = 'USD'` | `currency VARCHAR(8) DEFAULT 'USD'` | |
| `tick_size: float \| None` | `tick_size NUMERIC(12,6)` | Nullable |
| `lot_size: int = 1` | `lot_size INTEGER DEFAULT 1` | |
| `trading_calendar_id: str \| None` | `trading_calendar_id VARCHAR(32)` | FK added in migration 009 |
| `adjustment_policy: str` | `adjustment_policy VARCHAR(32) DEFAULT 'split_adjusted'` | |
| `active: bool = True` | `active BOOLEAN DEFAULT TRUE` | |
| `metadata: dict \| None` | `metadata JSONB` | Flexible extension |

#### provider_symbol_mapping

```sql
CREATE TABLE IF NOT EXISTS provider_symbol_mapping (
    mapping_id BIGSERIAL PRIMARY KEY,
    instrument_id BIGINT NOT NULL REFERENCES instruments(instrument_id),
    provider VARCHAR(32) NOT NULL,
    provider_symbol VARCHAR(64) NOT NULL,
    valid_from DATE,
    valid_to DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_provider_mapping_unique
    ON provider_symbol_mapping (provider, provider_symbol)
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS ix_provider_mapping_instrument
    ON provider_symbol_mapping (instrument_id);
```

The partial unique index on `(provider, provider_symbol) WHERE valid_to IS NULL` ensures only one active mapping per provider+symbol combination, while allowing historical mappings (where `valid_to` is set) to coexist.

#### trading_calendars

```sql
CREATE TABLE IF NOT EXISTS trading_calendars (
    calendar_id VARCHAR(32) PRIMARY KEY,
    exchange_name VARCHAR(64) NOT NULL,
    timezone VARCHAR(64) NOT NULL,
    market_open TIME NOT NULL,
    market_close TIME NOT NULL,
    extended_open TIME,
    extended_close TIME,
    has_extended_hours BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Column alignment with `TradingCalendar` class attributes (`data/base/trading_calendar.py`):

| Class attribute | Table column | Notes |
|---|---|---|
| `calendar_id` | `calendar_id VARCHAR(32) PK` | e.g. "NYSE", "NASDAQ" |
| `timezone` | `timezone VARCHAR(64)` | e.g. "America/New_York" |
| `market_open_time` | `market_open TIME` | e.g. 09:30 |
| `market_close_time` | `market_close TIME` | e.g. 16:00 |
| `extended_open_time` | `extended_open TIME` | e.g. 04:00 |
| `extended_close_time` | `extended_close TIME` | e.g. 20:00 |
| `has_extended_hours` | `has_extended_hours BOOLEAN` | |

#### trading_holidays

```sql
CREATE TABLE IF NOT EXISTS trading_holidays (
    holiday_id BIGSERIAL PRIMARY KEY,
    calendar_id VARCHAR(32) NOT NULL REFERENCES trading_calendars(calendar_id),
    holiday_date DATE NOT NULL,
    holiday_name VARCHAR(128) NOT NULL,
    market_status VARCHAR(16) NOT NULL,
    early_close_time TIME,
    late_open_time TIME,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_trading_holidays_unique
    ON trading_holidays (calendar_id, holiday_date);

CREATE INDEX IF NOT EXISTS ix_trading_holidays_date
    ON trading_holidays (holiday_date);
```

`market_status` values: `'closed'`, `'early_close'`, `'late_open'`. These align with the `Holiday.market_status` field in `data/base/trading_calendar.py`. Note: slice 104 will replace these string literals with a `StrEnum` in application code; the DB column stores the string value regardless.

#### minute_ohlcv alteration

```sql
ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS instrument_id BIGINT;
```

No FK, no index — just the nullable column. Index and FK are deferred to the backfill step (future work).

#### schema_migrations tracking

```sql
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id VARCHAR(64) PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT
);
```

### Migration Definitions

Migrations are defined in `schema/migrations.py` as an ordered list:

```python
MIGRATIONS = [
    {
        "id": "001_schema_migrations",
        "description": "Create schema_migrations tracking table",
        "sql": "CREATE TABLE IF NOT EXISTS schema_migrations ..."
    },
    {
        "id": "002_instruments",
        "description": "Create instruments table",
        "sql": "CREATE TABLE IF NOT EXISTS instruments ..."
    },
    {
        "id": "003_provider_symbol_mapping",
        "description": "Create provider_symbol_mapping table",
        "sql": "CREATE TABLE IF NOT EXISTS provider_symbol_mapping ..."
    },
    {
        "id": "004_trading_calendars",
        "description": "Create trading_calendars table",
        "sql": "CREATE TABLE IF NOT EXISTS trading_calendars ..."
    },
    {
        "id": "005_trading_holidays",
        "description": "Create trading_holidays table",
        "sql": "CREATE TABLE IF NOT EXISTS trading_holidays ..."
    },
    {
        "id": "006_minute_ohlcv_instrument_id",
        "description": "Add nullable instrument_id to minute_ohlcv",
        "sql": "ALTER TABLE minute_ohlcv ADD COLUMN IF NOT EXISTS instrument_id BIGINT"
    },
    {
        "id": "007_seed_nyse_calendar",
        "description": "Seed NYSE trading calendar and holidays 2020-2026",
        "sql": "..."  # Generated from seed_calendar.py
    },
    {
        "id": "008_seed_nasdaq_calendar",
        "description": "Seed NASDAQ trading calendar (shares NYSE holidays)",
        "sql": "..."
    },
    {
        "id": "009_instruments_calendar_fk",
        "description": "Add FK from instruments.trading_calendar_id to trading_calendars",
        "sql": """ALTER TABLE instruments
            ADD CONSTRAINT fk_instruments_calendar
            FOREIGN KEY (trading_calendar_id)
            REFERENCES trading_calendars(calendar_id)"""
    },
]
```

### Calendar Seed Data

The `seed_calendar.py` module provides functions that generate the INSERT SQL for calendar data. This keeps the holiday list maintainable and testable as Python data structures rather than raw SQL strings.

**NYSE seed entry:**
```python
NYSE_CALENDAR = {
    "calendar_id": "NYSE",
    "exchange_name": "New York Stock Exchange",
    "timezone": "America/New_York",
    "market_open": "09:30",
    "market_close": "16:00",
    "extended_open": "04:00",
    "extended_close": "20:00",
    "has_extended_hours": True,
}
```

**NASDAQ seed entry:**
```python
NASDAQ_CALENDAR = {
    "calendar_id": "NASDAQ",
    "exchange_name": "NASDAQ Stock Market",
    "timezone": "America/New_York",
    "market_open": "09:30",
    "market_close": "16:00",
    "extended_open": "04:00",
    "extended_close": "20:00",
    "has_extended_hours": True,
}
```

**Holiday generation:** A function generates the holiday list for a given year range, handling:
- Fixed-date holidays (New Year's, Juneteenth, Independence Day, Christmas) with weekend adjustment rules (observed on Friday if Saturday, Monday if Sunday)
- Relative holidays (MLK Day = 3rd Monday of January, Presidents' Day = 3rd Monday of February, etc.)
- Good Friday (calculated from Easter)
- Early close days (day before Independence Day at 13:00, Black Friday at 13:00, Christmas Eve at 13:00 when it falls on a weekday)

### Migration Runner

Added to `TimescaleMinuteDataDB`:

```python
def apply_schema_migrations(self) -> list[str]:
    """Apply pending schema migrations. Returns list of applied migration IDs."""
```

Logic:
1. Create `schema_migrations` table if not exists (migration 001 is self-bootstrapping)
2. Read applied migration IDs from `schema_migrations`
3. For each migration in `MIGRATIONS` not yet applied:
   a. Execute the SQL within a transaction
   b. Insert a record into `schema_migrations`
   c. Commit
4. Return list of newly applied migration IDs

Each migration runs in its own transaction so a failure mid-sequence leaves previously applied migrations committed.

### CLI Entry Point

A minimal CLI command to run migrations:

```
mt data migrate
```

This reuses the existing `data_app` infrastructure. The command:
1. Creates a `TimescaleMinuteDataDB` from `MT_TIMESCALE_DB_URL`
2. Calls `db.apply_schema_migrations()`
3. Prints applied migrations (or "Schema is up to date")

This is intentionally simple — it's the only new CLI addition and exists to make migration application scriptable.

## Integration Points

### Provides to Other Slices

| Consumer | What it gets |
|---|---|
| Slice 103 (Instrument Registry) | `instruments` table, `provider_symbol_mapping` table |
| Slice 104 (Trading Calendar) | `trading_calendars` table with NYSE/NASDAQ entries, `trading_holidays` table with 2020-2026 holidays |
| Slice 105 (Tick Hypertable) | `instruments` table for `instrument_id` FK reference |
| Future: instrument_id backfill | Nullable `instrument_id` column on `minute_ohlcv` |

### Consumes from Other Slices

| Dependency | What it provides |
|---|---|
| Slice 100 (psycopg3 migration) | `TimescaleMinuteDataDB` with psycopg3 pool, `Settings` with `timescale_db_url` |

## Success Criteria

### Functional Requirements
- All six tables exist on the TimescaleDB host after migration: `schema_migrations`, `instruments`, `provider_symbol_mapping`, `trading_calendars`, `trading_holidays`, plus `instrument_id` column on `minute_ohlcv`
- `trading_calendars` contains entries for NYSE and NASDAQ with correct market hours
- `trading_holidays` contains all standard US equity market holidays from 2020 through 2026 (full closures and early closes)
- `instruments` and `provider_symbol_mapping` tables exist but are empty (populated by slice 103)
- `minute_ohlcv.instrument_id` is nullable BIGINT with no data (backfilled later)
- `mt data migrate` applies all pending migrations and reports results
- Running `mt data migrate` a second time reports "Schema is up to date" (idempotent)
- Existing `minute_ohlcv` data and continuous aggregates are unaffected

### Technical Requirements
- Migration runner has unit tests with mocked DB connection
- Calendar seed data has unit tests verifying holiday dates for known years (e.g., 2024 Thanksgiving = Nov 28, 2024 Good Friday = Mar 29)
- Integration tests verify table creation and seed data against real DB (skip when unavailable)
- All migrations use `IF NOT EXISTS` / `IF NOT EXISTS` patterns for idempotency
- No changes to existing `TimescaleMinuteDataDB` read/write methods or continuous aggregates

### Verification Walkthrough

```bash
# 1. Run migrations
mt data migrate
# Expected: Lists applied migrations (001 through 009), or "Schema is up to date" if already applied

# 2. Verify tables exist (via psql or equivalent)
psql $MT_TIMESCALE_DB_URL -c "\dt"
# Expected: instruments, provider_symbol_mapping, trading_calendars, trading_holidays,
#   schema_migrations visible alongside existing minute_ohlcv

# 3. Verify calendar seed data
psql $MT_TIMESCALE_DB_URL -c "SELECT * FROM trading_calendars"
# Expected: 2 rows (NYSE, NASDAQ) with market_open=09:30, market_close=16:00, timezone=America/New_York

psql $MT_TIMESCALE_DB_URL -c "SELECT COUNT(*) FROM trading_holidays WHERE calendar_id = 'NYSE'"
# Expected: ~80-90 rows (roughly 10-13 holidays/early-closes per year x 7 years)

psql $MT_TIMESCALE_DB_URL -c "SELECT * FROM trading_holidays WHERE holiday_date = '2024-11-28'"
# Expected: Thanksgiving Day, market_status='closed'

psql $MT_TIMESCALE_DB_URL -c "SELECT * FROM trading_holidays WHERE holiday_date = '2024-11-29'"
# Expected: Day After Thanksgiving, market_status='early_close', early_close_time=13:00

# 4. Verify instrument_id column on minute_ohlcv
psql $MT_TIMESCALE_DB_URL -c "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = 'minute_ohlcv' AND column_name = 'instrument_id'"
# Expected: instrument_id, bigint, YES

# 5. Verify existing data unaffected
mt data minute coverage
# Expected: Same output as before migrations — fleet summary with existing symbols and row counts

# 6. Idempotency check
mt data migrate
# Expected: "Schema is up to date" (no migrations applied)

# 7. Tests pass
uv run python -m pytest test/unit test/integration --tb=short
# Expected: All existing tests pass + new schema tests pass
```

## Implementation Notes

### Development Approach

Suggested order:
1. Create `schema/migrations.py` with migration definitions and `schema/__init__.py`
2. Create `schema/seed_calendar.py` with holiday generation logic — unit test the holiday dates
3. Add `apply_schema_migrations()` to `TimescaleMinuteDataDB` — unit test with mocked connection
4. Add `mt data migrate` CLI command
5. Integration tests against real DB
6. Run verification walkthrough

### Special Considerations

- **Holiday date accuracy is critical.** The seed data drives gap detection quality in slice 104. Incorrect holiday dates → false positive gaps. Unit tests should verify specific known dates (Good Friday especially, since it moves every year).
- **Easter/Good Friday calculation** can use a well-known algorithm (Anonymous Gregorian algorithm / Butcher's algorithm) implemented in Python, or use the `dateutil` library if already a dependency. Check existing dependencies before adding new ones.
- **The `schema_migrations` table bootstrap** is a chicken-and-egg: migration 001 creates the table that tracks migrations. Handle by executing the CREATE TABLE outside the normal check-then-apply flow, or by checking for table existence first with `information_schema`.
