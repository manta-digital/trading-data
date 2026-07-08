#!/usr/bin/env python3
"""
Database Performance Diagnostic Script

Tests various scenarios to identify what causes inconsistent database write performance:
- Different row counts
- Connection pooling vs individual connections  
- Different batch sizes
- Network vs processing overhead
- Index impact analysis
"""

import asyncio
import os
import sys
import time
import pandas as pd
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

# Add the trading directory to Python path
sys.path.append('/Users/manta/source/repos/manta/trading')

from manta_trading.market.marketdb import MarketDB
from manta_trading.api.alphavantage.alphavantageapi import AlphavantageAPI

async def create_test_data(symbol, num_rows):
    """Create synthetic OHLCV data for testing"""
    start_date = date.today() - timedelta(days=num_rows)
    dates = [start_date + timedelta(days=i) for i in range(num_rows)]
    
    # Generate synthetic data
    base_price = 100.0
    data = []
    
    for i, test_date in enumerate(dates):
        price = base_price + (i * 0.5)  # Gradual price increase
        data.append({
            'open': price,
            'high': price + 1.0,
            'low': price - 1.0, 
            'close': price + 0.5,
            'adjusted_close': price + 0.5,
            'volume': 1000000,
            'dividend_amount': 0.0,
            'split_coefficient': 1.0
        })
    
    # Create DataFrame with date index
    df = pd.DataFrame(data, index=dates)
    return df

async def test_write_performance(db, symbol, data, test_name):
    """Test write performance and return detailed timing"""
    print(f"\n{'='*50}")
    print(f"Test: {test_name}")
    print(f"Symbol: {symbol}, Rows: {len(data)}")
    print(f"{'='*50}")
    
    start_time = time.time()
    
    # Test the actual write operation
    success = db.writeDailyOHLCVAdjusted(symbol, data)
    
    end_time = time.time()
    duration = end_time - start_time
    
    if success:
        rows_per_second = len(data) / duration if duration > 0 else 0
        print(f"✅ SUCCESS: {duration:.3f}s ({len(data)} rows, {rows_per_second:.0f} rows/s)")
    else:
        print(f"❌ FAILED: {duration:.3f}s")
    
    return success, duration, len(data)

async def test_connection_scenarios():
    """Test different connection scenarios"""
    print("🔍 DATABASE PERFORMANCE DIAGNOSTICS")
    print("=" * 60)
    
    # Load environment
    load_dotenv()
    
    # Database configuration
    db_config = {
        '_dbname': os.getenv('MARKET_PSQL_DB'),
        '_user': os.getenv('MARKET_PSQL_USER'), 
        '_password': os.getenv('MARKET_PSQL_PASSWORD'),
        '_host': os.getenv('MARKET_PSQL_HOST')
    }
    
    print(f"Database: {db_config['_dbname']}@{db_config['_host']}")
    
    # Test scenarios with different row counts
    test_cases = [
        ("Small dataset", 100),
        ("Medium dataset", 1000), 
        ("Large dataset", 3000),
        ("Very large dataset", 6000)
    ]
    
    results = []
    
    # Test 1: Individual connections for each write
    print("\n🧪 TEST 1: Individual Connections (current approach)")
    for test_name, num_rows in test_cases:
        db = MarketDB(**db_config)
        test_symbol = f"TEST{num_rows}"
        test_data = await create_test_data(test_symbol, num_rows)
        
        success, duration, rows = await test_write_performance(
            db, test_symbol, test_data, f"{test_name} - Individual Connection"
        )
        results.append({
            'test': f"{test_name} - Individual",
            'rows': rows,
            'duration': duration,
            'rows_per_sec': rows / duration if duration > 0 else 0,
            'success': success
        })
        
        # Clean up
        await db.aclose()
    
    # Test 2: Connection pooling with context manager
    print("\n🧪 TEST 2: Connection Pooling (with context manager)")
    db = MarketDB(**db_config)
    
    with db:  # Use context manager for connection reuse
        for test_name, num_rows in test_cases:
            test_symbol = f"POOL{num_rows}"
            test_data = await create_test_data(test_symbol, num_rows)
            
            success, duration, rows = await test_write_performance(
                db, test_symbol, test_data, f"{test_name} - Connection Pool"
            )
            results.append({
                'test': f"{test_name} - Pooled", 
                'rows': rows,
                'duration': duration,
                'rows_per_sec': rows / duration if duration > 0 else 0,
                'success': success
            })
    
    await db.aclose()
    
    # Test 3: Multiple small writes vs single large write
    print("\n🧪 TEST 3: Batch Size Comparison")
    
    # Create one large dataset
    large_data = await create_test_data("BATCH_TEST", 3000)
    
    # Test single large write
    db = MarketDB(**db_config)
    success, duration, rows = await test_write_performance(
        db, "BATCH_LARGE", large_data, "Single Large Write (3000 rows)"
    )
    results.append({
        'test': "Single Large Write",
        'rows': rows, 
        'duration': duration,
        'rows_per_sec': rows / duration if duration > 0 else 0,
        'success': success
    })
    await db.aclose()
    
    # Test multiple small writes
    print("\n🔄 Multiple Small Writes (6 x 500 rows)...")
    total_duration = 0
    total_rows = 0
    all_success = True
    
    for i in range(6):
        db = MarketDB(**db_config)
        chunk_data = large_data.iloc[i*500:(i+1)*500].copy()
        
        success, duration, rows = await test_write_performance(
            db, f"BATCH_SMALL_{i}", chunk_data, f"Small Write #{i+1} (500 rows)"
        )
        
        total_duration += duration
        total_rows += rows
        all_success = all_success and success
        await db.aclose()
    
    results.append({
        'test': "Multiple Small Writes",
        'rows': total_rows,
        'duration': total_duration, 
        'rows_per_sec': total_rows / total_duration if total_duration > 0 else 0,
        'success': all_success
    })
    
    # Print summary
    print("\n" + "=" * 60)
    print("📊 PERFORMANCE SUMMARY")
    print("=" * 60)
    print(f"{'Test':<25} {'Rows':<8} {'Duration':<10} {'Rows/Sec':<10} {'Status'}")
    print("-" * 60)
    
    for result in results:
        status = "✅" if result['success'] else "❌"
        print(f"{result['test']:<25} {result['rows']:<8} {result['duration']:<10.3f} {result['rows_per_sec']:<10.0f} {status}")
    
    # Analysis
    print("\n🔍 ANALYSIS:")
    
    # Find fastest and slowest
    successful_results = [r for r in results if r['success']]
    if successful_results:
        fastest = max(successful_results, key=lambda x: x['rows_per_sec'])
        slowest = min(successful_results, key=lambda x: x['rows_per_sec'])
        
        print(f"🚀 Fastest: {fastest['test']} ({fastest['rows_per_sec']:.0f} rows/s)")
        print(f"🐌 Slowest: {slowest['test']} ({slowest['rows_per_sec']:.0f} rows/s)")
        
        speed_ratio = fastest['rows_per_sec'] / slowest['rows_per_sec'] if slowest['rows_per_sec'] > 0 else 0
        print(f"⚡ Performance variance: {speed_ratio:.1f}x difference")
        
        # Check for patterns
        individual_avg = sum(r['rows_per_sec'] for r in successful_results if 'Individual' in r['test']) / len([r for r in successful_results if 'Individual' in r['test']])
        pooled_avg = sum(r['rows_per_sec'] for r in successful_results if 'Pooled' in r['test']) / len([r for r in successful_results if 'Pooled' in r['test']])
        
        print(f"📈 Individual connections avg: {individual_avg:.0f} rows/s")
        print(f"📈 Pooled connections avg: {pooled_avg:.0f} rows/s")
        print(f"📊 Connection pooling improvement: {(pooled_avg/individual_avg-1)*100:+.1f}%")

if __name__ == "__main__":
    asyncio.run(test_connection_scenarios())