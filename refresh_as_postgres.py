#!/usr/bin/env python3
"""
Refresh aggregations as postgres superuser to fix permissions issue
"""

import asyncio
import os
from dotenv import load_dotenv
import psycopg2
from loguru import logger

load_dotenv()

async def refresh_as_postgres():
    """Use MARKET_PSQL credentials (postgres user) to refresh aggregations"""
    
    # Use MARKET_PSQL_* credentials which should be postgres superuser
    conn_params = {
        'host': os.getenv('MARKET_PSQL_HOST'),  # Should be <prototype-host> 
        'database': 'postgres',  # Connect to postgres system database first
        'user': os.getenv('MARKET_PSQL_USER'),  # Should be postgres
        'password': os.getenv('MARKET_PSQL_PASSWORD'),
        'port': int(os.getenv('MARKET_PSQL_PORT', '5432'))
    }
    
    logger.info(f"Connecting to {conn_params['host']} as {conn_params['user']}")
    
    conn = None
    try:
        # First connect to postgres db, then switch to trading_test
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        
        # Close and reconnect to trading_test database
        conn.close()
        
        conn_params['database'] = os.getenv('TRADING_PSQL_DB', 'trading_test')
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        logger.info(f"Connected to database: {conn_params['database']}")
        
        # Check if we can see the continuous aggregations
        cur.execute("""
            SELECT view_name 
            FROM timescaledb_information.continuous_aggregates 
            WHERE view_name LIKE '%minute%ohlcv'
            ORDER BY view_name
        """)
        
        agg_views = cur.fetchall()
        if agg_views:
            logger.info(f"Found {len(agg_views)} continuous aggregations:")
            for view in agg_views:
                logger.info(f"  - {view[0]}")
        else:
            logger.warning("No continuous aggregations found!")
            return False
        
        # Now refresh them
        logger.info("=== Refreshing Continuous Aggregations ===")
        aggregations_to_refresh = [
            'minute_5min_ohlcv',
            'minute_15min_ohlcv', 
            'minute_hourly_ohlcv',
            'minute_4hour_ohlcv',
            'minute_daily_ohlcv',
            'minute_weekly_ohlcv',
            'minute_monthly_ohlcv'
        ]
        
        for agg_view in aggregations_to_refresh:
            try:
                logger.info(f"Refreshing {agg_view}...")
                cur.execute(f"CALL refresh_continuous_aggregate('{agg_view}', NULL, NULL)")
                logger.success(f"✅ Refreshed {agg_view}")
            except Exception as e:
                logger.error(f"❌ Failed to refresh {agg_view}: {e}")
        
        # Check row counts after refresh
        logger.info("\n=== Row Counts After Refresh ===")
        views = [
            'minute_ohlcv',
            'minute_5min_ohlcv', 
            'minute_15min_ohlcv',
            'minute_hourly_ohlcv',
            'minute_4hour_ohlcv',
            'minute_daily_ohlcv'
        ]
        
        for view in views:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                logger.info(f"{view}: {count} rows")
            except Exception as e:
                logger.error(f"Error counting {view}: {e}")
        
        cur.close()
        return True
        
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Refreshing aggregations as postgres user...")
    
    try:
        result = asyncio.run(refresh_as_postgres())
        if result:
            logger.success("✅ Aggregation refresh completed!")
        else:
            logger.error("❌ Aggregation refresh failed")
    except Exception as e:
        logger.error(f"Script failed: {e}")