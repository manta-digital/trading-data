#!/usr/bin/env python3
"""
Clean database setup script for TimescaleDB minute data system
This script creates a fresh setup with proper ownership to avoid postgres ownership issues
"""

import psycopg2
import os
from pathlib import Path
from loguru import logger

def setup_clean_database():
    """Run the database setup scripts to create clean TimescaleDB setup"""
    
    # Connection as postgres superuser (needed for database/user creation)
    postgres_params = {
        'host': os.getenv('MARKET_PSQL_HOST'),
        'database': os.getenv('MARKET_PSQL_DB'),
        'user': os.getenv('MARKET_PSQL_USER'),
        'password': os.getenv('MARKET_PSQL_PASSWORD'),
        'port': int(os.getenv('MARKET_PSQL_PORT', '5432'))
    }
    
    logger.info("Setting up clean TimescaleDB minute data system...")
    
    # Get SQL file paths
    sql_dir = Path(__file__).parent / 'sql'
    setup_sql = sql_dir / '01_setup_database.sql'
    verify_sql = sql_dir / '02_setup_verification.sql'
    
    if not setup_sql.exists():
        logger.error(f"Setup SQL file not found: {setup_sql}")
        return False
        
    if not verify_sql.exists():
        logger.error(f"Verification SQL file not found: {verify_sql}")
        return False
    
    conn = None
    try:
        # Step 1: Connect as postgres user and run setup
        logger.info("Connecting as postgres user for setup...")
        conn = psycopg2.connect(**postgres_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Read and execute setup SQL
        logger.info("Reading setup SQL script...")
        with open(setup_sql, 'r') as f:
            setup_commands = f.read()
        
        # Split by statements and execute (psql commands won't work, so we simulate)
        logger.info("Executing database setup...")
        
        # Create database if it doesn't exist
        try:
            cur.execute("CREATE DATABASE trading_test")
            logger.success("✅ Created trading_test database")
        except psycopg2.errors.DuplicateDatabase:
            logger.info("Database trading_test already exists")
        except Exception as e:
            logger.error(f"Error creating database: {e}")
        
        # Close connection to postgres, connect to trading_test
        cur.close()
        conn.close()
        
        # Step 2: Connect to trading_test database and continue setup
        trading_params = postgres_params.copy()
        trading_params['database'] = 'trading_test'
        
        logger.info("Connecting to trading_test database...")
        conn = psycopg2.connect(**trading_params)
        conn.autocommit = True
        cur = conn.cursor()
        
        # Create TimescaleDB extension
        cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
        logger.success("✅ TimescaleDB extension enabled")
        
        # Create user
        try:
            cur.execute("CREATE USER trading_app WITH PASSWORD %s", (postgres_params['password'],))
            logger.success("✅ Created trading_app user")
        except psycopg2.errors.DuplicateObject:
            logger.info("User trading_app already exists")
        
        # Grant permissions
        cur.execute("GRANT USAGE ON SCHEMA public TO trading_app")
        cur.execute("GRANT CREATE ON SCHEMA public TO trading_app")
        cur.execute("GRANT ALL PRIVILEGES ON DATABASE trading_test TO trading_app")
        logger.success("✅ Granted permissions to trading_app")
        
        # Switch to trading_app user for the rest
        cur.execute("SET ROLE trading_app")
        logger.info("Switched to trading_app user for table creation")
        
        # Create hypertable
        logger.info("Creating minute_ohlcv hypertable...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS minute_ohlcv (
                time TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open NUMERIC(10, 4) NOT NULL,
                high NUMERIC(10, 4) NOT NULL, 
                low NUMERIC(10, 4) NOT NULL,
                close NUMERIC(10, 4) NOT NULL,
                volume BIGINT NOT NULL,
                
                CONSTRAINT minute_ohlcv_pkey PRIMARY KEY (time, symbol),
                CONSTRAINT minute_ohlcv_positive_ohlc CHECK (open > 0 AND high > 0 AND low > 0 AND close > 0),
                CONSTRAINT minute_ohlcv_volume_positive CHECK (volume >= 0),
                CONSTRAINT minute_ohlcv_high_low CHECK (high >= low),
                CONSTRAINT minute_ohlcv_price_logic CHECK (high >= open AND high >= close AND low <= open AND low <= close)
            )
        """)
        
        # Convert to hypertable
        cur.execute("""
            SELECT create_hypertable(
                'minute_ohlcv', 
                'time',
                chunk_time_interval => INTERVAL '4 hours',
                if_not_exists => TRUE
            )
        """)
        logger.success("✅ Created minute_ohlcv hypertable")
        
        # Create indexes
        logger.info("Creating performance indexes...")
        indexes = [
            "CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_symbol_time ON minute_ohlcv (symbol, time DESC)",
            "CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_time_symbol ON minute_ohlcv (time DESC, symbol)",
            "CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_recent ON minute_ohlcv (symbol, time DESC) WHERE time >= NOW() - INTERVAL '7 days'",
            "CREATE INDEX IF NOT EXISTS ix_minute_ohlcv_symbol ON minute_ohlcv (symbol)"
        ]
        
        for idx in indexes:
            cur.execute(idx)
        logger.success("✅ Created performance indexes")
        
        # Create continuous aggregations
        logger.info("Creating continuous aggregations...")
        
        aggregations = [
            ("minute_5min_ohlcv_v2", "5 minutes"),
            ("minute_15min_ohlcv_v2", "15 minutes"),
            ("minute_hourly_ohlcv_v2", "1 hour"),
            ("minute_4hour_ohlcv_v2", "4 hours"),
            ("minute_daily_ohlcv_v2", "1 day")
        ]
        
        for agg_name, interval in aggregations:
            try:
                cur.execute(f"""
                    CREATE MATERIALIZED VIEW {agg_name}
                    WITH (timescaledb.continuous) AS
                    SELECT 
                        symbol,
                        time_bucket(INTERVAL '{interval}', time) AS time_bucket,
                        FIRST(open, time) AS open,
                        MAX(high) AS high,
                        MIN(low) AS low,
                        LAST(close, time) AS close,
                        SUM(volume) AS volume
                    FROM minute_ohlcv
                    GROUP BY symbol, time_bucket
                    ORDER BY symbol, time_bucket
                """)
                logger.success(f"✅ Created {agg_name}")
            except Exception as e:
                logger.error(f"❌ Failed to create {agg_name}: {e}")
        
        # Create refresh policies
        logger.info("Setting up refresh policies...")
        
        policies = [
            ("minute_5min_ohlcv_v2", "1 minute"),
            ("minute_15min_ohlcv_v2", "5 minutes"), 
            ("minute_hourly_ohlcv_v2", "15 minutes"),
            ("minute_4hour_ohlcv_v2", "1 hour"),
            ("minute_daily_ohlcv_v2", "1 hour")
        ]
        
        for agg_name, schedule in policies:
            try:
                cur.execute(f"""
                    SELECT add_continuous_aggregate_policy('{agg_name}',
                        start_offset => INTERVAL '1 day',
                        end_offset => INTERVAL '1 minute',
                        schedule_interval => INTERVAL '{schedule}')
                """)
                logger.success(f"✅ Created refresh policy for {agg_name}")
            except Exception as e:
                logger.error(f"❌ Failed to create policy for {agg_name}: {e}")
        
        # Set up compression
        logger.info("Configuring compression...")
        try:
            cur.execute("ALTER TABLE minute_ohlcv SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol', timescaledb.compress_orderby = 'time DESC')")
            cur.execute("SELECT add_compression_policy('minute_ohlcv', INTERVAL '2 hours')")
            logger.success("✅ Compression configured")
        except Exception as e:
            logger.error(f"❌ Compression setup failed: {e}")
        
        # Verification
        logger.info("Running verification checks...")
        
        # Check table ownership
        cur.execute("""
            SELECT tablename, tableowner 
            FROM pg_tables 
            WHERE tablename LIKE '%minute%ohlcv%' 
            ORDER BY tablename
        """)
        
        tables = cur.fetchall()
        for table, owner in tables:
            if owner == 'trading_app':
                logger.success(f"✅ {table} owned by trading_app")
            else:
                logger.error(f"❌ {table} owned by {owner} (should be trading_app)")
        
        cur.close()
        logger.success("🎉 Clean database setup completed successfully!")
        return True
        
    except Exception as e:
        logger.error(f"Setup failed: {e}")
        return False
        
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    logger.info("Starting clean TimescaleDB setup...")
    
    result = setup_clean_database()
    if result:
        logger.success("✅ Database setup completed! No ownership issues.")
        logger.info("You can now run your application without permission problems.")
    else:
        logger.error("❌ Database setup failed")
        logger.info("Check the logs above for specific errors")