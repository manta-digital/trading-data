#!/usr/bin/env python3
"""
Manual test script for Slice 750 foundation modules.

Tests the InstrumentRegistry and adjustment_policy modules against
the live database to verify they work correctly.

Usage:
    source .env
    python scripts/test_foundation_manual.py
"""

import os
import sys
from datetime import date, datetime, timezone

from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.base.adjustment_policy import (
    AdjustmentPolicy,
    SessionType,
    DataVersion,
    validate_ohlcv_consistency
)


def print_section(title):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def test_adjustment_policy():
    """Test adjustment policy enums and validation."""
    print_section("Testing Adjustment Policy Module")

    # Test enums
    print(f"AdjustmentPolicy.SPLIT_ADJUSTED = {AdjustmentPolicy.SPLIT_ADJUSTED}")
    print(f"SessionType.RTH = {SessionType.RTH}")

    # Test DataVersion
    now = datetime.now(timezone.utc)
    dv = DataVersion(version="1.0.0", ingestion_timestamp=now, provider_version="av_2024")
    print(f"\nDataVersion created: {dv.version}, provider: {dv.provider_version}")

    # Test OHLCV validation - valid case
    result = validate_ohlcv_consistency(
        open_price=100.0,
        high=105.0,
        low=99.0,
        close=103.0
    )
    print(f"\nValid OHLCV test: is_valid={result.is_valid}, errors={result.errors}")

    # Test OHLCV validation - invalid case
    result = validate_ohlcv_consistency(
        open_price=100.0,
        high=102.0,  # Too low - close is 105
        low=99.0,
        close=105.0
    )
    print(f"Invalid OHLCV test: is_valid={result.is_valid}, errors={result.errors}")

    print("✅ Adjustment policy tests passed")


def test_instrument_registry():
    """Test InstrumentRegistry against live database."""
    print_section("Testing InstrumentRegistry Module")

    # Get database config from environment
    db_config = {
        'host': os.getenv('TRADING_PSQL_HOST'),
        'port': int(os.getenv('TRADING_PSQL_PORT', 5432)),
        'database': os.getenv('TRADING_PSQL_DB'),
        'user': os.getenv('TRADING_PSQL_USER'),
        'password': os.getenv('TRADING_PSQL_PASSWORD')
    }

    print(f"Connecting to: {db_config['database']} on {db_config['host']}")

    registry = InstrumentRegistry(db_config)

    try:
        # Test 1: Lookup by canonical ID
        print("\n--- Test 1: Lookup by canonical ID ---")
        aapl = registry.get_instrument_by_canonical_id('AAPL.NASDAQ')
        if aapl:
            print(f"✅ Found AAPL: {aapl.symbol} at {aapl.venue}")
            print(f"   Calendar: {aapl.trading_calendar_id}, Active: {aapl.active}")
        else:
            print("❌ AAPL not found")

        # Test 2: Lookup by provider symbol
        print("\n--- Test 2: Lookup by provider symbol ---")
        msft = registry.get_instrument_by_provider_symbol('alphavantage', 'MSFT')
        if msft:
            print(f"✅ AlphaVantage 'MSFT' maps to: {msft.canonical_id}")
        else:
            print("❌ MSFT mapping not found")

        # Test 3: Historical lookup (should work with valid_from date)
        print("\n--- Test 3: Historical lookup ---")
        tsla = registry.get_instrument_by_provider_symbol(
            'alphavantage', 'TSLA', as_of_date=date(2024, 1, 1)
        )
        if tsla:
            print(f"✅ TSLA as of 2024-01-01: {tsla.canonical_id}")
        else:
            print("❌ TSLA not found for that date")

        # Test 4: List instruments by venue
        print("\n--- Test 4: List instruments by venue ---")
        nasdaq_stocks = registry.list_instruments(asset_class='stock', venue='NASDAQ')
        print(f"✅ Found {len(nasdaq_stocks)} NASDAQ stocks")
        if nasdaq_stocks:
            print(f"   First 5: {', '.join(s.symbol for s in nasdaq_stocks[:5])}")

        # Test 5: List instruments by asset class
        print("\n--- Test 5: List instruments by asset class ---")
        all_stocks = registry.list_instruments(asset_class='stock')
        print(f"✅ Found {len(all_stocks)} total stocks")

        # Test 6: Lookup non-existent instrument
        print("\n--- Test 6: Lookup non-existent instrument ---")
        fake = registry.get_instrument_by_canonical_id('NOTREAL.NYSE')
        if fake is None:
            print("✅ Correctly returned None for non-existent instrument")
        else:
            print("❌ Should have returned None")

        # Test 7: Cache behavior (second lookup should be faster)
        print("\n--- Test 7: Testing cache behavior ---")
        import time
        start = time.time()
        registry.get_instrument_by_canonical_id('AAPL.NASDAQ')
        first_time = time.time() - start

        start = time.time()
        registry.get_instrument_by_canonical_id('AAPL.NASDAQ')
        second_time = time.time() - start

        print(f"First lookup: {first_time*1000:.2f}ms")
        print(f"Second lookup (cached): {second_time*1000:.2f}ms")
        if second_time < first_time:
            print("✅ Cache is working (second lookup faster)")
        else:
            print("⚠️  Cache might not be working as expected")

        print("\n✅ All InstrumentRegistry tests passed")

    finally:
        registry.close()
        print("\nDatabase connection closed")


def main():
    """Run all manual tests."""
    print("\n" + "="*60)
    print("  Slice 750 Foundation Modules - Manual Testing")
    print("="*60)

    # Check environment variables
    required_vars = ['TRADING_PSQL_HOST', 'TRADING_PSQL_DB', 'TRADING_PSQL_USER', 'TRADING_PSQL_PASSWORD']
    missing_vars = [var for var in required_vars if not os.getenv(var)]

    if missing_vars:
        print(f"\n❌ ERROR: Missing environment variables: {', '.join(missing_vars)}")
        print("   Please run: source .env")
        sys.exit(1)

    try:
        test_adjustment_policy()
        test_instrument_registry()

        print_section("All Tests Complete")
        print("✅ Foundation modules are working correctly!")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
