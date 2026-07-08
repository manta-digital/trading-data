# Deploy TimescaleDB Schema to <db-host>

## Current Situation

The TimescaleDB schema exists on one host but needs to be deployed to the dedicated minute/tick data host at <db-host>.

## Deployment Instructions

### Option 1: Using DataGrip (Recommended)

1. **Open DataGrip** and connect to `manta-minute-timescale-user` (<db-host>)
2. **Switch to postgres user** if needed for initial setup
3. **Open and run**: `sql/final_working.sql`
   - This script is idempotent - safe to run multiple times
   - Creates everything: hypertable, 7 aggregation levels, compression

### Option 2: Using psql Command Line

```bash
# From your local machine
PGPASSWORD='[postgres-password]' psql \
  -h <db-host> \
  -U trading_app \
  -d trading \
  -f sql/final_working.sql
```

### Option 3: If Database Doesn't Exist

First create the database as postgres user:
```bash
PGPASSWORD='[postgres_password]' psql \
  -h <db-host> \
  -U postgres \
  -c "CREATE DATABASE trading;"
```

Then run the schema script.

## What Gets Created

1. **Core Hypertable**: `minute_ohlcv` with 4-hour chunks
2. **7 Aggregation Levels**:
   - 5-minute bars
   - 15-minute bars
   - Hourly bars
   - 4-hour bars (half trading day)
   - Daily bars
   - Weekly bars
   - Monthly bars
3. **Compression**: 95% space savings
4. **Indexes**: Optimized for symbol+time queries
5. **Test Data**: 7 sample rows for validation

## Validation

After deployment, the script outputs validation results showing:
- Base table count: 7 rows
- All aggregation levels populated
- Sample data visible

## Next Steps

Once deployed, the performance demo will work and show:
- Write performance >15,000 rows/sec
- Query performance <500ms for 1-day data
- Aggregated queries <100ms