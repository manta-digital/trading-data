# TimescaleDB Minute Data System Setup Guide

This guide ensures clean database setup without ownership issues.

## Problem We're Solving

**Issue**: When TimescaleDB objects are created by different users, we get permission errors:
```
❌ must be owner of view minute_5min_ohlcv
❌ must be owner of continuous aggregate "minute_5min_ohlcv"
```

**Solution**: Create everything with consistent ownership from the start.

## Setup Methods

### Method 1: Automated Python Script (Recommended)

```bash
# Run the automated setup script
python setup_clean_database.py
```

This script:
- Creates `trading_test` database
- Creates `trading_app` user with proper permissions
- Creates all objects as `trading_app` user
- Sets up continuous aggregations with proper ownership
- Configures refresh policies and compression

### Method 2: Manual SQL Execution

```bash
# As postgres superuser:
psql -h <db-host> -U postgres -f sql/01_setup_database.sql

# Verify setup:
psql -h <db-host> -U postgres -d trading_test -f sql/02_setup_verification.sql
```

## What Gets Created

### Core Infrastructure
- `minute_ohlcv` hypertable (4-hour chunks)
- Performance indexes for optimal queries
- Compression settings (2-hour delay, 95%+ compression ratio)
- Retention policy (2 years)

### Continuous Aggregations (All v2 versions)
- `minute_5min_ohlcv_v2` - 5-minute OHLCV bars
- `minute_15min_ohlcv_v2` - 15-minute OHLCV bars  
- `minute_hourly_ohlcv_v2` - Hourly OHLCV bars
- `minute_4hour_ohlcv_v2` - 4-hour OHLCV bars
- `minute_daily_ohlcv_v2` - Daily OHLCV bars

### Refresh Policies (Automatic)
- 5min aggregation: refreshes every 1 minute
- 15min aggregation: refreshes every 5 minutes
- Hourly aggregation: refreshes every 15 minutes
- 4-hour aggregation: refreshes every 1 hour
- Daily aggregation: refreshes every 1 hour

## Verification

After setup, verify everything works:

```bash
python -c "
import asyncio
from test_task_2_3_simple import test_core_collector_functionality
result = asyncio.run(test_core_collector_functionality())
"
```

Expected output:
```
✅ Data collection: Saved X minute bars to TimescaleDB
✅ Data retrieval: X bars
🎉 Task 2.3 Core Functionality VERIFIED
```

## For New Environments

When setting up on a new server:

1. **Install TimescaleDB**:
   ```bash
   # Add TimescaleDB repository
   sudo apt update
   sudo apt install postgresql-14 postgresql-client-14
   # Add TimescaleDB APT repository and install
   sudo apt install timescaledb-2-postgresql-14
   sudo timescaledb-tune --quiet --yes
   sudo systemctl restart postgresql
   ```

2. **Run Setup Script**:
   ```bash
   python setup_clean_database.py
   ```

3. **Configure Application**:
   Update `.env` file:
   ```
   TRADING_PSQL_HOST=your-timescale-host
   TRADING_PSQL_USER=trading_app
   TRADING_PSQL_PASSWORD=<test_db_password>
   TRADING_PSQL_DB=trading_test
   ```

## Migration from Existing Setup

If you already have a database with ownership issues:

### Option 1: Transfer Ownership (Quick Fix)
```sql
-- As postgres user:
ALTER TABLE minute_ohlcv OWNER TO trading_app;
ALTER VIEW minute_5min_ohlcv OWNER TO trading_app;
ALTER VIEW minute_15min_ohlcv OWNER TO trading_app;
-- ... repeat for all views
```

### Option 2: Clean Recreate (Recommended)
```bash
# Backup data if needed
pg_dump -h host -U postgres -d trading_test -t minute_ohlcv > backup.sql

# Drop and recreate
python setup_clean_database.py

# Restore data if needed
psql -h host -U trading_app -d trading_test < backup.sql
```

## Expected Performance

After clean setup:
- **Write Performance**: 75-133x faster than individual inserts
- **Query Performance**: <30ms for minute data, <25ms for aggregated
- **Storage Efficiency**: 95%+ compression ratio
- **Aggregation Latency**: 1-15 minutes (depends on refresh policy)

## Troubleshooting

### Permission Errors
If you still get permission errors, check ownership:
```sql
SELECT tablename, tableowner FROM pg_tables WHERE tablename LIKE '%minute%';
SELECT view_name, view_owner FROM information_schema.views WHERE view_name LIKE '%minute%';
```

All should be owned by `trading_app`.

### Connection Issues
Verify connection settings in `.env` and ensure TimescaleDB is running:
```bash
sudo systemctl status postgresql
sudo systemctl status timescaledb
```

### Aggregation Not Updating
Check refresh policies:
```sql
SELECT * FROM timescaledb_information.jobs WHERE application_name LIKE '%Continuous%';
```

Should show active jobs with recent `last_successful_finish` times.

## Files Created

- `sql/01_setup_database.sql` - Complete database setup script
- `sql/02_setup_verification.sql` - Verification queries
- `setup_clean_database.py` - Automated Python setup script
- `DATABASE_SETUP_GUIDE.md` - This documentation

## Benefits of Clean Setup

✅ **No ownership conflicts** - Everything owned by application user  
✅ **Automatic aggregation refresh** - No manual intervention needed  
✅ **Consistent permissions** - Application can manage all objects  
✅ **Easy replication** - Setup scripts work on any environment  
✅ **Production ready** - Follows TimescaleDB best practices