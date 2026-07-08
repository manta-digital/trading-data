# Foundation Database Schema

**Migration**: 750
**Date**: 2025-01-22
**Purpose**: Core infrastructure for Historical Minute Data Service

This schema provides the foundational tables for instrument metadata, trading calendars, and data quality tracking.

## Table of Contents
- [Overview](#overview)
- [Entity Relationship Diagram](#entity-relationship-diagram)
- [Tables](#tables)
- [Indexes](#indexes)
- [Foreign Keys](#foreign-keys)

---

## Overview

The foundation schema consists of 5 core tables:

1. **instruments** - Central registry of all tradeable instruments
2. **provider_symbol_mapping** - Maps provider-specific symbols to canonical instruments
3. **trading_calendars** - Exchange-specific trading schedules
4. **trading_holidays** - Market closures and special trading days
5. **trading_sessions** - Pre-computed session boundaries (RTH/ETH)

Additionally, the schema extends the existing **minute_ohlcv** table with metadata columns.

---

## Entity Relationship Diagram

```
┌─────────────────────┐
│  trading_calendars  │
│  ─────────────────  │
│  calendar_id (PK)   │
│  calendar_name      │
│  timezone           │
│  market_open_time   │
│  market_close_time  │
│  has_extended_hours │
│  extended_open_time │
│  extended_close_time│
└──────────┬──────────┘
           │
           │ 1
           │
           │ *
┌──────────┴──────────┐         ┌─────────────────────┐
│   instruments       │         │  trading_holidays   │
│  ─────────────────  │         │  ─────────────────  │
│  instrument_id (PK) │         │  holiday_id (PK)    │
│  canonical_id (UK)  │         │  calendar_id (FK)   │
│  symbol             │         │  holiday_date       │
│  asset_class        │         │  holiday_name       │
│  venue              │         │  market_status      │
│  currency           │         │  early_close_time   │
│  tick_size          │         │  late_open_time     │
│  lot_size           │         └─────────────────────┘
│  trading_calendar_id│
│  adjustment_policy  │         ┌─────────────────────┐
│  active             │         │  trading_sessions   │
│  metadata           │         │  ─────────────────  │
└──────────┬──────────┘         │  session_id (PK)    │
           │                    │  calendar_id (FK)   │
           │ 1                  │  session_date       │
           │                    │  session_type       │
           │ *                  │  session_start      │
┌──────────┴──────────┐         │  session_end        │
│provider_symbol_     │         │  is_active          │
│      mapping        │         └─────────────────────┘
│  ─────────────────  │
│  mapping_id (PK)    │
│  instrument_id (FK) │
│  provider           │
│  provider_symbol    │
│  valid_from         │
│  valid_to           │
│  metadata           │
└─────────────────────┘

           ┌─────────────────────┐
           │   minute_ohlcv      │
           │  ─────────────────  │
           │  (existing table)   │
           │  + adjustment_policy│
           │  + session_type     │
           │  + provider_version │
           │  + data_version     │
           │  + ingestion_timestamp│
           └─────────────────────┘
```

---

## Tables

### instruments

Central registry of all tradeable instruments with metadata.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| instrument_id | SERIAL | PRIMARY KEY | Auto-incrementing unique ID |
| canonical_id | VARCHAR(50) | UNIQUE NOT NULL | Canonical identifier (e.g., "AAPL.NASDAQ") |
| symbol | VARCHAR(20) | NOT NULL | Primary trading symbol |
| asset_class | VARCHAR(20) | NOT NULL | Asset type: 'stock', 'future', 'crypto', 'option' |
| venue | VARCHAR(50) | NOT NULL | Trading venue: 'NASDAQ', 'NYSE', 'CME', etc. |
| currency | VARCHAR(3) | DEFAULT 'USD' | Trading currency |
| tick_size | DECIMAL(10,8) | | Minimum price increment |
| lot_size | INTEGER | DEFAULT 1 | Contract/share multiplier |
| trading_calendar_id | VARCHAR(50) | FK → trading_calendars | Associated trading calendar |
| adjustment_policy | VARCHAR(20) | DEFAULT 'split_adjusted' | Default adjustment policy |
| corporate_action_policy | JSONB | | Split handling rules (JSON) |
| futures_roll_convention | VARCHAR(50) | | For futures contracts only |
| active | BOOLEAN | DEFAULT true | Whether instrument is currently active |
| metadata | JSONB | | Additional properties (JSON) |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |
| updated_at | TIMESTAMPTZ | DEFAULT NOW() | Last update timestamp |

**Purpose**: Provides a single source of truth for instrument metadata across the system.

**Key Design Decisions**:
- `canonical_id` format: `{SYMBOL}.{VENUE}` for uniqueness
- JSONB fields allow flexible metadata without schema changes
- `active` flag enables soft deletion

---

### provider_symbol_mapping

Maps provider-specific symbols to canonical instruments with validity periods.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| mapping_id | SERIAL | PRIMARY KEY | Auto-incrementing unique ID |
| instrument_id | INTEGER | FK → instruments | Reference to canonical instrument |
| provider | VARCHAR(50) | NOT NULL | Provider name: 'alphavantage', 'databento', etc. |
| provider_symbol | VARCHAR(50) | NOT NULL | Provider-specific symbol |
| valid_from | DATE | NOT NULL | Start date of mapping validity |
| valid_to | DATE | | End date (NULL = current mapping) |
| metadata | JSONB | | Additional mapping metadata |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Purpose**: Handles symbol changes, provider variations, and historical mapping.

**Key Design Decisions**:
- Validity date ranges support historical symbol lookups
- Unique constraint on (provider, provider_symbol, instrument_id) WHERE valid_to IS NULL
- Multiple providers can map to same instrument

**Example**:
```sql
-- AAPL has always been AAPL on AlphaVantage
provider='alphavantage', provider_symbol='AAPL', valid_from='2020-01-01', valid_to=NULL

-- But FB changed to META
provider='alphavantage', provider_symbol='FB', valid_from='2020-01-01', valid_to='2022-06-09'
provider='alphavantage', provider_symbol='META', valid_from='2022-06-09', valid_to=NULL
```

---

### trading_calendars

Exchange-specific trading schedules and hours.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| calendar_id | VARCHAR(50) | PRIMARY KEY | Unique calendar ID: 'NYSE', 'NASDAQ', 'CME' |
| calendar_name | VARCHAR(100) | NOT NULL | Full calendar name |
| timezone | VARCHAR(50) | NOT NULL | IANA timezone: 'America/New_York' |
| market_open_time | TIME | NOT NULL | Regular hours open time |
| market_close_time | TIME | NOT NULL | Regular hours close time |
| has_extended_hours | BOOLEAN | DEFAULT false | Whether ETH trading exists |
| extended_open_time | TIME | | Extended hours open (if applicable) |
| extended_close_time | TIME | | Extended hours close (if applicable) |
| metadata | JSONB | | Additional calendar properties |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Purpose**: Defines regular and extended trading hours for each exchange.

**Supported Calendars**:
- **NYSE**: 9:30 AM - 4:00 PM ET, Extended: 4:00 AM - 8:00 PM ET
- **NASDAQ**: 9:30 AM - 4:00 PM ET, Extended: 4:00 AM - 8:00 PM ET
- **CME**: Near 24-hour for futures

---

### trading_holidays

Market closures, early closes, and late opens.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| holiday_id | SERIAL | PRIMARY KEY | Auto-incrementing unique ID |
| calendar_id | VARCHAR(50) | FK → trading_calendars | Associated calendar |
| holiday_date | DATE | NOT NULL | Date of holiday |
| holiday_name | VARCHAR(100) | | Holiday name (e.g., "Thanksgiving") |
| market_status | VARCHAR(20) | NOT NULL | 'closed', 'early_close', 'late_open' |
| early_close_time | TIME | | Close time if early close |
| late_open_time | TIME | | Open time if late open |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Purpose**: Tracks all non-standard trading days.

**Market Status Values**:
- `closed`: Full market closure (e.g., Christmas)
- `early_close`: Early close day (e.g., day after Thanksgiving at 1:00 PM)
- `late_open`: Delayed open (rare)

**Unique Constraint**: (calendar_id, holiday_date) - prevents duplicate holidays

---

### trading_sessions

Pre-computed session boundaries for performance optimization.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| session_id | SERIAL | PRIMARY KEY | Auto-incrementing unique ID |
| calendar_id | VARCHAR(50) | FK → trading_calendars | Associated calendar |
| session_date | DATE | NOT NULL | Trading date |
| session_type | VARCHAR(20) | NOT NULL | 'RTH' or 'ETH' |
| session_start | TIMESTAMPTZ | NOT NULL | Session start (timezone-aware) |
| session_end | TIMESTAMPTZ | NOT NULL | Session end (timezone-aware) |
| is_active | BOOLEAN | DEFAULT true | Whether session is valid |
| created_at | TIMESTAMPTZ | DEFAULT NOW() | Record creation timestamp |

**Purpose**: Pre-compute session boundaries to avoid runtime calculations.

**Note**: This table is optional - sessions can be computed on-the-fly using trading_calendars and trading_holidays.

---

### minute_ohlcv (Extended)

Existing table with added metadata columns for data quality tracking.

**New Columns Added**:

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| adjustment_policy | VARCHAR(20) | 'split_adjusted' | How prices are adjusted |
| session_type | VARCHAR(10) | 'RTH' | 'RTH' or 'ETH' |
| provider_version | VARCHAR(50) | | Provider API version used |
| data_version | VARCHAR(50) | | Internal data version |
| ingestion_timestamp | TIMESTAMPTZ | NOW() | When data was ingested |

**Purpose**: Track data lineage and enable filtering by session type and adjustment policy.

---

## Indexes

All indexes use `IF NOT EXISTS` for idempotent migrations.

### instruments
- `idx_instruments_canonical_id` on (canonical_id) - Primary lookup
- `idx_instruments_symbol` on (symbol) - Symbol search
- `idx_instruments_asset_class` on (asset_class) - Filtering by type
- `idx_instruments_venue` on (venue) - Filtering by exchange

### provider_symbol_mapping
- `idx_provider_symbol_current` UNIQUE on (provider, provider_symbol, instrument_id) WHERE valid_to IS NULL
  - Ensures only one current mapping per provider/symbol
- `idx_provider_symbol_instrument` on (instrument_id) - Reverse lookup

### trading_holidays
- `idx_trading_holidays_calendar` on (calendar_id, holiday_date) - Calendar queries
- `idx_trading_holidays_unique` UNIQUE on (calendar_id, holiday_date) - Prevent duplicates

### trading_sessions
- `idx_trading_sessions_calendar` on (calendar_id, session_date) - Session queries

### minute_ohlcv
- `idx_minute_ohlcv_session` on (symbol, session_type, time) - Session-filtered queries

**Index Strategy**: Composite indexes on common query patterns, unique indexes to enforce constraints.

---

## Foreign Keys

| From Table | Column | References | On Delete |
|------------|--------|------------|-----------|
| instruments | trading_calendar_id | trading_calendars(calendar_id) | Not specified (default RESTRICT) |
| provider_symbol_mapping | instrument_id | instruments(instrument_id) | Not specified |
| trading_holidays | calendar_id | trading_calendars(calendar_id) | Not specified |
| trading_sessions | calendar_id | trading_calendars(calendar_id) | Not specified |

**Design Decision**: No CASCADE deletes - calendar and instrument deletions should be explicit operations.

---

## Migration Files

**Forward Migration**: `database/migrations/750_create_foundation_tables.sql`
**Rollback**: `database/migrations/750_rollback_foundation_tables.sql`
**Seed Data**: `database/seeds/750_foundation_seed_data.sql`
**Validation**: `database/migrations/750_validate_migration.sql`

See [Migration Guide](migration_750_guide.md) for detailed procedures.

---

## Data Quality Notes

1. **Canonical IDs**: Format is strictly `{SYMBOL}.{VENUE}` - enforced at application level
2. **Timezones**: All TIMESTAMPTZ columns are stored in UTC, converted for display
3. **Validity Periods**: `valid_to = NULL` indicates current/active mapping
4. **JSONB Fields**: Flexible metadata without schema migrations, but indexed queries are slower
5. **Adjustment Policy**: Default is 'split_adjusted' - raw data requires explicit marking

---

## Future Enhancements

Potential schema improvements for future iterations:

1. **Instrument Relationships**: Parent/child relationships for options, futures chains
2. **Calendar Exceptions**: More granular special trading days (half days, quarter days)
3. **Split History**: Dedicated table for corporate action tracking
4. **Data Lineage**: Full audit trail of data transformations
5. **Multi-Currency**: Enhanced support for FX conversions and cross-listings

---

**Last Updated**: 2025-01-22
**Schema Version**: 1.0
**Migration**: 750
