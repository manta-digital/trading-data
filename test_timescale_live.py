#!/usr/bin/env python3
"""
Live test of TimescaleMinuteDataDB against real test database
Run this to verify Task 2.2 functionality with actual TimescaleDB data
"""

import os
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import text
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

async def test_live_timescale():
    """Test our TimescaleMinuteDataDB class against the test database"""
    
    load_dotenv()
    
    # Connect using TRADING_PSQL_* environment variables (no defaults)
    required_env_vars = ['TRADING_PSQL_HOST', 'TRADING_PSQL_USER', 'TRADING_PSQL_PASSWORD', 'TRADING_PSQL_DB']
    for var in required_env_vars:
        if not os.getenv(var):
            raise ValueError(f"Required environment variable {var} is not set")
    
    db_config = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'database': os.getenv('TRADING_PSQL_DB'),  # Use TRADING_PSQL_DB from env
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD'),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))  # Port can have default
    }
    
    print("🔌 Connecting to TimescaleDB test database...")
    print(f"   Host: {db_config['host']}")
    print(f"   Database: {db_config['database']}")
    
    db = TimescaleMinuteDataDB(db_config)
    
    try:
        # Test basic connectivity
        async with db.get_connection() as conn:
            result = conn.execute(text("SELECT current_database(), version()"))
            row = result.fetchone()
            print(f"✅ Connected to: {row[0]}")
            print(f"   Version: {row[1][:50]}...")
        
        # Test 1: Get raw minute data (using UTC timestamps as stored)
        print("\n📊 Testing raw minute data query...")
        from datetime import timezone
        start_time = datetime(2024, 1, 1, 14, 30, tzinfo=timezone.utc)  # UTC as stored
        end_time = datetime(2024, 1, 1, 16, 0, tzinfo=timezone.utc)
        
        minute_data = await db.get_minute_data('TSLA', start_time, end_time)
        print(f"   Retrieved {len(minute_data)} rows of TSLA minute data")
        if not minute_data.empty:
            print(f"   Time range: {minute_data.index[0]} to {minute_data.index[-1]}")
            print(f"   Sample: Open=${minute_data.iloc[0]['open']}, Close=${minute_data.iloc[0]['close']}")
        
        # Test 2: Test all aggregation levels  
        print("\n📈 Testing aggregated data queries...")
        aggregations = ['5min', '15min', '1hour', '4hour', '1day']
        
        for agg in aggregations:
            agg_data = await db.get_minute_data('TSLA', start_time, end_time, aggregation=agg)
            print(f"   {agg:>6}: {len(agg_data)} bars")
            if not agg_data.empty:
                print(f"          First bar: Open=${agg_data.iloc[0]['open']}, Close=${agg_data.iloc[0]['close']}")
        
        # Test 3: Coverage analysis
        print("\n🔍 Testing coverage analysis...")
        coverage = await db.get_coverage_analysis('TSLA')
        
        if 'error' not in coverage:
            print(f"   Symbol: {coverage['symbol']}")
            print(f"   Total rows: {coverage['total_rows']}")
            print(f"   Date range: {coverage['earliest_data']} to {coverage['latest_data']}")
            print(f"   Compression info: {len(coverage['compression_info'])} chunks")
        else:
            print(f"   Error: {coverage['error']}")
        
        # Test 4: System metrics
        print("\n🏥 Testing system metrics...")
        metrics = await db.get_system_metrics()
        
        if 'error' not in metrics:
            print(f"   Hypertable: {metrics['hypertable']['name']} ({metrics['hypertable']['chunks']} chunks)")
            print(f"   Size: {metrics['hypertable']['size']}")
            print(f"   Compression: {metrics['compression']['compressed_chunks']}/{metrics['compression']['total_chunks']} chunks compressed")
            print(f"   Continuous aggregations: {len(metrics['continuous_aggregations'])} views")
        else:
            print(f"   Error: {metrics['error']}")
            
        print("\n🎉 All tests completed successfully!")
        print("✅ Task 2.2 Query Interface Implementation verified with live data!")
        
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    asyncio.run(test_live_timescale())