#!/usr/bin/env python3
"""
Manual refresh of TimescaleDB continuous aggregations
Since we have 2002 rows but only 2-4 in aggregated views
"""

import asyncio
import os
from dotenv import load_dotenv
import psycopg2
from loguru import logger

load_dotenv()

async def refresh_all_aggregations():
    """Manually refresh all continuous aggregations"""
    
    # Direct psycopg2 connection (outside transaction)
    conn_params = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True  # Avoid transaction issues
        cur = conn.cursor()
        
        # First check current row counts
        logger.info("=== Current Row Counts ===")
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
        
        # Now refresh continuous aggregations
        logger.info("\n=== Refreshing Continuous Aggregations ===")
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
        for view in views:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                logger.info(f"{view}: {count} rows")
            except Exception as e:
                logger.error(f"Error counting {view}: {e}")
        
        cur.close()
        
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()
    
    return True

if __name__ == "__main__":
    logger.info("Starting continuous aggregation refresh...")
    
    try:
        result = asyncio.run(refresh_all_aggregations())
        if result:
            logger.success("✅ Aggregation refresh completed!")
        else:
            logger.error("❌ Aggregation refresh failed")
    except Exception as e:
        logger.error(f"Script failed: {e}")