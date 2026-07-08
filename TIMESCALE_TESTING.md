# TimescaleMinuteDataDB Test Suite Documentation

## Overview

This document describes the comprehensive test suite for the `TimescaleMinuteDataDB` class, which provides high-performance data access to TimescaleDB for financial minute OHLCV data.

## Test Structure

The test suite is organized into three levels:

### 1. Unit Tests (`test/unit/testtimescaleminutedatadb.py`)
- **Purpose**: Fast, isolated tests using mocks
- **Runtime**: ~6 seconds
- **Coverage**: 28 test cases covering all methods and error conditions
- **Dependencies**: None (uses mocks for database operations)

### 2. Integration Tests (`test/integration/testtimescaleminutedatadbintegration.py`)
- **Purpose**: End-to-end tests with real TimescaleDB database
- **Runtime**: Variable (depends on database performance)
- **Coverage**: Real database operations, data integrity, concurrent operations
- **Dependencies**: Running TimescaleDB instance with test database

### 3. Performance Tests (`test/performance/testtimescaleminutedatadbperformance.py`)
- **Purpose**: Validate performance targets and benchmark system
- **Runtime**: Variable (can be long for large datasets)
- **Coverage**: Performance validation against requirements
- **Dependencies**: Running TimescaleDB instance optimized for performance

## Performance Requirements Tested

| Requirement | Target | Test Method |
|-------------|--------|-------------|
| Bulk Write Speed | >15,000 rows/sec | `test_bulk_write_performance_15k_target` |
| Query Performance (1-day) | <500ms | `test_query_performance_500ms_target` |
| Aggregated Query | <100ms | `test_aggregated_query_performance_100ms_target` |
| Data Integrity | 100% accuracy | Multiple tests across all suites |
| Concurrent Operations | Supported | `test_concurrent_writes_real_database` |

## Running the Tests

### Quick Start
```bash
# Run all unit tests (fast, no database required)
python run_timescale_tests.py --unit-only

# Run all tests (requires database)
python run_timescale_tests.py

# Run specific test types
python run_timescale_tests.py --integration-only
python run_timescale_tests.py --performance-only
```

### Manual Test Execution
```bash
# Unit tests
python -m pytest test/unit/testtimescaleminutedatadb.py -v

# Integration tests  
python -m pytest test/integration/testtimescaleminutedatadbintegration.py -v

# Performance tests
python -m pytest test/performance/testtimescaleminutedatadbperformance.py -v
```

## Environment Configuration

### Required Environment Variables
```bash
MARKET_PSQL_HOST=localhost          # Database host
MARKET_PSQL_DB_TEST=trading_test    # Test database name  
MARKET_PSQL_USER=postgres           # Database user
MARKET_PSQL_PASSWORD=your_password  # Database password
MARKET_PSQL_PORT=5432              # Database port
```

### Optional Control Variables
```bash
SKIP_DB_INTEGRATION_TESTS=1        # Skip integration tests
SKIP_PERFORMANCE_TESTS=1           # Skip performance tests
```

## Database Setup

### Prerequisites
1. PostgreSQL 12+ with TimescaleDB extension
2. Test database created and configured
3. TimescaleDB hypertables and continuous aggregations deployed

### Schema Setup
```sql
-- Run the schema setup script
\i sql/final_working.sql
```

This creates:
- `minute_ohlcv` hypertable with 4-hour chunks
- Continuous aggregation views for 5min, 15min, 1hour, 4hour, 1day, 1week, 1month
- Optimized indexes for symbol and time queries

## Test Coverage Details

### Unit Test Coverage (28 tests)

#### Connection Management
- ✅ Connection pool initialization with optimized settings
- ✅ Connection acquisition/release through context manager
- ✅ Error handling for connection failures
- ✅ Connection pool parameter validation
- ✅ Connection optimization settings (work_mem, parallel workers, etc.)

#### Bulk Write Operations  
- ✅ Successful bulk write with performance measurement
- ✅ Empty/None data handling
- ✅ Malformed data handling
- ✅ Error handling and recovery
- ✅ Data format consistency for TimescaleDB COPY operation
- ✅ Performance benchmark simulation (>15k rows/sec target)

#### Query Operations
- ✅ Basic minute data queries
- ✅ All aggregation levels (5min, 15min, 1hour, 1day, 1week, 1month)
- ✅ Invalid aggregation handling
- ✅ Error handling and empty result returns
- ✅ Materialized view mapping validation
- ✅ Query performance expectations

#### System Operations
- ✅ Coverage analysis with compression statistics
- ✅ System metrics retrieval (hypertable stats, compression, continuous aggregations)
- ✅ Error handling for system operations

#### Edge Cases & Configuration
- ✅ Database configuration handling with defaults
- ✅ Connection pool closure
- ✅ Performance validation frameworks

### Integration Test Coverage (13 tests)

#### Real Database Operations
- ✅ Database connectivity validation
- ✅ Data integrity through write/read cycles
- ✅ Large dataset handling (50k+ rows)
- ✅ Concurrent write operations
- ✅ Time range edge cases (past, future, subset ranges)

#### Performance Validation
- ✅ Real bulk write performance measurement
- ✅ Query performance against real data
- ✅ Aggregated query performance testing
- ✅ Coverage analysis with real compression data
- ✅ System metrics from real TimescaleDB instance

### Performance Test Coverage (7 tests)

#### Performance Benchmarks
- ✅ Bulk write >15,000 rows/sec validation
- ✅ Query <500ms for 1-day data validation  
- ✅ Aggregated query <100ms validation
- ✅ Concurrent operation scalability
- ✅ Memory efficiency with large datasets
- ✅ Database optimization impact measurement

## Test Data Generation

The test suite includes sophisticated test data generation:

```python
def create_realistic_minute_data(self, rows=1000, symbol='TSLA_TEST'):
    """Generate realistic OHLCV data with:
    - Realistic price movements using random walk
    - Proper OHLC relationships (high >= max(open,close), etc.)
    - Variable volume patterns
    - Timestamp sequences following market hours
    """
```

Features:
- Realistic price movements with controlled volatility
- Proper OHLC mathematical relationships
- Configurable dataset sizes (100 to 100,000+ rows)
- Multiple symbols for concurrent testing
- Vectorized generation for performance

## Error Scenarios Tested

### Connection Errors
- Database unavailable
- Invalid credentials
- Connection timeout
- Connection pool exhaustion

### Data Errors  
- Empty datasets
- Malformed DataFrames
- Invalid data types
- Missing required columns

### Query Errors
- Invalid time ranges
- Non-existent symbols
- Invalid aggregation parameters
- Database query failures

### System Errors
- TimescaleDB extension unavailable
- Insufficient permissions
- Disk space issues (simulated)
- Memory limitations

## Performance Monitoring

The test suite includes comprehensive performance monitoring:

### Metrics Collected
- Write throughput (rows/second)
- Query latency (milliseconds)
- Memory usage during operations
- Concurrent operation efficiency
- Database optimization impact

### Benchmarking Features
- Multiple iterations for statistical accuracy
- Warm-up runs to eliminate cold start effects
- Different dataset sizes for scalability testing
- Memory efficiency monitoring with psutil
- Detailed performance reporting

## Continuous Integration

### GitHub Actions Integration
```yaml
# Add to .github/workflows/tests.yml
- name: Run TimescaleDB Unit Tests
  run: python run_timescale_tests.py --unit-only

- name: Run TimescaleDB Integration Tests  
  run: python run_timescale_tests.py --integration-only
  env:
    MARKET_PSQL_HOST: localhost
    MARKET_PSQL_DB_TEST: trading_test
    MARKET_PSQL_USER: postgres
    MARKET_PSQL_PASSWORD: postgres
```

### Pre-commit Integration
```yaml
# Add to .pre-commit-config.yaml
- repo: local
  hooks:
    - id: timescale-unit-tests
      name: TimescaleDB Unit Tests
      entry: python run_timescale_tests.py --unit-only
      language: python
      pass_filenames: false
```

## Troubleshooting

### Common Issues

#### "Cannot connect to TimescaleDB"
- Check database is running: `pg_ctl status`
- Verify connection parameters in environment variables
- Test connection: `psql -h localhost -U postgres -d trading_test`

#### "TimescaleDB extension not found"
- Install TimescaleDB extension: `CREATE EXTENSION timescaledb;`
- Run schema setup: `\i sql/final_working.sql`

#### Performance tests failing
- Check database configuration (work_mem, shared_buffers, etc.)
- Verify TimescaleDB compression is enabled
- Monitor system resources during tests

#### Integration tests skipped
- Set `SKIP_DB_INTEGRATION_TESTS=0` or remove the environment variable
- Ensure test database exists and is accessible

### Test Data Cleanup

The test suite automatically cleans up test data:
- Unit tests use mocks (no cleanup needed)
- Integration tests clean up in `asyncTearDown()`
- Performance tests remove large datasets after completion

Manual cleanup if needed:
```sql
DELETE FROM minute_ohlcv WHERE symbol LIKE '%TEST%';
```

## Adding New Tests

### Unit Test Template
```python
async def test_new_functionality(self):
    """Test description"""
    db = TimescaleMinuteDataDB(self.db_config)
    
    # Mock dependencies
    with patch.object(db, 'dependency_method') as mock_dep:
        mock_dep.return_value = expected_result
        
        # Test the functionality
        result = await db.new_method(test_params)
        
        # Assertions
        self.assertEqual(result, expected_result)
        mock_dep.assert_called_once()
    
    db.close()
```

### Integration Test Template  
```python
async def test_new_integration(self):
    """Integration test description"""
    # Use real database operations
    test_data = self.create_realistic_minute_data(100, 'NEW_TEST')
    
    # Test real database interaction
    result = await self.db.new_database_operation(test_data)
    
    # Verify with real database query
    verification = await self.db.verify_operation()
    
    self.assertTrue(result)
    self.assertIsNotNone(verification)
```

### Performance Test Template
```python
async def test_new_performance_requirement(self):
    """Performance test for new requirement: <target>"""
    print(f"\nPERFORMANCE TEST: New Requirement (Target: <target>)")
    
    # Setup performance test
    test_data = self.create_performance_test_data(size)
    
    # Measure performance
    start_time = time.perf_counter()
    result = await self.db.performance_operation(test_data)
    elapsed = time.perf_counter() - start_time
    
    # Calculate metrics
    performance_metric = calculate_performance(result, elapsed)
    
    print(f"Performance: {performance_metric}")
    
    # Assert performance target
    self.assertLess(performance_metric, target_threshold)
```

## Conclusion

This comprehensive test suite ensures the TimescaleMinuteDataDB class meets all performance and reliability requirements for production financial data processing. The three-tier testing approach provides confidence in both functionality and performance while maintaining fast feedback cycles for development.

The test suite validates:
- ✅ **Performance**: >15k rows/sec writes, <500ms queries, <100ms aggregations
- ✅ **Reliability**: Error handling, connection management, data integrity
- ✅ **Scalability**: Large datasets, concurrent operations, memory efficiency  
- ✅ **Functionality**: All CRUD operations, aggregations, system metrics

Regular execution of this test suite ensures the system maintains its performance characteristics as the codebase evolves.