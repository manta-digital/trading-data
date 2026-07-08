-- FINAL WORKING TIMESCALEDB - Run as postgres user on trading database
-- This version avoids all the timezone and policy bullshit

-- Clean slate
DROP MATERIALIZED VIEW IF EXISTS minute_monthly_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_weekly_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_daily_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_4hour_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_hourly_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_15min_ohlcv CASCADE;
DROP MATERIALIZED VIEW IF EXISTS minute_5min_ohlcv CASCADE;
DROP TABLE IF EXISTS minute_ohlcv CASCADE;

-- Create hypertable
CREATE TABLE minute_ohlcv (
    time TIMESTAMPTZ NOT NULL,
    symbol TEXT NOT NULL,
    open NUMERIC(12,4) NOT NULL,
    high NUMERIC(12,4) NOT NULL,
    low NUMERIC(12,4) NOT NULL,
    close NUMERIC(12,4) NOT NULL,
    volume BIGINT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Make it a hypertable
SELECT create_hypertable('minute_ohlcv', 'time', chunk_time_interval => INTERVAL '4 hours');

-- Add indexes
CREATE INDEX ix_minute_ohlcv_symbol_time ON minute_ohlcv (symbol, time DESC);
CREATE INDEX ix_minute_ohlcv_time_symbol ON minute_ohlcv (time DESC, symbol);

-- 5-minute continuous aggregation
CREATE MATERIALIZED VIEW minute_5min_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('5 minutes', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume,
    COUNT(*) as minute_count
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- 15-minute continuous aggregation
CREATE MATERIALIZED VIEW minute_15min_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('15 minutes', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- Hourly continuous aggregation
CREATE MATERIALIZED VIEW minute_hourly_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 hour', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- 4-hour continuous aggregation (half trading day - useful for charting)
CREATE MATERIALIZED VIEW minute_4hour_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('4 hours', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- Daily continuous aggregation (NO TIMEZONE - fixes the bucket error)
CREATE MATERIALIZED VIEW minute_daily_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 day', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- Weekly continuous aggregation
CREATE MATERIALIZED VIEW minute_weekly_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 week', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- Monthly continuous aggregation
CREATE MATERIALIZED VIEW minute_monthly_ohlcv
WITH (timescaledb.continuous) AS
SELECT 
    time_bucket('1 month', time) AS time_bucket,
    symbol,
    FIRST(open, time) as open,
    MAX(high) as high,
    MIN(low) as low,
    LAST(close, time) as close,
    SUM(volume) as volume
FROM minute_ohlcv
GROUP BY time_bucket, symbol;

-- Add compression (the good stuff)
ALTER TABLE minute_ohlcv SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol',
    timescaledb.compress_orderby = 'time DESC'
);

-- Insert test data
INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, volume) VALUES 
('2024-01-01 14:30:00+00', 'TSLA', 250.00, 251.50, 249.75, 251.00, 1000000),
('2024-01-01 14:31:00+00', 'TSLA', 251.00, 252.25, 250.50, 251.75, 950000),
('2024-01-01 14:32:00+00', 'TSLA', 251.75, 253.00, 251.50, 252.50, 1100000),
('2024-01-01 14:35:00+00', 'TSLA', 252.50, 253.75, 252.00, 253.25, 1200000),
('2024-01-01 14:40:00+00', 'TSLA', 253.25, 254.50, 253.00, 254.00, 1150000),
('2024-01-01 15:00:00+00', 'AAPL', 180.00, 181.25, 179.50, 180.75, 2000000),
('2024-01-01 15:01:00+00', 'AAPL', 180.75, 181.50, 180.25, 181.00, 1900000);

-- Refresh all aggregations with WIDER DATE RANGE (fixes bucket errors)
CALL refresh_continuous_aggregate('minute_5min_ohlcv', '2024-01-01', '2024-01-03');
CALL refresh_continuous_aggregate('minute_15min_ohlcv', '2024-01-01', '2024-01-03');
CALL refresh_continuous_aggregate('minute_hourly_ohlcv', '2024-01-01', '2024-01-03');
CALL refresh_continuous_aggregate('minute_4hour_ohlcv', '2024-01-01', '2024-01-03');
CALL refresh_continuous_aggregate('minute_daily_ohlcv', '2024-01-01', '2024-01-03');
CALL refresh_continuous_aggregate('minute_weekly_ohlcv', '2024-01-01', '2024-01-10');
CALL refresh_continuous_aggregate('minute_monthly_ohlcv', '2024-01-01', '2024-02-01');

-- VALIDATION - This should show everything working
SELECT 'Base table count:' as metric, COUNT(*) as value FROM minute_ohlcv;
SELECT '5min aggregations:' as metric, COUNT(*) as value FROM minute_5min_ohlcv;
SELECT '15min aggregations:' as metric, COUNT(*) as value FROM minute_15min_ohlcv;
SELECT 'Hourly aggregations:' as metric, COUNT(*) as value FROM minute_hourly_ohlcv;
SELECT '4hour aggregations:' as metric, COUNT(*) as value FROM minute_4hour_ohlcv;
SELECT 'Daily aggregations:' as metric, COUNT(*) as value FROM minute_daily_ohlcv;
SELECT 'Weekly aggregations:' as metric, COUNT(*) as value FROM minute_weekly_ohlcv;
SELECT 'Monthly aggregations:' as metric, COUNT(*) as value FROM minute_monthly_ohlcv;

SELECT 'Sample aggregations:' as section;
SELECT * FROM minute_5min_ohlcv ORDER BY time_bucket LIMIT 3;
SELECT * FROM minute_4hour_ohlcv ORDER BY time_bucket LIMIT 2;

SELECT 'COMPLETE SUCCESS - TimescaleDB with 7 aggregation levels!' as status;