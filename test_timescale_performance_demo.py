#!/usr/bin/env python3
"""Quick performance demonstration of TimescaleMinuteDataDB"""

import asyncio
import time
import os
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from dotenv import load_dotenv
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

async def performance_demo():
    """Demonstrate write and query performance"""
    load_dotenv()
    
    # Database configuration for trading database with TimescaleDB
    # SECURITY: Always use environment variables for credentials
    db_config = {
        'host': os.getenv('TRADING_PSQL_HOST', 'localhost'),
        'database': os.getenv('TRADING_PSQL_DATABASE', 'trading'),
        'user': os.getenv('TRADING_PSQL_USER', 'trading_app'),
        'password': os.getenv('TRADING_PSQL_PASSWORD', ''),
        'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
    }
    
    # Validate configuration
    if not db_config['password']:
        print("❌ Error: TRADING_PSQL_PASSWORD not set in environment")
        print("   Please ensure .env file contains TRADING_PSQL_* variables")
        return False
    
    print("\n" + "="*70)
    print("TimescaleMinuteDataDB Performance Demonstration")
    print("="*70)
    
    try:
        # Initialize database connection
        db = TimescaleMinuteDataDB(db_config)
        print("✅ Connection pool initialized")
        
        # Create test data (10,000 rows)
        print("\n📊 Creating test dataset...")
        rows = 10000
        start_time = datetime(2024, 1, 2, 9, 30)
        timestamps = pd.date_range(start=start_time, periods=rows, freq='1min')
        
        # Generate realistic OHLCV data
        base_price = 250.0
        price_changes = np.random.normal(0, 0.1, rows)
        prices = np.cumsum(price_changes) + base_price
        
        test_data = pd.DataFrame({
            'open': np.round(prices + np.random.normal(0, 0.05, rows), 4),
            'high': np.round(prices + np.abs(np.random.normal(0, 0.1, rows)), 4),
            'low': np.round(prices - np.abs(np.random.normal(0, 0.1, rows)), 4),
            'close': np.round(prices + np.random.normal(0, 0.05, rows), 4),
            'volume': np.random.randint(10000, 100000, rows)
        }, index=timestamps)
        
        print(f"  Created {rows:,} rows of OHLCV data")
        
        # Test 1: Bulk Write Performance
        print("\n🚀 TEST 1: Bulk Write Performance")
        write_start = time.perf_counter()
        result = await db.write_minute_data_bulk('DEMO_TEST', test_data)
        write_time = time.perf_counter() - write_start
        
        if result:
            rows_per_sec = rows / write_time
            print(f"  ✅ Wrote {rows:,} rows in {write_time:.3f} seconds")
            print(f"  📈 Performance: {rows_per_sec:,.0f} rows/second")
            if rows_per_sec > 15000:
                print(f"  🎯 EXCEEDS TARGET (>15,000 rows/sec)")
            else:
                print(f"  ⚠️  Below target, but may be due to small dataset")
        
        # Test 2: Query Performance - Minute Data
        print("\n⚡ TEST 2: Query Performance - Raw Minute Data")
        query_start = time.perf_counter()
        df = await db.get_minute_data('DEMO_TEST', start_time, start_time + timedelta(days=1))
        query_time = time.perf_counter() - query_start
        
        print(f"  ✅ Queried 1 day of minute data")
        print(f"  📊 Retrieved {len(df)} rows in {query_time*1000:.1f}ms")
        if query_time < 0.5:
            print(f"  🎯 MEETS TARGET (<500ms for 1-day query)")
        
        # Test 3: Aggregated Query Performance
        print("\n⚡ TEST 3: Aggregated Query Performance")
        aggregations = ['5min', '15min', '1hour', '4hour']
        
        for agg in aggregations:
            agg_start = time.perf_counter()
            df_agg = await db.get_minute_data('DEMO_TEST', start_time, 
                                             start_time + timedelta(days=1), 
                                             aggregation=agg)
            agg_time = time.perf_counter() - agg_start
            
            print(f"  {agg:6s}: {len(df_agg):3d} bars in {agg_time*1000:5.1f}ms", end="")
            if agg_time < 0.1:
                print(" ✅ (<100ms)")
            else:
                print()
        
        # Test 4: System Metrics
        print("\n📊 TEST 4: System Metrics")
        metrics = await db.get_system_metrics()
        
        if 'hypertable' in metrics:
            print(f"  Hypertable: {metrics['hypertable']['name']}")
            print(f"  Chunks: {metrics['hypertable']['chunks']}")
            print(f"  Size: {metrics['hypertable']['size']}")
        
        if 'compression' in metrics:
            comp = metrics['compression']
            if comp['total_chunks'] > 0:
                ratio = comp['avg_compression_ratio'] * 100 if comp['avg_compression_ratio'] else 0
                print(f"  Compression: {comp['compressed_chunks']}/{comp['total_chunks']} chunks")
                print(f"  Compression ratio: {ratio:.1f}%")
        
        # Clean up test data
        print("\n🧹 Cleaning up test data...")
        async with db.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM minute_ohlcv WHERE symbol = 'DEMO_TEST'")
                conn.commit()
                print("  ✅ Test data cleaned up")
        
        db.close()
        print("\n✅ Demo completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        return False
    
    return True

if __name__ == "__main__":
    asyncio.run(performance_demo())