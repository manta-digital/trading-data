---
docType: slice-design
slice: tick-event-hypertable-schema
project: trading
parent: user/architecture/100-slices.data-storage.md
dependencies: [100]
interfaces: []
dateCreated: 20260404
dateUpdated: 20260404
status: complete
dateUpdated: 20260404
---

# Slice Design: Tick Event Hypertable Schema

## Overview

Create the `tick_events` hypertable on a separate TimescaleDB instance for storing trade and quote tick data. This is a schema-only slice: SQL migration scripts, a `tick_db_url` addition to `Settings`, and validation tooling. No application code, no ingestion logic, no data services — those belong to Initiative 120.

The tick hypertable stores trade and quote events in a single table using an `event_type` discriminator column. The natural key `(instrument_id, timestamp, sequence_number, source)` supports idempotent ingestion via `ON CONFLICT`. Space partitioning by `instrument_id` enables efficient per-symbol queries. 1-hour chunk intervals match the expected query patterns for tick-level analysis.

## Value

**Architectural:** Completes the storage schema layer for Initiative 100 (Data Storage). With this slice, all three data tiers — daily OHLCV (PostgreSQL), minute OHLCV (TimescaleDB), and tick events (TimescaleDB) — have defined schemas. Initiative 120 (Data Acquisition) can begin tick ingestion work immediately.

**Developer-facing:** Provides a validated, production-ready hypertable schema that ingestion code can write to without schema design decisions. The migration scripts are idempotent and include rollback, so the tick DB instance can be set up and torn down cleanly.

**Operator-facing:** The `tick_db_url` setting follows the established `Settings` pattern, so tick DB connectivity is configured the same way as minute and daily DB connections (`MT_TICK_DB_URL` environment variable).

## Technical Scope

### In Scope
- SQL migration script creating the `tick_events` hypertable with trade/quote event support
- Rollback script to cleanly remove the hypertable
- Validation script to confirm the migration was applied correctly
- `TickEventType` StrEnum in a new module (`tick_schema.py`) defining event type constants
- `tick_db_url` field added to `Settings` class (env var: `MT_TICK_DB_URL`)
- TimescaleDB compression policy (segment by `instrument_id`, order by `timestamp, sequence_number`)
- Space partitioning by `instrument_id`
- Update to `database/migrations/README.md` documenting the new migration
- Unit tests for `TickEventType` enum and `Settings.tick_db_url`
- Integration test (skip when DB unavailable) that applies migration and validates schema

### Out of Scope
- Application-level data access classes (no `TickDataDB` or similar — Initiative 120)
- Tick ingestion pipeline or DataBento provider integration (Initiative 120)
- Tick data aggregation or continuous aggregates (future work, needs real data first)
- Retention policies (need usage patterns from Initiative 120 before deciding)
- BBO, NBBO, depth, or status event types (architecture notes: add when needed, trade/quote covers initial use cases)
- CLI commands for tick data (no data to query yet)
- Populating the `instruments` table with futures instruments (no seed data for futures exists yet)

## Dependencies

### Prerequisites
- **Slice 100 (complete):** psycopg3 connection patterns established. `Settings` class with `model_config` and `MT_` prefix convention.
- **Slice 102 (complete):** `instruments` table exists (provides `instrument_id` referenced by `tick_events`).
- **Separate TimescaleDB instance:** The tick hypertable targets a different database host than the minute data instance (`<db-host>`). The host may not exist yet — the migration is designed to be applied whenever the instance is provisioned.

### Interfaces Required
- `instruments` table schema (migration 750): `instrument_id INTEGER` — the `tick_events` table references this via a logical foreign key (not enforced across database instances, since tick DB and minute DB are on separate hosts).
- `Settings` class from `src/manta_trading/config/__init__.py`

## Architecture

### Component Structure

```
database/migrations/
  ├── 760_create_tick_events_hypertable.sql    (new — forward migration)
  ├── 760_rollback_tick_events_hypertable.sql  (new — rollback)
  ├── 760_validate_migration.sql               (new — validation queries)
  └── README.md                                (update — document migration 760)

src/manta_trading/data/base/tick_schema.py     (new — TickEventType StrEnum)
src/manta_trading/config/__init__.py           (update — add tick_db_url)

test/unit/data/base/test_tick_schema.py        (new — enum tests)
test/unit/test_settings.py                     (update — tick_db_url test)
test/integration/test_tick_schema_integration.py (new — migration validation)
```

### Data Flow

This slice has no runtime data flow — it creates schema infrastructure only. The intended data flow for Initiative 120 is:

1. **Ingestion** (future): DataBento tick data → normalize → `INSERT INTO tick_events ... ON CONFLICT DO UPDATE`
2. **Query** (future): Application code queries `tick_events` by `instrument_id` and time range
3. **Compression** (automatic): TimescaleDB compresses chunks older than the configured threshold

### Schema Design

The `tick_events` table uses a single-table design with an `event_type` discriminator:

```
tick_events
├── instrument_id INTEGER NOT NULL        — logical FK to instruments.instrument_id
├── timestamp TIMESTAMPTZ NOT NULL        — event timestamp (exchange time)
├── sequence_number BIGINT NOT NULL       — provider sequence within symbol+timestamp
├── source VARCHAR(50) NOT NULL           — data provider ('databento', etc.)
├── event_type VARCHAR(10) NOT NULL       — 'trade' or 'quote'
├── price NUMERIC(18,8)                   — trade price (NULL for quotes)
├── size NUMERIC(18,4)                    — trade size (NULL for quotes)
├── exchange VARCHAR(10)                  — execution/quoting exchange
├── conditions VARCHAR(100)               — trade conditions string (NULL for quotes)
├── bid_price NUMERIC(18,8)              — quote bid (NULL for trades)
├── bid_size NUMERIC(18,4)               — quote bid size (NULL for trades)
├── ask_price NUMERIC(18,8)              — quote ask (NULL for trades)
├── ask_size NUMERIC(18,4)               — quote ask size (NULL for trades)
├── ingestion_timestamp TIMESTAMPTZ       — when this row was written
└── metadata JSONB                        — extensible per-event metadata
```

**Natural key:** `(instrument_id, timestamp, sequence_number, source)` — composite unique constraint supporting `ON CONFLICT DO UPDATE` for idempotent ingestion.

## Technical Decisions

### Single Table with Event Type Discriminator

Trade and quote events share the same table rather than using separate `tick_trades` and `tick_quotes` tables. Rationale:

- **Unified timeline queries:** "Show me all events for ES between 09:30:00 and 09:30:05" requires a single query, not a UNION
- **Simpler ingestion:** One INSERT path, one ON CONFLICT handler
- **Natural ordering:** Events arrive interleaved; storing them interleaved preserves the market's event sequence
- **Storage efficiency:** NULL columns for trade-only or quote-only fields compress well in TimescaleDB (columnar compression)
- **Architecture note:** If future event types (BBO, depth, status) create excessive NULLs, a separate table can be introduced. For trade/quote only, a single table is efficient.

### Numeric Precision

- **Price:** `NUMERIC(18,8)` — supports prices from sub-penny (fractional tick sizes in futures) to large values. 8 decimal places covers all current exchange requirements.
- **Size:** `NUMERIC(18,4)` — supports fractional lot sizes (e.g., mini/micro contracts) while handling large block trades.

These match the precision requirements from the archived architecture doc and are consistent with DataBento's tick data format.

### 1-Hour Chunk Interval

The architecture specifies 1-hour chunks for tick data (vs 4-hour for minute data). Rationale:
- Tick volume is ~28x minute volume at modest scale — smaller chunks keep individual chunks manageable
- Per-symbol queries typically span minutes to hours, not days — 1-hour chunks align with access patterns
- Compression operates per chunk — smaller chunks mean more recent data can be compressed sooner

### Space Partitioning by instrument_id

Space partitioning creates sub-chunks per `instrument_id` within each time chunk. This:
- Improves query performance for single-symbol lookups (most common access pattern)
- Enables per-symbol compression ratios
- Aligns with TimescaleDB's recommended practice for multi-tenant time-series data

The number of space partitions is set to match the expected symbol count range. Starting with 4 partitions (matching the initial 4-32 symbol target). This can be adjusted later without data migration.

### Logical Foreign Key to instruments

The `instrument_id` column references `instruments.instrument_id` conceptually but **not** via a PostgreSQL `FOREIGN KEY` constraint, because:
- `tick_events` lives on a separate database instance from `instruments`
- Cross-database foreign keys are not supported in PostgreSQL
- Application-level validation at ingestion time (Initiative 120) ensures referential integrity

A `CHECK (instrument_id > 0)` constraint provides basic sanity checking at the database level.

### Compression Policy

```sql
ALTER TABLE tick_events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_id',
    timescaledb.compress_orderby = 'timestamp, sequence_number'
);

SELECT add_compression_policy('tick_events', INTERVAL '7 days');
```

- **Segment by `instrument_id`:** Each compressed segment contains data for one instrument, enabling efficient per-symbol decompression
- **Order by `timestamp, sequence_number`:** Preserves event sequence within each segment for efficient range scans
- **7-day delay:** Keeps the most recent week uncompressed for potential late-arriving data and corrections. This is conservative; can be reduced once ingestion patterns are established.

### ON CONFLICT Strategy

The natural key enables idempotent writes:

```sql
INSERT INTO tick_events (instrument_id, timestamp, sequence_number, source, ...)
VALUES (...)
ON CONFLICT (instrument_id, timestamp, sequence_number, source)
DO UPDATE SET
    event_type = EXCLUDED.event_type,
    price = EXCLUDED.price,
    size = EXCLUDED.size,
    ...
    ingestion_timestamp = NOW();
```

This supports:
- Safe re-ingestion of historical bulk data
- Late-arriving corrections that overwrite earlier values
- Duplicate detection without application-level dedup logic

### TickEventType StrEnum

```python
class TickEventType(StrEnum):
    TRADE = "trade"
    QUOTE = "quote"
```

Values match the database `event_type` column exactly. Placed in `src/manta_trading/data/base/tick_schema.py` alongside the enum — a minimal module that future Initiative 120 code will import. No dataclasses or ORM models in this slice.

### Migration Numbering: 760

Follows the established pattern: migration 750 created foundation tables (slice 102), migration 760 creates the tick hypertable (slice 105). The gap leaves room for any intermediate migrations if needed.

## Implementation Details

### Database / Storage Schema

**Forward migration (`760_create_tick_events_hypertable.sql`):**

```sql
-- Migration 760: Create Tick Events Hypertable
-- Date: 2026-04-04
-- Description: Creates tick_events hypertable for trade/quote tick data
-- Dependencies: Requires TimescaleDB extension on separate tick database instance
-- Related: Slice 105 - Tick Event Hypertable Schema

-- Require TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Create tick_events table
CREATE TABLE IF NOT EXISTS tick_events (
    instrument_id INTEGER NOT NULL CHECK (instrument_id > 0),
    timestamp TIMESTAMPTZ NOT NULL,
    sequence_number BIGINT NOT NULL,
    source VARCHAR(50) NOT NULL,
    event_type VARCHAR(10) NOT NULL CHECK (event_type IN ('trade', 'quote')),
    price NUMERIC(18,8),
    size NUMERIC(18,4),
    exchange VARCHAR(10),
    conditions VARCHAR(100),
    bid_price NUMERIC(18,8),
    bid_size NUMERIC(18,4),
    ask_price NUMERIC(18,8),
    ask_size NUMERIC(18,4),
    ingestion_timestamp TIMESTAMPTZ DEFAULT NOW(),
    metadata JSONB
);

-- Convert to hypertable with 1-hour chunks
SELECT create_hypertable(
    'tick_events',
    'timestamp',
    chunk_time_interval => INTERVAL '1 hour',
    if_not_exists => TRUE
);

-- Add space partitioning by instrument_id
SELECT add_dimension(
    'tick_events',
    'instrument_id',
    number_partitions => 4,
    if_not_exists => TRUE
);

-- Natural key constraint for idempotent ingestion
CREATE UNIQUE INDEX IF NOT EXISTS idx_tick_events_natural_key
    ON tick_events (instrument_id, timestamp, sequence_number, source);

-- Query indexes
CREATE INDEX IF NOT EXISTS idx_tick_events_instrument_time
    ON tick_events (instrument_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_tick_events_type
    ON tick_events (event_type, instrument_id, timestamp DESC);

-- Compression policy
ALTER TABLE tick_events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_id',
    timescaledb.compress_orderby = 'timestamp, sequence_number'
);

SELECT add_compression_policy('tick_events', INTERVAL '7 days', if_not_exists => TRUE);
```

**Rollback migration (`760_rollback_tick_events_hypertable.sql`):**

```sql
-- Rollback Migration 760: Remove Tick Events Hypertable
-- Remove compression policy first, then drop table

SELECT remove_compression_policy('tick_events', if_exists => TRUE);
DROP TABLE IF EXISTS tick_events CASCADE;
```

**Validation migration (`760_validate_migration.sql`):**

```sql
-- Validate Migration 760: Tick Events Hypertable
-- Run after forward migration to confirm schema is correct

-- 1. Table exists and is a hypertable
SELECT h.hypertable_name
FROM timescaledb_information.hypertables h
WHERE h.hypertable_name = 'tick_events';

-- 2. Chunk interval is 1 hour
SELECT h.hypertable_name
     , d.column_name
     , d.time_interval
FROM timescaledb_information.dimensions d
JOIN timescaledb_information.hypertables h
  ON d.hypertable_name = h.hypertable_name
WHERE h.hypertable_name = 'tick_events'
  AND d.column_name = 'timestamp';

-- 3. Space dimension exists on instrument_id
SELECT d.column_name, d.num_partitions
FROM timescaledb_information.dimensions d
JOIN timescaledb_information.hypertables h
  ON d.hypertable_name = h.hypertable_name
WHERE h.hypertable_name = 'tick_events'
  AND d.column_name = 'instrument_id';

-- 4. Compression is enabled
SELECT hypertable_name, compression_enabled
FROM timescaledb_information.hypertables
WHERE hypertable_name = 'tick_events';

-- 5. Natural key index exists
SELECT indexname FROM pg_indexes
WHERE tablename = 'tick_events'
  AND indexname = 'idx_tick_events_natural_key';

-- 6. Check constraint on event_type
SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'tick_events'::regclass
  AND contype = 'c';
```

### Module Changes

**`src/manta_trading/config/__init__.py`:**

Add one field to `Settings`:

```python
    # Database
    market_db_url: str | None = None
    timescale_db_url: str | None = None
    tick_db_url: str | None = None
```

**`src/manta_trading/data/base/tick_schema.py` (new):**

Minimal module containing only `TickEventType`:

```python
"""Tick event schema constants.

Defines the event type enum for the tick_events hypertable.
Application-level tick data classes belong to Initiative 120.
"""

from __future__ import annotations

from enum import StrEnum


class TickEventType(StrEnum):
    """Event type discriminator for the tick_events table.

    Values match the CHECK constraint on tick_events.event_type exactly.
    """

    TRADE = "trade"
    QUOTE = "quote"
```

## Integration Points

### Provides to Other Slices
- **Initiative 120 (Data Acquisition):** `tick_events` hypertable ready for write operations. `TickEventType` enum for event type constants. `Settings.tick_db_url` for connection configuration.
- **Initiative 140 (Data Quality):** Schema supports quality queries (gap detection via sequence_number gaps, completeness via time range coverage).

### Consumes from Other Slices
- **Slice 100:** `Settings` class pattern (pydantic-settings with `MT_` prefix)
- **Slice 102:** `instruments` table schema (logical FK reference for `instrument_id`)

## Success Criteria

### Functional Requirements
- Migration 760 applies cleanly on a fresh TimescaleDB instance with `CREATE EXTENSION IF NOT EXISTS timescaledb`
- `tick_events` is a hypertable with 1-hour chunk interval
- Space partitioning on `instrument_id` with 4 partitions
- Natural key unique index on `(instrument_id, timestamp, sequence_number, source)`
- `event_type` CHECK constraint permits only `'trade'` and `'quote'`
- Compression policy configured (segment by `instrument_id`, order by `timestamp, sequence_number`, 7-day delay)
- Rollback script cleanly removes everything
- Validation script confirms all schema elements
- `Settings.tick_db_url` loads from `MT_TICK_DB_URL` environment variable

### Technical Requirements
- All SQL uses `IF NOT EXISTS` / `if_not_exists` for idempotency
- `TickEventType` values match database CHECK constraint values exactly
- No application-level data access code (schema and constants only)
- Unit tests for `TickEventType` enum
- Unit test confirming `Settings.tick_db_url` defaults to `None` and loads from env
- Integration test applying migration and running validation queries (skip when DB unavailable)

### Verification Walkthrough

**1. Apply migration (requires TimescaleDB instance):**
```bash
psql -h <tick-host> -U <user> -d <database> -f database/migrations/760_create_tick_events_hypertable.sql
```
Expected: All statements succeed. Table and indexes created.

**2. Validate migration:**
```bash
psql -h <tick-host> -U <user> -d <database> -f database/migrations/760_validate_migration.sql
```
Expected: All 6 queries return expected results confirming hypertable, dimensions, compression, indexes, and constraints.

**3. Test idempotent insert:**
```sql
INSERT INTO tick_events (instrument_id, timestamp, sequence_number, source, event_type, price, size, exchange)
VALUES (1, '2025-01-15 09:30:00.123-05', 1, 'databento', 'trade', 5250.75, 2.0, 'CME');

-- Re-insert same natural key with updated price
INSERT INTO tick_events (instrument_id, timestamp, sequence_number, source, event_type, price, size, exchange)
VALUES (1, '2025-01-15 09:30:00.123-05', 1, 'databento', 'trade', 5251.00, 2.0, 'CME')
ON CONFLICT (instrument_id, timestamp, sequence_number, source)
DO UPDATE SET price = EXCLUDED.price, size = EXCLUDED.size, ingestion_timestamp = NOW();

SELECT price FROM tick_events WHERE instrument_id = 1 AND sequence_number = 1;
-- Expected: 5251.00 (updated value)
```

**4. Test rollback:**
```bash
psql -h <tick-host> -U <user> -d <database> -f database/migrations/760_rollback_tick_events_hypertable.sql
```
Expected: Table dropped cleanly.

**5. Unit tests:**
```bash
uv run pytest test/unit/data/base/test_tick_schema.py -v
uv run pytest test/unit/test_settings.py -v -k tick
```
Actual result (2026-04-04): `10 passed` (tick_schema) and `2 passed` (settings -k tick). Full suite: `630 passed, 7 skipped` — no regressions.

**6. Integration tests (requires TimescaleDB):**
```bash
MT_TICK_DB_URL=postgresql://... uv run pytest test/integration/test_tick_schema_integration.py -v
```
Actual result (2026-04-04, no tick DB provisioned): `16 skipped` — all tests skip cleanly when `MT_TICK_DB_URL` is not set.

**Caveat:** Steps 1-4 and 6 require a running TimescaleDB instance. The tick DB instance was not provisioned at implementation time — the migration scripts are designed to be applied whenever it becomes available. Integration tests have been verified to skip cleanly.

## Risk Assessment

### Technical Risks
- **Tick DB instance may not exist yet.** The architecture specifies a separate host from the minute data instance. If the host isn't provisioned, migration scripts cannot be validated against a real database.

### Mitigation Strategies
- Migration scripts are idempotent (`IF NOT EXISTS`) and can be applied to any TimescaleDB instance for testing, including the existing minute data host (`<db-host>`) in a separate database.
- Integration tests skip cleanly when `MT_TICK_DB_URL` is not set.
- The validation script can be run independently to confirm correct setup.

## Implementation Notes

### Development Approach

Suggested implementation order:
1. Add `tick_db_url` to `Settings` + unit test
2. Create `TickEventType` StrEnum in `tick_schema.py` + unit tests
3. Write forward migration SQL (`760_create_tick_events_hypertable.sql`)
4. Write rollback SQL (`760_rollback_tick_events_hypertable.sql`)
5. Write validation SQL (`760_validate_migration.sql`)
6. Update `database/migrations/README.md`
7. Integration test (apply → validate → rollback cycle)
8. Verify full test suite passes with no regressions

### Special Considerations

- **Separate database instance:** The tick DB is intentionally on a different host. The migration scripts do not reference the `instruments` table via FK — they use a CHECK constraint instead. Application-level referential integrity is enforced at ingestion time (Initiative 120).
- **No retention policy yet:** Retention policies depend on data volume and access patterns that won't be known until Initiative 120 produces real tick data. The compression policy is included because it's purely beneficial; retention is deferred.
- **Space partition count:** Starting at 4 partitions. TimescaleDB documentation recommends matching the number of disks or the expected number of distinct partition key values, whichever is smaller. 4 is conservative for the initial 4-32 symbol range and can be increased later.
