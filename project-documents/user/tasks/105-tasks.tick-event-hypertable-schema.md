---
docType: tasks
slice: tick-event-hypertable-schema
project: trading
lld: user/slices/105-slice.tick-event-hypertable-schema.md
dependencies: [100, 102]
projectState: >
  Slices 100-104 complete. psycopg3 migration done, instruments table and trading
  calendars exist on TimescaleDB (<db-host>). Settings class has market_db_url
  and timescale_db_url. No tick-level schema exists yet. Tick DB instance
  (separate host) may not be provisioned.
dateCreated: 20260404
dateUpdated: 20260404
status: complete
---

## Context Summary

- Working on tick-event-hypertable-schema slice (105)
- Creates `tick_events` hypertable on a separate TimescaleDB instance for trade/quote tick data
- Schema-only slice: SQL migrations, `TickEventType` StrEnum, `Settings.tick_db_url` — no application code or ingestion
- Dependencies: slice 100 (Settings patterns), slice 102 (instruments table schema for logical FK)
- Delivers: migration 760 (forward + rollback + validate), `TickEventType` enum, `tick_db_url` setting
- Next planned: Initiative 120 (Data Acquisition) will build ingestion against this schema
- Effort: 2/5

## Tasks

### Task 1: Add `tick_db_url` to Settings

- [x] **Add `tick_db_url` field to `Settings` class**
  - [x] Edit `src/manta_trading/config/__init__.py`
  - [x] Add `tick_db_url: str | None = None` in the Database section, after `timescale_db_url`
  - [x] Comment: `# Tick data database (separate instance)`
  - [x] **Success:** `Settings().tick_db_url` returns `None` by default; `MT_TICK_DB_URL=postgresql://... Settings()` loads the URL

- [x] **Add unit test for `tick_db_url`**
  - [x] Edit `test/unit/test_settings.py`
  - [x] Add test in `TestSettingsDefaults`: verify `tick_db_url` defaults to `None`
  - [x] Add test in `TestSettingsFromEnv` (or equivalent): verify `MT_TICK_DB_URL` env var is read correctly
  - [x] **Success:** `uv run pytest test/unit/test_settings.py -v -k tick` — all new tests pass; existing tests unaffected

**Commit:** `feat: add tick_db_url to Settings for tick database connection`

### Task 2: Create `TickEventType` StrEnum

- [x] **Create `src/manta_trading/data/base/tick_schema.py`**
  - [x] Module docstring: "Tick event schema constants" — note that application-level tick data classes belong to Initiative 120
  - [x] Define `TickEventType(StrEnum)` with members `TRADE = "trade"` and `QUOTE = "quote"`
  - [x] Docstring on the enum: note values match the CHECK constraint on `tick_events.event_type` exactly
  - [x] Imports: `from __future__ import annotations`, `from enum import StrEnum`
  - [x] **Success:** Module imports without error; `TickEventType.TRADE == "trade"` and `TickEventType.QUOTE == "quote"`

- [x] **Add unit tests for `TickEventType`**
  - [x] Create `test/unit/data/base/test_tick_schema.py`
  - [x] Test: enum has exactly 2 members (`TRADE`, `QUOTE`)
  - [x] Test: values match expected strings (`"trade"`, `"quote"`)
  - [x] Test: string comparison works (StrEnum behavior): `TickEventType.TRADE == "trade"` is `True`
  - [x] Test: constructing from string: `TickEventType("trade") is TickEventType.TRADE`
  - [x] **Success:** `uv run pytest test/unit/data/base/test_tick_schema.py -v` — all pass

**Commit:** `feat: add TickEventType StrEnum for tick_events event type constants`

### Task 3: Write Forward Migration SQL

- [x] **Create `database/migrations/760_create_tick_events_hypertable.sql`**
  - [x] Header comment block: migration number, date, description, dependencies (TimescaleDB extension), related slice (105)
  - [x] `CREATE EXTENSION IF NOT EXISTS timescaledb;`
  - [x] `CREATE TABLE IF NOT EXISTS tick_events` with all columns per slice design Schema Design section:
    1. [x] `instrument_id INTEGER NOT NULL CHECK (instrument_id > 0)`
    2. [x] `timestamp TIMESTAMPTZ NOT NULL`
    3. [x] `sequence_number BIGINT NOT NULL`
    4. [x] `source VARCHAR(50) NOT NULL`
    5. [x] `event_type VARCHAR(10) NOT NULL CHECK (event_type IN ('trade', 'quote'))`
    6. [x] `price NUMERIC(18,8)`, `size NUMERIC(18,4)`, `exchange VARCHAR(10)`, `conditions VARCHAR(100)`
    7. [x] `bid_price NUMERIC(18,8)`, `bid_size NUMERIC(18,4)`, `ask_price NUMERIC(18,8)`, `ask_size NUMERIC(18,4)`
    8. [x] `ingestion_timestamp TIMESTAMPTZ DEFAULT NOW()`, `metadata JSONB`
  - [x] Convert to hypertable: `create_hypertable('tick_events', 'timestamp', chunk_time_interval => INTERVAL '1 hour', if_not_exists => TRUE)`
  - [x] Add space dimension: `add_dimension('tick_events', 'instrument_id', number_partitions => 4, if_not_exists => TRUE)`
  - [x] Create natural key unique index: `idx_tick_events_natural_key ON tick_events (instrument_id, timestamp, sequence_number, source)`
  - [x] Create query indexes:
    1. [x] `idx_tick_events_instrument_time ON tick_events (instrument_id, timestamp DESC)`
    2. [x] `idx_tick_events_type ON tick_events (event_type, instrument_id, timestamp DESC)`
  - [x] Configure compression: `compress_segmentby = 'instrument_id'`, `compress_orderby = 'timestamp, sequence_number'`
  - [x] Add compression policy: `add_compression_policy('tick_events', INTERVAL '7 days', if_not_exists => TRUE)`
  - [x] All DDL uses `IF NOT EXISTS` / `if_not_exists` for idempotency
  - [x] **Success:** SQL file parses without syntax errors; all statements use idempotent patterns

### Task 4: Write Rollback Migration SQL

- [x] **Create `database/migrations/760_rollback_tick_events_hypertable.sql`**
  - [x] Header comment: rollback for migration 760
  - [x] `SELECT remove_compression_policy('tick_events', if_exists => TRUE);`
  - [x] `DROP TABLE IF EXISTS tick_events CASCADE;`
  - [x] Order matters: remove compression policy before dropping table
  - [x] **Success:** SQL file parses without syntax errors; drops table cleanly

### Task 5: Write Validation Migration SQL

- [x] **Create `database/migrations/760_validate_migration.sql`**
  - [x] Header comment: validation for migration 760
  - [x] Query 1: Table exists and is a hypertable (query `timescaledb_information.hypertables`)
  - [x] Query 2: Chunk interval is 1 hour (query `timescaledb_information.dimensions` for `timestamp` column)
  - [x] Query 3: Space dimension on `instrument_id` with expected partition count (query `timescaledb_information.dimensions`)
  - [x] Query 4: Compression is enabled (query `timescaledb_information.hypertables` for `compression_enabled`)
  - [x] Query 5: Natural key index exists (`pg_indexes` for `idx_tick_events_natural_key`)
  - [x] Query 6: CHECK constraints exist on `event_type` and `instrument_id` (query `pg_constraint`)
  - [x] **Success:** All 6 queries are well-formed and target the correct schema metadata views

**Commit:** `feat: add migration 760 for tick_events hypertable (forward, rollback, validate)`

### Task 6: Update Migration README

- [x] **Update `database/migrations/README.md`**
  - [x] Add new section `### Migration 760: Tick Events Hypertable` following the format of existing migration entries (750)
  - [x] List files: forward, rollback, validate
  - [x] Description: creates tick_events hypertable for trade/quote tick data on separate TimescaleDB instance
  - [x] Dependencies: requires TimescaleDB extension; separate database instance from minute data
  - [x] Forward/rollback command examples (same pattern as migration 750)
  - [x] Special considerations: separate DB instance, idempotent, no retention policy yet
  - [x] **Success:** README documents migration 760 consistently with existing entries

**Commit:** `docs: document migration 760 in migrations README`

### Task 7: Integration Test — Migration Cycle

- [x] **Create `test/integration/test_tick_schema_integration.py`**
  - [x] Skip marker: skip all tests when `MT_TICK_DB_URL` env var is not set (same pattern as `test_trading_calendar_integration.py`)
  - [x] Use `TICK_URL = os.environ.get("MT_TICK_DB_URL", "")` at module level
  - [x] Fixture: `tick_db` — establishes psycopg connection using `TICK_URL`, yields connection, closes on teardown
  - [x] **Test: forward migration applies cleanly**
    - [x] Read `760_create_tick_events_hypertable.sql` from file
    - [x] Execute via psycopg connection
    - [x] Verify no errors
  - [x] **Test: hypertable exists with correct dimensions**
    - [x] Query `timescaledb_information.hypertables` for `tick_events`
    - [x] Verify 1-hour chunk interval
    - [x] Verify space dimension on `instrument_id` with 4 partitions
  - [x] **Test: compression is enabled**
    - [x] Query `timescaledb_information.hypertables` for `compression_enabled`
    - [x] Assert `True`
  - [x] **Test: indexes exist**
    - [x] Query `pg_indexes` for `idx_tick_events_natural_key`, `idx_tick_events_instrument_time`, `idx_tick_events_type`
    - [x] Assert all 3 exist
  - [x] **Test: CHECK constraints enforce valid values**
    - [x] Insert a trade row — succeeds
    - [x] Insert a quote row — succeeds
    - [x] Insert row with `event_type = 'invalid'` — raises `CheckViolation`
    - [x] Insert row with `instrument_id = 0` — raises `CheckViolation`
    - [x] Insert row with `instrument_id = -1` — raises `CheckViolation`
  - [x] **Test: idempotent insert with ON CONFLICT**
    - [x] Insert a trade row
    - [x] Re-insert same natural key with updated price using `ON CONFLICT DO UPDATE`
    - [x] Verify updated price is returned
  - [x] **Test: rollback migration removes everything**
    - [x] Read `760_rollback_tick_events_hypertable.sql` from file
    - [x] Execute via psycopg connection
    - [x] Verify `tick_events` table no longer exists
  - [x] **Test: forward migration is idempotent**
    - [x] Apply forward migration twice in sequence
    - [x] Verify no errors on second application
  - [x] **Success:** `MT_TICK_DB_URL=postgresql://... uv run pytest test/integration/test_tick_schema_integration.py -v` — all pass; tests skip cleanly when env var is not set

**Commit:** `test: add integration tests for tick_events migration cycle`

### Task 8: Full Test Suite Verification

- [x] **Run complete unit test suite**
  - [x] `uv run pytest test/unit/ -v`
  - [x] **Success:** All tests pass with no regressions. New test count should be previous total + settings test(s) + tick_schema tests (expect ~4-6 new tests)
    - Result: 630 tests pass (7 skipped pre-existing), 12 new tests added (2 settings + 10 tick_schema)

### Task 9: Final Verification and Completion

- [x] **Run through Verification Walkthrough from slice design**
  - [x] Verify unit tests pass: `uv run pytest test/unit/data/base/test_tick_schema.py -v` and `uv run pytest test/unit/test_settings.py -v -k tick`
  - [x] Verify integration tests skip when `MT_TICK_DB_URL` is not set
  - [x] If TimescaleDB instance is available, run integration tests and verify migration cycle
  - [x] Update Verification Walkthrough section of slice design with actual results

- [x] **Update slice status**
  - [x] Set `status: complete` and `dateUpdated: 20260404` in slice design frontmatter
  - [x] Set `status: complete` and `dateUpdated: 20260404` in this task file frontmatter
  - [x] Check off slice 105 entry in `user/architecture/100-slices.data-storage.md` (change `[ ]` to `[x]`)

- [x] **Update CHANGELOG.md**
  - [x] Add slice 105 section under `[Unreleased]`
  - [x] Added: `tick_events` hypertable schema (migration 760), `TickEventType` StrEnum, `Settings.tick_db_url`
  - [x] Note: schema-only slice, no application code

- [x] **Run `workflow_check` with fix parameter**

**Commit:** `docs: mark slice 105 complete, update changelog`
