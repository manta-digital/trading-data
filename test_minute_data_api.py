#!/usr/bin/env python3
"""
Test AlphaVantage Minute Data API

Explore the minute data API endpoint, analyze the schema, and plan database design.
"""

import os
import sys
import asyncio
import json
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Add the trading directory to Python path
sys.path.append('/Users/manta/source/repos/manta/trading')

from manta_trading.api.alphavantage.alphavantageapi import AlphavantageAPI

async def test_minute_data_api():
    """Test the AlphaVantage minute data API and analyze the response"""
    print("🕐 ALPHAVANTAGE MINUTE DATA API TEST")
    print("=" * 50)
    
    # Load environment
    load_dotenv()
    
    # Initialize API
    api_key = os.getenv('ALPHAVANTAGE_API_KEY')
    if not api_key:
        print("❌ ALPHAVANTAGE_API_KEY not found in .env")
        return
        
    print(f"🔑 API Key found: {api_key[:10]}...")
    
    # Check if AlphavantageAPI has minute data method
    api = AlphavantageAPI(apiKey=api_key)
    
    # Test different approaches to get minute data
    print("\n🧪 TESTING MINUTE DATA ENDPOINTS")
    print("-" * 30)
    
    # Method 1: Check if there's an existing minute data method
    if hasattr(api, 'getMinuteOHLCV'):
        print("✅ Found existing getMinuteOHLCV method")
        try:
            data = await api.getMinuteOHLCV('TSLA')
            print(f"📊 Minute data shape: {data.shape if data is not None else 'None'}")
        except Exception as e:
            print(f"❌ getMinuteOHLCV failed: {e}")
    else:
        print("❌ No existing getMinuteOHLCV method found")
    
    # Method 2: Check if there's an intraday method
    if hasattr(api, 'getIntradayOHLCV'):
        print("✅ Found existing getIntradayOHLCV method")
        try:
            data = await api.getIntradayOHLCV('TSLA', interval='1min')
            print(f"📊 Intraday data shape: {data.shape if data is not None else 'None'}")
        except Exception as e:
            print(f"❌ getIntradayOHLCV failed: {e}")
    else:
        print("❌ No existing getIntradayOHLCV method found")
    
    # Method 3: Direct API call to test the endpoint
    print("\n🔗 DIRECT API ENDPOINT TEST")
    print("-" * 30)
    
    import aiohttp
    
    # Test TIME_SERIES_INTRADAY endpoint directly
    base_url = "https://www.alphavantage.co/query"
    params = {
        'function': 'TIME_SERIES_INTRADAY',
        'symbol': 'TSLA',
        'interval': '1min',
        'apikey': api_key,
        'outputsize': 'compact',  # Start with compact to avoid hitting rate limits
        'datatype': 'json'
    }
    
    print(f"🌐 Testing URL: {base_url}")
    print(f"📋 Parameters: {params}")
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(base_url, params=params) as response:
                print(f"📡 HTTP Status: {response.status}")
                
                if response.status == 200:
                    data = await response.json()
                    
                    # Save raw response for analysis
                    with open('tsla_minute_data_sample.json', 'w') as f:
                        json.dump(data, f, indent=2, default=str)
                    
                    print("✅ API Response received successfully")
                    print(f"💾 Raw data saved to: tsla_minute_data_sample.json")
                    
                    # Analyze the response structure
                    print("\n📊 RESPONSE ANALYSIS")
                    print("-" * 30)
                    
                    print(f"🔍 Top-level keys: {list(data.keys())}")
                    
                    # Look for metadata
                    metadata_key = None
                    timeseries_key = None
                    
                    for key in data.keys():
                        if 'meta' in key.lower():
                            metadata_key = key
                            print(f"📋 Metadata key found: {key}")
                        elif 'time' in key.lower():
                            timeseries_key = key
                            print(f"⏰ Time series key found: {key}")
                    
                    if metadata_key:
                        metadata = data[metadata_key]
                        print(f"📋 Metadata: {json.dumps(metadata, indent=2, default=str)}")
                    
                    if timeseries_key:
                        timeseries = data[timeseries_key]
                        print(f"📈 Time series entries: {len(timeseries)}")
                        
                        # Analyze first few entries
                        first_entries = dict(list(timeseries.items())[:3])
                        print(f"🔍 Sample entries:")
                        for timestamp, values in first_entries.items():
                            print(f"  {timestamp}: {values}")
                        
                        # Get the latest entry to analyze schema
                        latest_timestamp = list(timeseries.keys())[0]
                        latest_data = timeseries[latest_timestamp]
                        
                        print(f"\n🏗️ DATA SCHEMA ANALYSIS")
                        print("-" * 30)
                        print(f"📅 Latest timestamp: {latest_timestamp}")
                        print(f"📊 Data fields: {list(latest_data.keys())}")
                        print(f"📋 Sample values: {latest_data}")
                        
                        # Compare with daily data schema
                        print(f"\n📊 SCHEMA COMPARISON")
                        print("-" * 30)
                        print("Daily data fields typically include:")
                        print("  - open, high, low, close, adjusted_close")
                        print("  - volume, dividend_amount, split_coefficient")
                        print("")
                        print("Minute data fields found:")
                        for field, value in latest_data.items():
                            print(f"  - {field}: {value} (type: {type(value).__name__})")
                        
                        # Estimate data volume
                        print(f"\n📏 DATA VOLUME ESTIMATION")
                        print("-" * 30)
                        
                        # Calculate rows per day (market hours: ~6.5 hours = 390 minutes)
                        market_minutes_per_day = 390  # 9:30 AM to 4:00 PM EST
                        trading_days_per_year = 252
                        
                        rows_per_day = market_minutes_per_day
                        rows_per_year = rows_per_day * trading_days_per_year
                        
                        print(f"📊 Estimated rows per trading day: {rows_per_day:,}")
                        print(f"📊 Estimated rows per year: {rows_per_year:,}")
                        print(f"📊 Estimated rows per 20 years: {rows_per_year * 20:,}")
                        
                        # For index estimates
                        sp500_symbols = 500
                        russell3000_symbols = 3000
                        
                        print(f"\n📊 INDEX DATA VOLUME ESTIMATES")
                        print("-" * 30)
                        print(f"SP500 (500 symbols):")
                        print(f"  - Per year: {rows_per_year * sp500_symbols:,} rows")
                        print(f"  - Per 20 years: {rows_per_year * 20 * sp500_symbols:,} rows")
                        
                        print(f"Russell 3000 (3000 symbols):")
                        print(f"  - Per year: {rows_per_year * russell3000_symbols:,} rows")  
                        print(f"  - Per 20 years: {rows_per_year * 20 * russell3000_symbols:,} rows")
                        
                        # Storage estimates (assuming ~50 bytes per row)
                        bytes_per_row = 50
                        print(f"\n💾 STORAGE ESTIMATES (assuming {bytes_per_row} bytes/row)")
                        print("-" * 30)
                        sp500_20yr_gb = (rows_per_year * 20 * sp500_symbols * bytes_per_row) / (1024**3)
                        russell_20yr_gb = (rows_per_year * 20 * russell3000_symbols * bytes_per_row) / (1024**3)
                        
                        print(f"SP500 (20 years): {sp500_20yr_gb:.1f} GB")
                        print(f"Russell 3000 (20 years): {russell_20yr_gb:.1f} GB")
                        
                else:
                    error_text = await response.text()
                    print(f"❌ API Error: {response.status}")
                    print(f"📄 Response: {error_text}")
                    
        except Exception as e:
            print(f"❌ Request failed: {str(e)}")
    
    print(f"\n✅ MINUTE DATA API TEST COMPLETE")

if __name__ == "__main__":
    asyncio.run(test_minute_data_api())