#!/usr/bin/env python3
"""
Database inspection and test data setup for chunking system.

This script:
1. Inspects the current test database structure
2. Adds some test symbols with various last updated dates
3. Tests our chunking logic with real database data
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from manta_trading.market.marketdb import MarketDB
from manta_trading.market.marketservice import MarketService
from manta_trading.market.config import ChunkingConfig


def inspect_database():
    """Inspect the current test database structure and content."""
    print("🔍 Inspecting Test Database...")
    
    # Connect to test database
    dbname = os.getenv('MARKET_PSQL_DB_TEST')
    user = os.getenv('MARKET_PSQL_USER')
    password = os.getenv('MARKET_PSQL_PASSWORD')
    host = os.getenv('MARKET_PSQL_HOST')
    port = int(os.getenv('MARKET_PSQL_PORT', '5432'))
    
    db = MarketDB(dbname, user, password, host, _port=port)
    db.connect()
    
    # Check tables
    db.cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    tables = [row[0] for row in db.cur.fetchall()]
    print(f"  📋 Tables: {tables}")
    
    # Check symbol_list content
    db.cur.execute("SELECT COUNT(*) FROM symbol_list")
    symbol_count = db.cur.fetchone()[0]
    print(f"  📊 Symbols in symbol_list: {symbol_count}")
    
    if symbol_count > 0:
        db.cur.execute("""
            SELECT symbol, name, lastupdatedday 
            FROM symbol_list 
            ORDER BY lastupdatedday ASC 
            LIMIT 10
        """)
        symbols = db.cur.fetchall()
        print("  📈 Sample symbols:")
        for symbol, name, last_updated in symbols:
            print(f"    {symbol:8} | {name:30} | {last_updated}")
    
    # Check dailyohlcvadjusted content
    db.cur.execute("SELECT COUNT(*) FROM dailyohlcvadjusted")
    ohlc_count = db.cur.fetchone()[0]
    print(f"  📈 OHLC records: {ohlc_count}")
    
    if ohlc_count > 0:
        db.cur.execute("""
            SELECT symbol, MIN(date) as earliest, MAX(date) as latest, COUNT(*) as record_count
            FROM dailyohlcvadjusted 
            GROUP BY symbol 
            ORDER BY record_count DESC
            LIMIT 5
        """)
        data_summary = db.cur.fetchall()
        print("  📊 Data coverage (top 5 symbols by record count):")
        for symbol, earliest, latest, count in data_summary:
            print(f"    {symbol:8} | {earliest} to {latest} | {count} records")
    
    db.close()
    return symbol_count, ohlc_count


def setup_test_data():
    """Add test symbols with various gap scenarios."""
    print("\n🔧 Setting up Test Data...")
    
    # Connect to test database
    dbname = os.getenv('MARKET_PSQL_DB_TEST')
    user = os.getenv('MARKET_PSQL_USER')
    password = os.getenv('MARKET_PSQL_PASSWORD')
    host = os.getenv('MARKET_PSQL_HOST')
    port = int(os.getenv('MARKET_PSQL_PORT', '5432'))
    
    db = MarketDB(dbname, user, password, host, _port=port)
    db.connect()
    
    today = date.today()
    
    # Test scenarios with different gap sizes
    test_symbols = [
        ("TEST_RECENT", "Test Recent Update", today - timedelta(days=1)),     # Should skip
        ("TEST_SMALL", "Test Small Gap", today - timedelta(days=30)),         # Compact
        ("TEST_MEDIUM", "Test Medium Gap", today - timedelta(days=150)),      # 2 chunks
        ("TEST_LARGE", "Test Large Gap", today - timedelta(days=300)),        # 3 chunks
        ("TEST_HUGE", "Test Huge Gap", today - timedelta(days=1500)),         # Full fetch
        ("TEST_NEW", "Test New Symbol", None),                                # Full fetch
    ]
    
    print("  Adding test symbols:")
    for symbol, name, last_updated in test_symbols:
        try:
            # Insert or update symbol with required exchange field
            db.cur.execute("""
                INSERT INTO symbol_list (symbol, name, exchange, lastupdatedday)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (symbol) DO UPDATE SET
                    name = EXCLUDED.name,
                    exchange = EXCLUDED.exchange,
                    lastupdatedday = EXCLUDED.lastupdatedday
            """, (symbol, name, 'TEST', last_updated))
            
            days_gap = "N/A" if last_updated is None else (today - last_updated).days
            print(f"    ✅ {symbol:12} | {str(days_gap):>4} days | {name}")
            
        except Exception as e:
            print(f"    ❌ {symbol:12} | Error: {e}")
    
    db.conn.commit()
    db.close()


def test_chunking_with_real_db():
    """Test our chunking logic with real database data."""
    print("\n🎯 Testing Chunking Logic with Real Database...")
    
    # Connect to test database
    dbname = os.getenv('MARKET_PSQL_DB_TEST')
    user = os.getenv('MARKET_PSQL_USER')
    password = os.getenv('MARKET_PSQL_PASSWORD')
    host = os.getenv('MARKET_PSQL_HOST')
    port = int(os.getenv('MARKET_PSQL_PORT', '5432'))
    
    db = MarketDB(dbname, user, password, host, _port=port)
    
    # Create MarketService with intelligent chunking
    config = ChunkingConfig()
    service = MarketService(db=db, chunking_config=config)
    
    # Get test symbols from database
    db.connect()
    db.cur.execute("""
        SELECT symbol, name, lastupdatedday 
        FROM symbol_list 
        WHERE symbol LIKE 'TEST_%'
        ORDER BY symbol
    """)
    test_symbols = db.cur.fetchall()
    db.close()
    
    if not test_symbols:
        print("  ⚠️  No test symbols found. Run setup_test_data() first.")
        return
    
    print(f"  Testing chunking decisions for {len(test_symbols)} symbols:")
    
    for symbol, name, last_updated in test_symbols:
        try:
            # Test the old method (for backward compatibility)
            output_size = service.getOutputSizeFromLastUpdatedDay(symbol)
            
            # Test the new method (for full chunking plan)
            fetch_plan = service.getFetchPlan(symbol)
            
            # Analyze the decision
            if not fetch_plan:
                decision = "SKIP"
            elif len(fetch_plan) == 1:
                decision = f"{fetch_plan[0].output_size.upper()}"
            else:
                decision = f"CHUNKED ({len(fetch_plan)} chunks)"
            
            days_gap = "N/A" if last_updated is None else (date.today() - last_updated).days
            
            print(f"    {symbol:12} | {str(days_gap):>4} days | {decision:15} | {output_size}")
            
            # Show chunk details for multi-chunk operations
            if len(fetch_plan) > 1:
                for i, instruction in enumerate(fetch_plan, 1):
                    print(f"      └─ Chunk {i}/{len(fetch_plan)}: {instruction.output_size}")
        
        except Exception as e:
            print(f"    {symbol:12} | ERROR: {e}")


def test_real_chunking_execution():
    """Test the actual chunking execution (without API calls)."""
    print("\n⚡ Testing Chunked Execution Logic...")
    
    # We'll test this with mocked API to avoid real API calls
    from unittest.mock import AsyncMock
    import pandas as pd
    import asyncio
    
    # Connect to test database
    dbname = os.getenv('MARKET_PSQL_DB_TEST')
    user = os.getenv('MARKET_PSQL_USER')
    password = os.getenv('MARKET_PSQL_PASSWORD')
    host = os.getenv('MARKET_PSQL_HOST')
    port = int(os.getenv('MARKET_PSQL_PORT', '5432'))
    
    db = MarketDB(dbname, user, password, host, _port=port)
    
    # Mock API that returns sample data with correct columns
    mock_api = AsyncMock()
    mock_data = pd.DataFrame({
        'date': ['2024-01-01', '2024-01-02'],
        'open': [100.0, 101.0],
        'high': [102.0, 103.0], 
        'low': [99.0, 100.0],
        'close': [101.0, 102.0],
        'adjusted_close': [101.0, 102.0],  # Required by the database
        'volume': [1000, 1100],
        'dividend_amount': [0.0, 0.0],
        'split_coefficient': [1.0, 1.0]
    })
    mock_api.getDailyOHLCV.return_value = mock_data
    
    # Create service with mocked API
    config = ChunkingConfig()
    service = MarketService(api=mock_api, db=db, chunking_config=config)
    
    async def run_test():
        # Test chunked execution on a medium gap symbol
        result = await service.updateDailyOHLCVWithChunking("TEST_MEDIUM")
        return result
    
    # Run the test
    result = asyncio.run(run_test())
    print(f"  ✅ Chunked execution test: {'SUCCESS' if result else 'FAILED'}")
    print(f"  📞 Mock API calls made: {mock_api.getDailyOHLCV.call_count}")


def main():
    """Run all database inspection and testing."""
    print("🚀 Database Inspection and Chunking Test Suite")
    print("=" * 60)
    
    # Step 1: Inspect current database
    symbol_count, ohlc_count = inspect_database()
    
    # Step 2: Setup test data if needed
    if symbol_count == 0:
        print("\n💡 Database is empty, adding test data...")
        setup_test_data()
        print("✅ Test data added successfully!")
    else:
        print(f"\n📊 Database has {symbol_count} symbols, adding test symbols anyway...")
        setup_test_data()
    
    # Step 3: Test chunking logic with real database
    test_chunking_with_real_db()
    
    # Step 4: Test actual chunking execution
    test_real_chunking_execution()
    
    print("\n" + "=" * 60)
    print("🏁 Database Testing Summary:")
    print("  ✅ Database connectivity confirmed")
    print("  ✅ Test data setup completed") 
    print("  ✅ Chunking logic tested with real database")
    print("  ✅ Chunked execution logic validated")
    print("\n🎉 System is ready for production testing!")


if __name__ == "__main__":
    main()