# Migration 750 Guide

Step-by-step guide for applying and rolling back Migration 750 (Foundation Tables).

## Table of Contents
- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Pre-Migration Checklist](#pre-migration-checklist)
- [Migration Procedure](#migration-procedure)
- [Post-Migration Verification](#post-migration-verification)
- [Rollback Procedure](#rollback-procedure)
- [Troubleshooting](#troubleshooting)

---

## Overview

**Migration Number**: 750
**Purpose**: Create foundation infrastructure for Historical Minute Data Service
**Tables Created**: 5 new tables
**Tables Modified**: 1 (minute_ohlcv - adds 5 columns)
**Estimated Time**: 5-10 minutes
**Downtime Required**: No (additive changes only)

### What This Migration Does

1. Creates `instruments` table - central instrument registry
2. Creates `provider_symbol_mapping` table - provider symbol mappings with validity periods
3. Creates `trading_calendars` table - exchange trading schedules
4. Creates `trading_holidays` table - market closures and special trading days
5. Creates `trading_sessions` table - pre-computed session boundaries
6. Extends `minute_ohlcv` table - adds metadata columns for data quality tracking

### Breaking Changes

**None** - This is an additive migration. Existing tables and data are not modified (except for adding new columns to minute_ohlcv with defaults).

---

## Prerequisites

### Required

- PostgreSQL 12+ with TimescaleDB extension
- Database user with CREATE TABLE, CREATE INDEX, and ALTER TABLE privileges
- Existing `minute_ohlcv` table (created by migration 025)
- psql client or DataGrip

### Recommended

- Full database backup before migration
- Test environment for dry-run
- Maintenance window (though not strictly required)

---

## Pre-Migration Checklist

### 1. Verify Environment

```bash
# Check PostgreSQL version
psql --version

# Verify TimescaleDB extension
psql -h <host> -U <user> -d <database> -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'timescaledb';"
```

### 2. Backup Database

```bash
# Full database backup
pg_dump -h <host> -U <user> -d <database> -F c -f backup_pre_migration_750_$(date +%Y%m%d).dump

# Or just schema backup
pg_dump -h <host> -U <user> -d <database> -s -f backup_schema_pre_migration_750_$(date +%Y%m%d).sql
```

### 3. Verify Current State

```bash
# Check that minute_ohlcv table exists
psql -h <host> -U <user> -d <database> -c "\dt minute_ohlcv"

# Check that new tables don't already exist
psql -h <host> -U <user> -d <database> -c "
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions');"
```

**Expected**: minute_ohlcv exists, new tables do not exist.

### 4. Check Disk Space

```bash
# Check available disk space
df -h /path/to/postgres/data
```

**Recommended**: At least 1 GB free space (new tables will be small initially).

---

## Migration Procedure

### Method 1: Command Line (psql)

#### Step 1: Set Environment Variables

```bash
# In project root
cd /path/to/trading

# Set database connection info
export PGHOST="<db-host>"
export PGUSER="trading_app"
export PGDATABASE="trading_test"
export PGPASSWORD="your_password"
```

#### Step 2: Run Migration

```bash
# Run the migration script
psql -f database/migrations/750_create_foundation_tables.sql

# Check exit code
echo $?  # Should be 0 for success
```

#### Step 3: Load Seed Data

```bash
# Load initial data (calendars, holidays, sample instruments)
psql -f database/seeds/750_foundation_seed_data.sql
```

#### Step 4: Validate Migration

```bash
# Run validation script
psql -f database/migrations/750_validate_migration.sql
```

### Method 2: DataGrip

#### Step 1: Open Migration Script

1. Open DataGrip
2. Connect to your database
3. Open `database/migrations/750_create_foundation_tables.sql`

#### Step 2: Execute Migration

1. Select all (Cmd/Ctrl + A)
2. Execute (Cmd/Ctrl + Enter)
3. Review console output for errors

#### Step 3: Load Seed Data

1. Open `database/seeds/750_foundation_seed_data.sql`
2. Execute the entire script
3. Review console output

#### Step 4: Validate

1. Open `database/migrations/750_validate_migration.sql`
2. Execute and review results
3. Look for "PASS" indicators

### Method 3: Automated Script

Create a wrapper script:

```bash
#!/bin/bash
# migrate_750.sh

set -e  # Exit on error

echo "Starting Migration 750..."

# Load environment
source .env

# Run migration
echo "Running migration..."
PGPASSWORD="$TRADING_PSQL_PASSWORD" psql \
  -h "$TRADING_PSQL_HOST" \
  -U "$TRADING_PSQL_USER" \
  -d "$TRADING_PSQL_DB" \
  -f database/migrations/750_create_foundation_tables.sql

# Load seed data
echo "Loading seed data..."
PGPASSWORD="$TRADING_PSQL_PASSWORD" psql \
  -h "$TRADING_PSQL_HOST" \
  -U "$TRADING_PSQL_USER" \
  -d "$TRADING_PSQL_DB" \
  -f database/seeds/750_foundation_seed_data.sql

# Validate
echo "Validating migration..."
PGPASSWORD="$TRADING_PSQL_PASSWORD" psql \
  -h "$TRADING_PSQL_HOST" \
  -U "$TRADING_PSQL_USER" \
  -d "$TRADING_PSQL_DB" \
  -f database/migrations/750_validate_migration.sql

echo "Migration 750 complete!"
```

Usage:
```bash
chmod +x migrate_750.sh
./migrate_750.sh
```

---

## Post-Migration Verification

### 1. Verify Tables Created

```sql
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions')
ORDER BY table_name;
```

**Expected**: 5 tables listed.

### 2. Verify minute_ohlcv Columns Added

```sql
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'minute_ohlcv'
  AND column_name IN ('adjustment_policy', 'session_type', 'provider_version', 'data_version', 'ingestion_timestamp')
ORDER BY column_name;
```

**Expected**: 5 new columns.

### 3. Verify Indexes Created

```sql
SELECT tablename, indexname
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions', 'minute_ohlcv')
ORDER BY tablename, indexname;
```

**Expected**: Multiple indexes per table.

### 4. Verify Seed Data Loaded

```sql
-- Check instruments
SELECT COUNT(*) as instrument_count FROM instruments;
-- Expected: 50 (top 50 US stocks)

-- Check calendars
SELECT calendar_id FROM trading_calendars ORDER BY calendar_id;
-- Expected: CME, NASDAQ, NYSE

-- Check holidays
SELECT COUNT(*) as holiday_count FROM trading_holidays WHERE calendar_id = 'NYSE';
-- Expected: 22 (11 per year for 2024-2025)

-- Check provider mappings
SELECT COUNT(*) as mapping_count FROM provider_symbol_mapping;
-- Expected: 50 (one per instrument)
```

### 5. Run Validation Scripts

```bash
# Calendar validation
psql -f scripts/validate_750_calendar_data.sql

# Instrument validation
psql -f scripts/validate_750_instrument_data.sql
```

**Expected**: All checks show ✓ PASS.

### 6. Test Python Modules

```bash
# Run manual test script
python scripts/test_foundation_manual.py
```

**Expected**: All tests pass, no errors.

---

## Rollback Procedure

### When to Rollback

- Critical bug discovered in migration
- Performance issues with new schema
- Need to revert to previous state for testing

### Rollback Steps

#### Step 1: Backup Current State (Optional)

```bash
pg_dump -h <host> -U <user> -d <database> -F c -f backup_before_rollback_$(date +%Y%m%d).dump
```

#### Step 2: Run Rollback Script

```bash
# Using psql
psql -h <host> -U <user> -d <database> -f database/migrations/750_rollback_foundation_tables.sql

# Or in DataGrip
# Open and execute 750_rollback_foundation_tables.sql
```

#### Step 3: Verify Rollback

```sql
-- Verify tables dropped
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions');
-- Expected: 0 rows

-- Verify minute_ohlcv columns removed
SELECT column_name
FROM information_schema.columns
WHERE table_name = 'minute_ohlcv'
  AND column_name IN ('adjustment_policy', 'session_type', 'provider_version', 'data_version', 'ingestion_timestamp');
-- Expected: 0 rows
```

### Rollback Warnings

⚠️ **Data Loss**: Rolling back will **permanently delete** all data in:
- instruments
- provider_symbol_mapping
- trading_holidays
- trading_sessions
- metadata columns in minute_ohlcv

⚠️ **No Partial Rollback**: The rollback script is all-or-nothing.

⚠️ **Foreign Key Dependencies**: If you've added custom tables that reference these tables, rollback will fail. Remove those tables first.

---

## Troubleshooting

### Problem: "relation already exists"

**Symptom**: Error during migration about tables already existing.

**Solution**: Migration script uses `IF NOT EXISTS` - this error shouldn't occur. If it does:
1. Check if tables exist: `\dt instruments`
2. If they exist from a previous attempt, either:
   - Skip migration (already applied)
   - Rollback first, then re-run

### Problem: "must be owner of table minute_ohlcv"

**Symptom**: Permission denied when altering minute_ohlcv.

**Solution**:
1. Ensure user has ALTER TABLE privileges
2. Or run as database owner:
```sql
GRANT ALL ON TABLE minute_ohlcv TO trading_app;
```

### Problem: Seed data fails with "duplicate key value"

**Symptom**: Seed script fails on INSERT with unique constraint violation.

**Solution**: Seed data already loaded. Check:
```sql
SELECT COUNT(*) FROM instruments;
```
If count > 0, seed data is present. Safe to continue.

### Problem: Validation script shows failures

**Symptom**: Validation checks show ✗ FAIL.

**Solution**:
1. Review specific failure messages
2. Common issues:
   - Holiday counts wrong: Check seed data loaded
   - Market hours wrong: Check trading_calendars data
   - Missing instruments: Run seed data script

### Problem: Performance degradation after migration

**Symptom**: Queries slower after adding new tables.

**Diagnosis**:
```sql
-- Check index usage
SELECT schemaname, tablename, indexname, idx_scan
FROM pg_stat_user_indexes
WHERE tablename IN ('instruments', 'provider_symbol_mapping')
ORDER BY idx_scan ASC;
```

**Solution**:
1. Ensure indexes were created (check pg_indexes)
2. Run ANALYZE on new tables:
```sql
ANALYZE instruments;
ANALYZE provider_symbol_mapping;
ANALYZE trading_calendars;
ANALYZE trading_holidays;
ANALYZE trading_sessions;
```

### Problem: minute_ohlcv queries returning unexpected results

**Symptom**: Queries on minute_ohlcv behave differently.

**Cause**: New columns have defaults, might affect WHERE clauses.

**Solution**: Update queries to explicitly handle new columns:
```sql
-- Before
SELECT * FROM minute_ohlcv WHERE symbol = 'AAPL';

-- After (if filtering by session matters)
SELECT * FROM minute_ohlcv
WHERE symbol = 'AAPL'
  AND session_type = 'RTH';
```

---

## Support

### Getting Help

1. **Check validation scripts**: Run `validate_750_calendar_data.sql` and `validate_750_instrument_data.sql`
2. **Review logs**: Check PostgreSQL logs for detailed error messages
3. **Test environment**: Try migration on test database first
4. **Documentation**: See [Schema Documentation](foundation_schema.md)

### Reporting Issues

When reporting migration issues, include:
1. PostgreSQL version
2. TimescaleDB version
3. Full error message
4. Output of validation scripts
5. Table sizes: `SELECT pg_size_pretty(pg_total_relation_size('table_name'));`

---

## Migration History

| Date | Migration | Description | Status |
|------|-----------|-------------|--------|
| 2024-XX-XX | 025 | Initial minute_ohlcv table | Applied |
| 2025-01-22 | 750 | Foundation tables | Current |

---

**Last Updated**: 2025-01-22
**Migration Version**: 750
**Schema Version**: 1.0
