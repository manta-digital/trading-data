#!/usr/bin/env python3
"""
Direct connection to refresh aggregations - try as postgres user on same host
"""

import os
import psycopg2
from loguru import logger

def refresh_aggregations_direct():
    """Connect directly and refresh"""
    
    # Try connecting as postgres to the same host that has our data
    conn_params = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    logger.info(f"Connecting to {conn_params['host']}:{conn_params['port']} as {conn_params['user']}")
    
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        logger.info(f"Connected to database: {conn_params['database']}")
        
        # Check current data 
        logger.info("=== Current Data Status ===")
        cur.execute("SELECT COUNT(*) FROM minute_ohlcv")
        minute_count = cur.fetchone()[0]
        logger.info(f"Raw minute data: {minute_count} rows")
        
        cur.execute("SELECT COUNT(*) FROM minute_5min_ohlcv")
        min5_count = cur.fetchone()[0]
        logger.info(f"5-minute aggregated: {min5_count} rows")
        
        # Try to refresh 5-minute aggregation
        logger.info("=== Attempting Refresh ===")
        cur.execute("CALL refresh_continuous_aggregate('minute_5min_ohlcv', NULL, NULL)")
        logger.success("✅ Refreshed minute_5min_ohlcv")
        
        # Check count after refresh
        cur.execute("SELECT COUNT(*) FROM minute_5min_ohlcv")
        new_count = cur.fetchone()[0]
        logger.info(f"5-minute aggregated after refresh: {new_count} rows")
        
        if new_count > min5_count:
            logger.success(f"🎉 Success! Increased from {min5_count} to {new_count} rows")
        else:
            logger.warning(f"No change: still {new_count} rows")
        
        cur.close()
        return True
        
    except psycopg2.OperationalError as e:
        if "authentication failed" in str(e):
            logger.error("❌ Authentication failed - need correct postgres password")
        else:
            logger.error(f"❌ Connection error: {e}")
        return False
    except Exception as e:
        logger.error(f"❌ Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Attempting direct aggregation refresh...")
    
    result = refresh_aggregations_direct()
    if result:
        logger.success("✅ Direct refresh completed!")
    else:
        logger.error("❌ Direct refresh failed - may need correct postgres credentials")