#!/usr/bin/env python3
"""
Create new continuous aggregations with proper ownership using new names
This bypasses the permission issue by creating fresh aggregations
"""

import os
import psycopg2
from loguru import logger

def create_new_aggregations():
    """Create new continuous aggregations with _v2 suffix as trading_app user"""
    
    conn_params = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    logger.info("Creating new continuous aggregations with proper ownership...")
    
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Check current data
        cur.execute("SELECT COUNT(*) FROM minute_ohlcv")
        raw_count = cur.fetchone()[0]
        logger.info(f"Raw minute data available: {raw_count} rows")
        
        logger.info("\n=== Creating New Aggregations (v2) ===")
        
        # 5-minute aggregation
        logger.info("Creating 5-minute aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_5min_ohlcv_v2
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
        logger.success("✅ Created minute_5min_ohlcv_v2")
        
        # 15-minute aggregation
        logger.info("Creating 15-minute aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_15min_ohlcv_v2
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
        logger.success("✅ Created minute_15min_ohlcv_v2")
        
        # Hourly aggregation
        logger.info("Creating hourly aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_hourly_ohlcv_v2
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
        logger.success("✅ Created minute_hourly_ohlcv_v2")
        
        # 4-hour aggregation
        logger.info("Creating 4-hour aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_4hour_ohlcv_v2
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
        logger.success("✅ Created minute_4hour_ohlcv_v2")
        
        # Daily aggregation
        logger.info("Creating daily aggregation...")
        cur.execute("""
            CREATE MATERIALIZED VIEW minute_daily_ohlcv_v2
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
        logger.success("✅ Created minute_daily_ohlcv_v2")
        
        # Initial refresh to populate with existing data
        logger.info("\n=== Initial Refresh ===")
        new_aggregations = [
            'minute_5min_ohlcv_v2',
            'minute_15min_ohlcv_v2', 
            'minute_hourly_ohlcv_v2',
            'minute_4hour_ohlcv_v2',
            'minute_daily_ohlcv_v2'
        ]
        
        for agg in new_aggregations:
            try:
                cur.execute(f"CALL refresh_continuous_aggregate('{agg}', NULL, NULL)")
                logger.success(f"✅ Refreshed {agg}")
            except Exception as e:
                logger.error(f"❌ Failed to refresh {agg}: {e}")
        
        # Create refresh policies
        logger.info("\n=== Setting Up Refresh Policies ===")
        
        policies = [
            ('minute_5min_ohlcv_v2', '1 minute'),
            ('minute_15min_ohlcv_v2', '5 minutes'),
            ('minute_hourly_ohlcv_v2', '15 minutes'),
            ('minute_4hour_ohlcv_v2', '1 hour'),
            ('minute_daily_ohlcv_v2', '1 hour')
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
        
        # Verify results
        logger.info("\n=== Verification ===")
        all_views = ['minute_ohlcv'] + new_aggregations
        
        for view in all_views:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                
                # Calculate expected rows for comparison
                if view == 'minute_ohlcv':
                    expected = f"{count} (raw data)"
                elif '5min' in view:
                    expected = f"~{raw_count//5} expected"
                elif '15min' in view:
                    expected = f"~{raw_count//15} expected"
                elif 'hourly' in view:
                    expected = f"~{raw_count//60} expected"
                elif '4hour' in view:
                    expected = f"~{raw_count//240} expected"
                elif 'daily' in view:
                    expected = f"~{raw_count//390} expected"  # ~6.5 hours of market data per day
                else:
                    expected = ""
                
                logger.info(f"{view}: {count} rows {expected}")
                
            except Exception as e:
                logger.error(f"Can't count {view}: {e}")
        
        cur.close()
        return True
        
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Creating new continuous aggregations...")
    
    result = create_new_aggregations()
    if result:
        logger.success("🎉 New aggregations created successfully!")
        logger.info("All v2 aggregations are owned by trading_app user and should auto-refresh")
        logger.info("You can now use the v2 aggregation views in your queries")
    else:
        logger.error("❌ New aggregation creation failed")