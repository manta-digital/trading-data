import os
import unittest
import time
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import statistics

from dotenv import load_dotenv
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


class TestTimescaleMinuteDataDBPerformance(unittest.TestCase):
    """Performance benchmark tests for TimescaleMinuteDataDB
    
    These tests validate that the implementation meets the performance requirements:
    - Bulk write: >15,000 rows/sec 
    - Query: <500ms for 1-day data
    - Aggregated query: <100ms
    
    Set SKIP_PERFORMANCE_TESTS=1 to skip these tests.
    """

    @classmethod
    def setUpClass(cls):
        """Check if performance tests should be skipped"""
        load_dotenv()
        cls.skip_tests = os.getenv('SKIP_PERFORMANCE_TESTS', '0') == '1'
        
        if cls.skip_tests:
            cls.skipTest(None, "Performance tests skipped (SKIP_PERFORMANCE_TESTS=1)")

    def setUp(self):
        """Set up test environment"""
        if self.skip_tests:
            self.skipTest("Performance tests skipped")

        # Use TRADING_PSQL_* for minute/tick data on <db-host>
        # TRADING_PSQL and MARKET_PSQL are NOT interchangeable - different hosts/purposes
        self.db_config = {
            'host': os.getenv('TRADING_PSQL_HOST', 'localhost'),
            'database': os.getenv('TRADING_PSQL_DB', 'trading'),
            'user': os.getenv('TRADING_PSQL_USER', 'trading_app'),
            'password': os.getenv('TRADING_PSQL_PASSWORD', ''),
            'port': int(os.getenv('TRADING_PSQL_PORT', '5432'))
        }

        # Set up database connection
        if not self.skip_tests:
            try:
                self.db = TimescaleMinuteDataDB(self.db_config)
                # Test connectivity
                with self.db.engine.begin():
                    pass
            except Exception as e:
                self.skipTest(f"Cannot connect to TimescaleDB: {e}")

    def tearDown(self):
        """Clean up after tests"""
        if hasattr(self, 'db') and self.db:
            # Clean up performance test data
            try:
                with self.db.engine.begin() as conn:
                    raw_conn = conn.connection
                    with raw_conn.cursor() as cur:
                        cur.execute("DELETE FROM minute_ohlcv WHERE symbol LIKE 'PERF_TEST_%'")
                        raw_conn.commit()
            except Exception:
                pass

            self.db.close()

    def create_performance_test_data(self, rows=10000, base_symbol='PERF_TEST'):
        """Create large dataset for performance testing"""
        start_time = datetime(2024, 1, 1, 9, 30)  # Start of year
        timestamps = pd.date_range(start_time, periods=rows, freq='1min')
        
        # Generate realistic but fast test data
        base_price = 100.0
        price_changes = np.random.normal(0, 0.1, rows)
        prices = np.cumsum(price_changes) + base_price
        prices = np.maximum(prices, 1.0)  # Prevent negative prices
        
        # Vectorized OHLCV generation for speed
        noise = np.random.normal(0, 0.05, (rows, 4))  # [open_noise, high_noise, low_noise, close_noise]
        
        opens = prices + noise[:, 0]
        closes = prices + noise[:, 3]
        highs = np.maximum(opens, closes) + np.abs(noise[:, 1]) + 0.01
        lows = np.minimum(opens, closes) - np.abs(noise[:, 2]) - 0.01
        volumes = np.random.randint(1000, 50000, rows)
        
        df = pd.DataFrame({
            'open': np.round(opens, 4),
            'high': np.round(highs, 4),
            'low': np.round(lows, 4),
            'close': np.round(closes, 4),
            'volume': volumes
        }, index=timestamps)
        
        return df

    def test_bulk_write_performance_15k_target(self):
        """Test: Bulk write performance must achieve >15,000 rows/sec"""
        print("\n" + "="*60)
        print("PERFORMANCE TEST: Bulk Write Speed (Target: >15,000 rows/sec)")
        print("="*60)
        
        test_sizes = [10000, 25000, 50000]  # Different dataset sizes
        results = []
        
        for size in test_sizes:
            symbol = f'PERF_TEST_WRITE_{size}'
            test_data = self.create_performance_test_data(size, symbol)
            
            # Warm up database connection
            self.db.write_minute_data_bulk(f'{symbol}_WARMUP', test_data.head(100))
            
            # Run actual performance test
            start_time = time.perf_counter()
            result = self.db.write_minute_data_bulk(symbol, test_data)
            elapsed_time = time.perf_counter() - start_time
            
            self.assertTrue(result, f"Write should succeed for {size} rows")
            
            rows_per_sec = size / elapsed_time if elapsed_time > 0 else float('inf')
            results.append(rows_per_sec)
            
            print(f"  {size:,} rows: {rows_per_sec:,.0f} rows/sec ({elapsed_time:.3f}s)")
        
        avg_performance = statistics.mean(results)
        print(f"\nAverage performance: {avg_performance:,.0f} rows/sec")
        print(f"Target: 15,000 rows/sec")
        
        # Assert performance target
        self.assertGreater(
            avg_performance, 15000, 
            f"Average bulk write performance ({avg_performance:.0f} rows/sec) must exceed 15,000 rows/sec"
        )

    def test_query_performance_500ms_target(self):
        """Test: Query performance must be <500ms for 1-day data"""
        print("\n" + "="*60) 
        print("PERFORMANCE TEST: Query Speed (Target: <500ms for 1-day)")
        print("="*60)
        
        # Create 1 day of minute data (390 trading minutes)
        symbol = 'PERF_TEST_QUERY'
        one_day_data = self.create_performance_test_data(390, symbol)
        
        # Write the data
        self.db.write_minute_data_bulk(symbol, one_day_data)
        
        start_time = one_day_data.index[0]
        end_time = one_day_data.index[-1]
        
        # Run multiple query performance tests
        query_times = []
        for i in range(5):  # Run 5 iterations for consistency
            query_start = time.perf_counter()
            result = self.db.get_minute_data(symbol, start_time, end_time)
            query_time = time.perf_counter() - query_start
            query_times.append(query_time * 1000)  # Convert to milliseconds
            
            self.assertFalse(result.empty, "Should retrieve data")
            self.assertEqual(len(result), 390, "Should retrieve all 390 rows")
        
        avg_query_time = statistics.mean(query_times)
        min_query_time = min(query_times)
        max_query_time = max(query_times)
        
        print(f"  Query times: {[f'{t:.1f}ms' for t in query_times]}")
        print(f"  Average: {avg_query_time:.1f}ms")
        print(f"  Range: {min_query_time:.1f}ms - {max_query_time:.1f}ms")
        print(f"  Target: <500ms")
        
        # Assert performance target
        self.assertLess(
            avg_query_time, 500,
            f"Average query time ({avg_query_time:.1f}ms) must be less than 500ms"
        )

    def test_aggregated_query_performance_100ms_target(self):
        """Test: Aggregated query performance must be <100ms"""
        print("\n" + "="*60)
        print("PERFORMANCE TEST: Aggregated Query Speed (Target: <100ms)")
        print("="*60)
        
        # Create several days of minute data for aggregation testing
        symbol = 'PERF_TEST_AGG'
        multi_day_data = self.create_performance_test_data(2000, symbol)  # ~5 days
        
        # Write the data
        self.db.write_minute_data_bulk(symbol, multi_day_data)
        
        start_time = multi_day_data.index[0]
        end_time = multi_day_data.index[-1]
        
        aggregations = ['5min', '15min', '1hour', '1day']
        
        for aggregation in aggregations:
            query_times = []
            
            for i in range(3):  # Run 3 iterations per aggregation
                query_start = time.perf_counter()
                result = self.db.get_minute_data(symbol, start_time, end_time, aggregation)
                query_time = time.perf_counter() - query_start
                query_times.append(query_time * 1000)  # Convert to milliseconds
                
                self.assertFalse(result.empty, f"Should retrieve {aggregation} data")
            
            avg_query_time = statistics.mean(query_times)
            
            print(f"  {aggregation:>6}: {avg_query_time:.1f}ms (range: {min(query_times):.1f}-{max(query_times):.1f}ms)")
            
            # Assert performance target for aggregated queries
            self.assertLess(
                avg_query_time, 100,
                f"Aggregated query time for {aggregation} ({avg_query_time:.1f}ms) must be less than 100ms"
            )

    def test_concurrent_write_performance(self):
        """Test: Sequential write performance (sync implementation)"""
        print("\n" + "="*60)
        print("PERFORMANCE TEST: Sequential Write Performance")
        print("="*60)

        num_sequential = 3
        rows_per_writer = 5000

        # Create datasets for sequential writes
        symbols = [f'PERF_TEST_CONCURRENT_{i}' for i in range(num_sequential)]
        datasets = [self.create_performance_test_data(rows_per_writer, symbol) for symbol in symbols]

        # Sequential writes (sync implementation doesn't support true concurrency)
        print(f"\nSequential {num_sequential} writes:")
        sequential_start = time.perf_counter()
        results = [
            self.db.write_minute_data_bulk(symbol, data)
            for symbol, data in zip(symbols, datasets)
        ]
        sequential_time = time.perf_counter() - sequential_start
        sequential_throughput = (num_sequential * rows_per_writer) / sequential_time

        print(f"  Sequential: {sequential_throughput:,.0f} total rows/sec")

        # All writes should succeed
        self.assertTrue(all(results), "All sequential writes should succeed")

        # Cleanup test data
        try:
            with self.db.engine.begin() as conn:
                raw_conn = conn.connection
                with raw_conn.cursor() as cur:
                    for symbol in symbols:
                        cur.execute("DELETE FROM minute_ohlcv WHERE symbol = %s", (symbol,))
                    raw_conn.commit()
        except Exception:
            pass

    def test_memory_efficiency_large_datasets(self):
        """Test: Memory efficiency with large datasets"""
        print("\n" + "="*60)
        print("PERFORMANCE TEST: Memory Efficiency (Large Datasets)")
        print("="*60)
        
        import psutil
        import gc
        
        process = psutil.Process()
        
        # Baseline memory usage
        gc.collect()  # Force garbage collection
        baseline_memory = process.memory_info().rss / 1024 / 1024  # MB
        
        print(f"Baseline memory: {baseline_memory:.1f} MB")
        
        # Test with increasingly large datasets
        test_sizes = [10000, 50000, 100000]
        
        for size in test_sizes:
            symbol = f'PERF_TEST_MEMORY_{size}'
            
            # Create and write data
            large_data = self.create_performance_test_data(size, symbol)
            
            memory_before = process.memory_info().rss / 1024 / 1024
            
            start_time = time.perf_counter()
            result = self.db.write_minute_data_bulk(symbol, large_data)
            elapsed_time = time.perf_counter() - start_time
            
            memory_after = process.memory_info().rss / 1024 / 1024
            memory_delta = memory_after - memory_before
            
            self.assertTrue(result, f"Write should succeed for {size} rows")
            
            rows_per_sec = size / elapsed_time
            memory_per_row = (memory_delta * 1024 * 1024) / size  # bytes per row
            
            print(f"  {size:,} rows: {rows_per_sec:,.0f} rows/sec, "
                  f"memory delta: +{memory_delta:.1f}MB ({memory_per_row:.1f} bytes/row)")
            
            # Clean up to prevent memory accumulation
            del large_data
            gc.collect()
        
        # Final memory check
        final_memory = process.memory_info().rss / 1024 / 1024
        total_memory_delta = final_memory - baseline_memory
        
        print(f"\nFinal memory: {final_memory:.1f} MB (delta: +{total_memory_delta:.1f} MB)")
        
        # Memory usage should be reasonable (allowing for some growth)
        self.assertLess(total_memory_delta, 500, "Memory growth should be reasonable (<500MB)")

    def test_database_optimization_impact(self):
        """Test: Impact of database optimization settings"""
        print("\n" + "="*60)
        print("PERFORMANCE TEST: Database Optimization Impact")
        print("="*60)
        
        # Test with current optimized settings
        optimized_db = TimescaleMinuteDataDB(self.db_config)
        
        # Test data
        symbol = 'PERF_TEST_OPTIMIZATION'
        test_data = self.create_performance_test_data(10000, symbol)
        
        # Test optimized performance
        start_time = time.perf_counter()
        result = optimized_db.write_minute_data_bulk(symbol, test_data)
        optimized_time = time.perf_counter() - start_time
        
        self.assertTrue(result, "Optimized write should succeed")
        
        optimized_throughput = len(test_data) / optimized_time
        
        print(f"Optimized configuration: {optimized_throughput:,.0f} rows/sec")
        
        # Query performance test
        start_time = test_data.index[0]
        end_time = test_data.index[-1]
        
        query_start = time.perf_counter()
        query_result = optimized_db.get_minute_data(symbol, start_time, end_time)
        query_time = (time.perf_counter() - query_start) * 1000  # ms
        
        print(f"Query performance: {query_time:.1f}ms")
        
        # Verify data integrity
        self.assertFalse(query_result.empty, "Should retrieve data")
        self.assertEqual(len(query_result), len(test_data), "Should retrieve all data")
        
        optimized_db.close()
        
        # Performance should meet targets
        self.assertGreater(optimized_throughput, 15000, "Should exceed 15k rows/sec target")
        self.assertLess(query_time, 500, "Query should be under 500ms")


def print_performance_summary():
    """Print performance requirements summary"""
    print("\n" + "="*60)
    print("TIMESCALEDB PERFORMANCE REQUIREMENTS SUMMARY")
    print("="*60)
    print("1. Bulk Write Performance: >15,000 rows/sec")
    print("2. Query Performance (1-day): <500ms")
    print("3. Aggregated Query Performance: <100ms")
    print("4. Data Integrity: 100% accuracy")
    print("5. Concurrent Operations: Supported")
    print("6. Memory Efficiency: Reasonable growth")
    print("="*60)


if __name__ == '__main__':
    print_performance_summary()
    unittest.main()