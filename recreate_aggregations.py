#!/usr/bin/env python3
"""
Recreate continuous aggregations with proper ownership
This ensures trading_app user owns and can manage all aggregations
"""

import os
import psycopg2
from loguru import logger

def recreate_aggregations():
    """Drop and recreate all continuous aggregations as trading_app user"""
    
    conn_params = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    logger.info("Recreating continuous aggregations with proper ownership...")
    
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Step 1: Drop existing aggregations
        logger.info("=== Dropping Existing Aggregations ===")
        aggregations = [
            'minute_5min_ohlcv',
            'minute_15min_ohlcv', 
            'minute_hourly_ohlcv',
            'minute_4hour_ohlcv',
            'minute_daily_ohlcv',
            'minute_weekly_ohlcv',
            'minute_monthly_ohlcv'
        ]
        
        for agg in aggregations:
            try:
                cur.execute(f"DROP MATERIALIZED VIEW IF EXISTS {agg} CASCADE")
                logger.success(f"✅ Dropped {agg}")
            except Exception as e:
                logger.warning(f"⚠️  Could not drop {agg}: {e}")
        
        # Step 2: Recreate aggregations with proper ownership
        logger.info("\n=== Creating New Aggregations ===")
        
        # 5-minute aggregation
        logger.info("Creating 5-minute aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_5min_ohlcv
            WITH (timescaledb.continuous) AS
            SELECT 
                symbol,
                time_bucket(INTERVAL '5 minutes', time) AS time_bucket,
                FIRST(open, time) AS open,
                MAX(high) AS high, 
                MIN(low) AS low,
                LAST(close, time) AS close,
                SUM(volume) AS volume
            FROM minute_ohlcv
            GROUP BY symbol, time_bucket
            ORDER BY symbol, time_bucket;
        """)
        logger.success("✅ Created minute_5min_ohlcv")
        
        # 15-minute aggregation
        logger.info("Creating 15-minute aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_15min_ohlcv
            WITH (timescaledb.continuous) AS
            SELECT 
                symbol,
                time_bucket(INTERVAL '15 minutes', time) AS time_bucket,
                FIRST(open, time) AS open,
                MAX(high) AS high,
                MIN(low) AS low, 
                LAST(close, time) AS close,
                SUM(volume) AS volume
            FROM minute_ohlcv
            GROUP BY symbol, time_bucket
            ORDER BY symbol, time_bucket;
        """)
        logger.success("✅ Created minute_15min_ohlcv")
        
        # Hourly aggregation
        logger.info("Creating hourly aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_hourly_ohlcv
            WITH (timescaledb.continuous) AS
            SELECT 
                symbol,
                time_bucket(INTERVAL '1 hour', time) AS time_bucket,
                FIRST(open, time) AS open,
                MAX(high) AS high,
                MIN(low) AS low,
                LAST(close, time) AS close, 
                SUM(volume) AS volume
            FROM minute_ohlcv
            GROUP BY symbol, time_bucket
            ORDER BY symbol, time_bucket;
        """)
        logger.success("✅ Created minute_hourly_ohlcv")
        
        # 4-hour aggregation
        logger.info("Creating 4-hour aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_4hour_ohlcv
            WITH (timescaledb.continuous) AS
            SELECT 
                symbol,
                time_bucket(INTERVAL '4 hours', time) AS time_bucket,
                FIRST(open, time) AS open,
                MAX(high) AS high,
                MIN(low) AS low,
                LAST(close, time) AS close,
                SUM(volume) AS volume
            FROM minute_ohlcv
            GROUP BY symbol, time_bucket
            ORDER BY symbol, time_bucket;
        """)
        logger.success("✅ Created minute_4hour_ohlcv")
        
        # Daily aggregation (market hours aware)
        logger.info("Creating daily aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_daily_ohlcv
            WITH (timescaledb.continuous) AS
            SELECT 
                symbol,
                time_bucket(INTERVAL '1 day', time AT TIME ZONE 'America/New_York') AS time_bucket,
                FIRST(open, time) AS open,
                MAX(high) AS high,
                MIN(low) AS low,
                LAST(close, time) AS close,
                SUM(volume) AS volume
            FROM minute_ohlcv
            GROUP BY symbol, time_bucket
            ORDER BY symbol, time_bucket;
        """)
        logger.success("✅ Created minute_daily_ohlcv")
        
        # Step 3: Create refresh policies
        logger.info("\n=== Setting Up Refresh Policies ===")
        
        policies = [
            ('minute_5min_ohlcv', '1 minute'),
            ('minute_15min_ohlcv', '5 minutes'),
            ('minute_hourly_ohlcv', '15 minutes'),
            ('minute_4hour_ohlcv', '1 hour'),
            ('minute_daily_ohlcv', '1 hour')
        ]
        
        for agg_name, interval in policies:
            try:
                cur.execute(f"""
                    SELECT add_continuous_aggregate_policy('{agg_name}',
                        start_offset => INTERVAL '1 day',
                        end_offset => INTERVAL '1 minute',
                        schedule_interval => INTERVAL '{interval}')
                """)
                logger.success(f"✅ Created refresh policy for {agg_name} (every {interval})")
            except Exception as e:
                logger.error(f"❌ Failed to create policy for {agg_name}: {e}")
        
        # Step 4: Initial refresh
        logger.info("\n=== Initial Refresh ===")
        for agg in ['minute_5min_ohlcv', 'minute_15min_ohlcv', 'minute_hourly_ohlcv', 'minute_4hour_ohlcv', 'minute_daily_ohlcv']:
            try:
                cur.execute(f"CALL refresh_continuous_aggregate('{agg}', NULL, NULL)")
                logger.success(f"✅ Refreshed {agg}")
            except Exception as e:
                logger.error(f"❌ Failed to refresh {agg}: {e}")
        
        # Step 5: Verify results
        logger.info("\n=== Verification ===")
        for agg in ['minute_ohlcv'] + aggregations[:5]:  # Check main ones
            try:
                cur.execute(f"SELECT COUNT(*) FROM {agg}")
                count = cur.fetchone()[0]
                logger.info(f"{agg}: {count} rows")
            except Exception as e:
                logger.error(f"Can't count {agg}: {e}")
        
        cur.close()
        return True
        
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Starting continuous aggregation recreation...")
    
    result = recreate_aggregations()
    if result:
        logger.success("🎉 Aggregations recreated successfully!")
        logger.info("All continuous aggregations now owned by trading_app user")
    else:
        logger.error("❌ Aggregation recreation failed")