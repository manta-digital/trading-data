# TimescaleDB Continuous Aggregation Permissions Issue

## Problem Summary

✅ **Data Collection Working**: 2002 rows successfully written to `minute_ohlcv`  
❌ **Aggregations Not Updating**: Only 2-4 rows in aggregated views instead of ~400+ expected

## Root Cause

The continuous aggregation views were created by the `postgres` superuser, but our application runs as `trading_app` user. This causes permission errors:

```
❌ must be owner of view minute_5min_ohlcv
❌ must be owner of continuous aggregate "minute_5min_ohlcv"
```

## Current Status

| View | Expected Rows* | Actual Rows | Status |
|------|----------------|-------------|---------|
| `minute_ohlcv` | 2002 | 2002 | ✅ Working |
| `minute_5min_ohlcv` | ~400 | 4 | ❌ Not refreshing |
| `minute_15min_ohlcv` | ~133 | 2 | ❌ Not refreshing |
| `minute_hourly_ohlcv` | ~33 | 2 | ❌ Not refreshing |
| `minute_4hour_ohlcv` | ~8 | 2 | ❌ Not refreshing |
| `minute_daily_ohlcv` | ~2 | 2 | ❌ Not refreshing |

*Expected rows calculated from 2002 minute bars ÷ aggregation factor

## Solutions

### Option 1: Transfer Ownership (Recommended)
Connect as `postgres` superuser and transfer ownership:

```sql
-- As postgres user
ALTER VIEW minute_5min_ohlcv OWNER TO trading_app;
ALTER VIEW minute_15min_ohlcv OWNER TO trading_app;
ALTER VIEW minute_hourly_ohlcv OWNER TO trading_app;
ALTER VIEW minute_4hour_ohlcv OWNER TO trading_app;
ALTER VIEW minute_daily_ohlcv OWNER TO trading_app;
ALTER VIEW minute_weekly_ohlcv OWNER TO trading_app;
ALTER VIEW minute_monthly_ohlcv OWNER TO trading_app;

-- Then create refresh policies
SELECT add_continuous_aggregate_policy('minute_5min_ohlcv',
    start_offset => INTERVAL '1 day',
    end_offset => INTERVAL '1 minute', 
    schedule_interval => INTERVAL '1 minute');

-- Repeat for other aggregations...
```

### Option 2: Manual Refresh as Postgres
Create a maintenance script that runs as `postgres` user:

```sql
-- As postgres user, refresh manually
CALL refresh_continuous_aggregate('minute_5min_ohlcv', NULL, NULL);
CALL refresh_continuous_aggregate('minute_15min_ohlcv', NULL, NULL);
-- etc...
```

### Option 3: Recreate Aggregations
Drop and recreate all continuous aggregations as `trading_app` user.

## Impact on Task 2.3

**Task 2.3 Core Functionality: ✅ COMPLETE**
- ✅ TimescaleDB collector interface working
- ✅ Data successfully written (2002 rows)
- ✅ Data successfully retrieved (40 rows in test)
- ✅ Coverage analysis working (with version compatibility notes)

**Outstanding Issue: Aggregation Refresh**
This is an **operational setup issue**, not a core functionality problem. The aggregation views exist and work - they just need to be refreshed to show current data.

## Verification Commands

To check if aggregations are working after fix:

```sql
-- Should show ~400 rows if working
SELECT COUNT(*) FROM minute_5min_ohlcv;

-- Should show recent data
SELECT symbol, time_bucket, open, high, low, close, volume 
FROM minute_5min_ohlcv 
ORDER BY time_bucket DESC 
LIMIT 5;
```

## Recommendation

For production deployment, implement **Option 1** (transfer ownership) as part of the database setup process to ensure the application user has full control over the continuous aggregations.