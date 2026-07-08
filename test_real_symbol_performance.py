#!/usr/bin/env python3
"""
Real Symbol Database Performance Test

Tests database write performance with existing symbols to identify
what causes the inconsistent performance (sometimes fast, sometimes slow).
"""

import asyncio
import os
import sys
import time
from dotenv import load_dotenv

# Add the trading directory to Python path
sys.path.append('/Users/manta/source/repos/manta/trading')

from manta_trading.market.marketdb import MarketDB
from manta_trading.api.alphavantage.alphavantageapi import AlphavantageAPI

async def test_connection_warmup_effect():
    """Test if connection warmup affects performance"""
    print("🔥 CONNECTION WARMUP PERFORMANCE TEST")
    print("=" * 50)
    
    # Load environment
    load_dotenv()
    
    # Database configuration
    db_config = {
        '_dbname': os.getenv('MARKET_PSQL_DB'),
        '_user': os.getenv('MARKET_PSQL_USER'), 
        '_password': os.getenv('MARKET_PSQL_PASSWORD'),
        '_host': os.getenv('MARKET_PSQL_HOST')
    }
    
    # API configuration  
    api_key = os.getenv('ALPHAVANTAGE_API_KEY')
    api = AlphavantageAPI(apiKey=api_key)
    
    print(f"Database: {db_config['_dbname']}@{db_config['_host']}")
    print(f"Testing with real API data to measure actual performance patterns")
    
    # Test symbols
    test_symbols = ['GOOGL', 'META', 'AMZN']  # Large stocks likely to have full datasets
    
    results = []
    
    for i, symbol in enumerate(test_symbols):
        print(f"\n🧪 TEST {i+1}: {symbol} (fresh connection)")
        
        # Create fresh database connection for each test
        db = MarketDB(**db_config)
        
        try:
            # Time the connection establishment
            conn_start = time.time()
            success = db.connect()
            conn_time = time.time() - conn_start
            
            if not success:
                print(f"❌ Connection failed for {symbol}")
                continue
                
            print(f"🔌 Connection time: {conn_time:.3f}s")
            
            # Get fresh data from API
            print(f"📡 Fetching {symbol} data from API...")
            api_start = time.time()
            data = await api.getDailyOHLCV(symbol, 'full')
            api_time = time.time() - api_start
            
            if data is None or data.empty:
                print(f"❌ No data received for {symbol}")
                continue
                
            print(f"📊 API fetch: {api_time:.3f}s ({len(data)} rows)")
            
            # Test database write performance
            write_start = time.time()
            write_success = db.writeDailyOHLCVAdjusted(symbol, data)
            write_time = time.time() - write_start
            
            rows_per_sec = len(data) / write_time if write_time > 0 else 0
            
            if write_success:
                print(f"✅ DB write: {write_time:.3f}s ({len(data)} rows, {rows_per_sec:.0f} rows/s)")
                print(f"📈 Total time: {conn_time + api_time + write_time:.3f}s")
            else:
                print(f"❌ DB write failed: {write_time:.3f}s")
                
            results.append({
                'symbol': symbol,
                'conn_time': conn_time,
                'api_time': api_time, 
                'write_time': write_time,
                'rows': len(data) if data is not None else 0,
                'rows_per_sec': rows_per_sec,
                'success': write_success
            })
            
        except Exception as e:
            print(f"❌ Error testing {symbol}: {str(e)}")
            
        finally:
            # Close connection
            await db.aclose()
            
        # Add delay between tests to ensure fresh connections
        if i < len(test_symbols) - 1:
            print("⏳ Waiting 5s for clean connection state...")
            await asyncio.sleep(5)
    
    # Test connection reuse (warm connections)
    print(f"\n🔥 CONNECTION REUSE TEST (warm connections)")
    print("=" * 50)
    
    db = MarketDB(**db_config)
    with db:  # Use context manager for connection reuse
        for i, symbol in enumerate(['NFLX', 'NVDA']):  # Different symbols to avoid cache effects
            print(f"\n🧪 WARM TEST {i+1}: {symbol} (reused connection)")
            
            try:
                # Get fresh data from API (using different outputSize to get different data)
                api_start = time.time()
                data = await api.getDailyOHLCV(symbol, 'compact')
                api_time = time.time() - api_start
                
                if data is None or data.empty:
                    print(f"❌ No data received for {symbol}")
                    continue
                    
                print(f"📊 API fetch: {api_time:.3f}s ({len(data)} rows)")
                
                # Test database write performance with warm connection
                write_start = time.time()
                write_success = db.writeDailyOHLCVAdjusted(symbol, data)
                write_time = time.time() - write_start
                
                rows_per_sec = len(data) / write_time if write_time > 0 else 0
                
                if write_success:
                    print(f"✅ DB write (warm): {write_time:.3f}s ({len(data)} rows, {rows_per_sec:.0f} rows/s)")
                else:
                    print(f"❌ DB write failed: {write_time:.3f}s")
                    
                results.append({
                    'symbol': f"{symbol}_warm",
                    'conn_time': 0,  # Connection already established
                    'api_time': api_time,
                    'write_time': write_time, 
                    'rows': len(data),
                    'rows_per_sec': rows_per_sec,
                    'success': write_success
                })
                
            except Exception as e:
                print(f"❌ Error testing {symbol}: {str(e)}")
    
    await db.aclose()
    
    # Print comprehensive analysis
    print(f"\n📊 PERFORMANCE ANALYSIS")
    print("=" * 60)
    print(f"{'Symbol':<12} {'Conn(s)':<8} {'API(s)':<8} {'Write(s)':<10} {'Rows':<8} {'R/s':<8} {'Status'}")
    print("-" * 60)
    
    cold_writes = []
    warm_writes = []
    
    for r in results:
        if not r['success']:
            continue
            
        status = "✅" if r['success'] else "❌"
        print(f"{r['symbol']:<12} {r['conn_time']:<8.3f} {r['api_time']:<8.3f} {r['write_time']:<10.3f} {r['rows']:<8} {r['rows_per_sec']:<8.0f} {status}")
        
        if '_warm' in r['symbol']:
            warm_writes.append(r['rows_per_sec'])
        else:
            cold_writes.append(r['rows_per_sec'])
    
    print(f"\n🔍 PERFORMANCE INSIGHTS:")
    
    if cold_writes:
        cold_avg = sum(cold_writes) / len(cold_writes)
        print(f"🥶 Cold connection average: {cold_avg:.0f} rows/s")
        print(f"🥶 Cold connection range: {min(cold_writes):.0f} - {max(cold_writes):.0f} rows/s")
    
    if warm_writes:
        warm_avg = sum(warm_writes) / len(warm_writes)
        print(f"🔥 Warm connection average: {warm_avg:.0f} rows/s")
        print(f"🔥 Warm connection range: {min(warm_writes):.0f} - {max(warm_writes):.0f} rows/s")
    
    if cold_writes and warm_writes:
        improvement = (warm_avg / cold_avg - 1) * 100
        print(f"📈 Warm vs Cold improvement: {improvement:+.1f}%")
    
    # Check for performance consistency
    all_writes = cold_writes + warm_writes
    if len(all_writes) > 1:
        variance = max(all_writes) / min(all_writes) if min(all_writes) > 0 else 0
        print(f"📊 Performance variance: {variance:.1f}x difference between best and worst")
        
        if variance > 2.0:
            print(f"⚠️  HIGH VARIANCE detected - this explains the inconsistent performance!")
        else:
            print(f"✅ Performance is relatively consistent")

if __name__ == "__main__":
    asyncio.run(test_connection_warmup_effect())