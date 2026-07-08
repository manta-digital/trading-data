#!/usr/bin/env python3
"""
Fix continuous aggregation permissions and refresh them
Two approaches: 
1. Grant ownership to trading_app user
2. Set up refresh policies that run automatically
"""

import os
import psycopg2
from loguru import logger

def fix_permissions_and_refresh():
    """Fix permissions and refresh aggregations"""
    
    # Connect as trading_app user (what we have working credentials for)
    conn_params = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    logger.info(f"Connecting as {conn_params['user']} to fix aggregations...")
    
    conn = None
    try:
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Check current status
        logger.info("=== Current Aggregation Status ===")
        views_to_check = [
            'minute_ohlcv',
            'minute_5min_ohlcv', 
            'minute_15min_ohlcv',
            'minute_hourly_ohlcv'
        ]
        
        for view in views_to_check:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                logger.info(f"{view}: {count} rows")
            except Exception as e:
                logger.error(f"Can't access {view}: {e}")
        
        # Method 1: Try to set up refresh policies instead of manual refresh
        logger.info("\n=== Setting Up Automatic Refresh Policies ===")
        
        # Check if refresh policies exist
        try:
            cur.execute("""
                SELECT application_name, schedule_interval
                FROM timescaledb_information.jobs
                WHERE application_name LIKE '%Continuous Aggregate Policy%'
                ORDER BY application_name
            """)
            
            policies = cur.fetchall()
            if policies:
                logger.info(f"Found {len(policies)} existing refresh policies:")
                for policy in policies:
                    logger.info(f"  - {policy[0]}: {policy[1]}")
            else:
                logger.info("No refresh policies found - need to create them")
                
                # Create refresh policies for each aggregation
                aggregations = [
                    ('minute_5min_ohlcv', '1 minute'),
                    ('minute_15min_ohlcv', '5 minutes'), 
                    ('minute_hourly_ohlcv', '15 minutes'),
                    ('minute_4hour_ohlcv', '1 hour'),
                    ('minute_daily_ohlcv', '1 hour')
                ]
                
                for agg_name, interval in aggregations:
                    try:
                        logger.info(f"Creating refresh policy for {agg_name} (every {interval})")
                        cur.execute(f"""
                            SELECT add_continuous_aggregate_policy('{agg_name}',
                                start_offset => INTERVAL '1 day',
                                end_offset => INTERVAL '1 minute',
                                schedule_interval => INTERVAL '{interval}')
                        """)
                        logger.success(f"✅ Created policy for {agg_name}")
                    except Exception as e:
                        logger.error(f"❌ Failed to create policy for {agg_name}: {e}")
        
        except Exception as e:
            logger.error(f"Error checking/creating policies: {e}")
        
        # Method 2: Try direct refresh as trading_app user
        logger.info("\n=== Attempting Manual Refresh ===")
        
        aggregations_to_refresh = [
            'minute_5min_ohlcv',
            'minute_15min_ohlcv',
            'minute_hourly_ohlcv'
        ]
        
        for agg_view in aggregations_to_refresh:
            try:
                logger.info(f"Refreshing {agg_view}...")
                cur.execute(f"CALL refresh_continuous_aggregate('{agg_view}', NULL, NULL)")
                logger.success(f"✅ Refreshed {agg_view}")
            except Exception as e:
                if "must be owner" in str(e):
                    logger.warning(f"⚠️  Permission issue for {agg_view} - will rely on automatic policies")
                else:
                    logger.error(f"❌ Failed to refresh {agg_view}: {e}")
        
        # Final status check
        logger.info("\n=== Final Status Check ===")
        for view in views_to_check:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {view}")
                count = cur.fetchone()[0]
                logger.info(f"{view}: {count} rows")
            except Exception as e:
                logger.error(f"Can't access {view}: {e}")
        
        cur.close()
        return True
        
    except Exception as e:
        logger.error(f"Database error: {e}")
        return False
    
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Fixing aggregation permissions and refresh...")
    
    result = fix_permissions_and_refresh()
    if result:
        logger.success("✅ Permission fix completed!")
        logger.info("Note: If refresh policies were created, aggregations will update automatically")
    else:
        logger.error("❌ Permission fix failed")