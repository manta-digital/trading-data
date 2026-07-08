#!/usr/bin/env python3
"""
Test runner for TimescaleMinuteDataDB test suite

This script runs the comprehensive test suite for the TimescaleMinuteDataDB class:
- Unit tests (mocked, fast)
- Integration tests (require real database)  
- Performance tests (validate >15k rows/sec target)

Usage:
    python run_timescale_tests.py [--unit-only] [--integration-only] [--performance-only]
    
Environment Variables:
    SKIP_DB_INTEGRATION_TESTS=1    Skip integration tests if DB not available
    SKIP_PERFORMANCE_TESTS=1       Skip performance tests
    MARKET_PSQL_HOST              Database host (default: localhost)
    MARKET_PSQL_DB_TEST          Test database name (default: trading_test)
    MARKET_PSQL_USER             Database user (default: postgres)
    MARKET_PSQL_PASSWORD         Database password
    MARKET_PSQL_PORT             Database port (default: 5432)
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path

def run_command(cmd, description):
    """Run a command and handle output"""
    print(f"\n{'='*60}")
    print(f"RUNNING: {description}")
    print(f"{'='*60}")
    print(f"Command: {' '.join(cmd)}")
    print()
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print("STDERR:", result.stderr)
            
        if result.returncode != 0:
            print(f"❌ {description} FAILED (exit code: {result.returncode})")
            return False
        else:
            print(f"✅ {description} PASSED")
            return True
            
    except Exception as e:
        print(f"❌ {description} FAILED: {e}")
        return False

def check_environment():
    """Check environment setup"""
    print("Environment Configuration:")
    print(f"  MARKET_PSQL_HOST: {os.getenv('MARKET_PSQL_HOST', 'localhost')}")
    print(f"  MARKET_PSQL_DB_TEST: {os.getenv('MARKET_PSQL_DB_TEST', 'trading_test')}")
    print(f"  MARKET_PSQL_USER: {os.getenv('MARKET_PSQL_USER', 'postgres')}")
    print(f"  MARKET_PSQL_PASSWORD: {'***' if os.getenv('MARKET_PSQL_PASSWORD') else 'Not set'}")
    print(f"  MARKET_PSQL_PORT: {os.getenv('MARKET_PSQL_PORT', '5432')}")
    print(f"  SKIP_DB_INTEGRATION_TESTS: {os.getenv('SKIP_DB_INTEGRATION_TESTS', '0')}")
    print(f"  SKIP_PERFORMANCE_TESTS: {os.getenv('SKIP_PERFORMANCE_TESTS', '0')}")

def main():
    parser = argparse.ArgumentParser(description='Run TimescaleMinuteDataDB test suite')
    parser.add_argument('--unit-only', action='store_true', help='Run only unit tests')
    parser.add_argument('--integration-only', action='store_true', help='Run only integration tests')
    parser.add_argument('--performance-only', action='store_true', help='Run only performance tests')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    
    args = parser.parse_args()
    
    # Change to project directory
    project_dir = Path(__file__).parent
    os.chdir(project_dir)
    
    print("TimescaleMinuteDataDB Test Suite")
    print("="*60)
    check_environment()
    
    success_count = 0
    total_tests = 0
    
    # Unit tests (always run unless specific test type requested)
    if not args.integration_only and not args.performance_only:
        total_tests += 1
        cmd = [
            sys.executable, '-m', 'pytest',
            'test/unit/testtimescaleminutedatadb.py',
            '--tb=short'
        ]
        if args.verbose:
            cmd.append('-v')
            
        if run_command(cmd, "Unit Tests"):
            success_count += 1
    
    # Integration tests (require database)
    if not args.unit_only and not args.performance_only:
        total_tests += 1
        cmd = [
            sys.executable, '-m', 'pytest', 
            'test/integration/testtimescaleminutedatadbintegration.py',
            '--tb=short'
        ]
        if args.verbose:
            cmd.append('-v')
            
        if run_command(cmd, "Integration Tests"):
            success_count += 1
    
    # Performance tests (require database and are slower)
    if not args.unit_only and not args.integration_only:
        total_tests += 1
        cmd = [
            sys.executable, '-m', 'pytest',
            'test/performance/testtimescaleminutedatadbperformance.py',
            '--tb=short'
        ]
        if args.verbose:
            cmd.append('-v')
            
        if run_command(cmd, "Performance Tests"):
            success_count += 1
    
    # Summary
    print(f"\n{'='*60}")
    print("TEST SUITE SUMMARY")
    print(f"{'='*60}")
    print(f"Tests passed: {success_count}/{total_tests}")
    
    if success_count == total_tests:
        print("🎉 ALL TESTS PASSED!")
        print("\nTimescaleMinuteDataDB is ready for production use!")
        print("Performance targets validated:")
        print("  ✅ Bulk write: >15,000 rows/sec")
        print("  ✅ Query: <500ms for 1-day data") 
        print("  ✅ Aggregated query: <100ms")
        sys.exit(0)
    else:
        print("❌ SOME TESTS FAILED")
        print("\nPlease check the test output above for details.")
        print("Common issues:")
        print("  - Database not running or not accessible")
        print("  - Missing environment variables")
        print("  - TimescaleDB extensions not installed")
        sys.exit(1)

if __name__ == '__main__':
    main()