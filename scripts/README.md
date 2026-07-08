# Scripts Directory

Utility scripts for database management, testing, and maintenance.

## Database Scripts

### verify_750_migration.sql
Verification script for Slice 750 foundation tables migration.

**Usage in DataGrip:**
1. Open the script in DataGrip
2. Select your `trading_test` database connection
3. Run the entire script (Ctrl/Cmd + Enter)

**Usage from command line:**
```bash
source .env
PGPASSWORD="$TRADING_PSQL_PASSWORD" psql \
  -h "$TRADING_PSQL_HOST" \
  -U "$TRADING_PSQL_USER" \
  -d "$TRADING_PSQL_DB" \
  -f scripts/verify_750_migration.sql
```

**What it checks:**
- Table existence (instruments, provider_symbol_mapping, trading_calendars, etc.)
- Row counts and data summaries
- Sample data from each table
- New columns on minute_ohlcv
- Indexes and foreign key constraints

## Python Test Scripts

### test_foundation_manual.py
Manual integration test for Slice 750 foundation Python modules.

**Usage:**
```bash
source .env
python scripts/test_foundation_manual.py
```

**What it tests:**
- AdjustmentPolicy enums and validation
- DataVersion dataclass
- OHLCV consistency validation
- InstrumentRegistry database operations:
  - Lookup by canonical ID
  - Lookup by provider symbol
  - Historical lookups with date ranges
  - List instruments with filters
  - Cache behavior verification

**Prerequisites:**
- Database migrations must be run first
- Seed data must be loaded
- Environment variables configured (.env file)

## Other Scripts

### update-guides
Updates AI project guide symlinks (existing script).
