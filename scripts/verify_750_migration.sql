-- Verification Script for Migration 750
-- Run this in DataGrip or via psql to verify the foundation tables
-- Usage: psql -h <host> -U <user> -d trading_test -f scripts/verify_750_migration.sql

\echo '=== Checking Table Existence ==='
SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'public'
  AND table_name IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions')
ORDER BY table_name;

\echo ''
\echo '=== Instruments Summary ==='
SELECT COUNT(*) as total_instruments,
       COUNT(DISTINCT asset_class) as asset_classes,
       COUNT(DISTINCT venue) as venues
FROM instruments;

\echo ''
\echo '=== Sample Instruments ==='
SELECT canonical_id, symbol, asset_class, venue, trading_calendar_id
FROM instruments
ORDER BY canonical_id
LIMIT 10;

\echo ''
\echo '=== Trading Calendars ==='
SELECT calendar_id, calendar_name, timezone,
       market_open_time, market_close_time, has_extended_hours
FROM trading_calendars
ORDER BY calendar_id;

\echo ''
\echo '=== Holidays Summary by Calendar ==='
SELECT calendar_id,
       COUNT(*) as holiday_count,
       MIN(holiday_date) as earliest,
       MAX(holiday_date) as latest
FROM trading_holidays
GROUP BY calendar_id
ORDER BY calendar_id;

\echo ''
\echo '=== Sample NYSE Holidays ==='
SELECT holiday_date, holiday_name, market_status, early_close_time
FROM trading_holidays
WHERE calendar_id = 'NYSE'
ORDER BY holiday_date
LIMIT 10;

\echo ''
\echo '=== Provider Mappings Summary ==='
SELECT provider,
       COUNT(*) as mapping_count,
       COUNT(DISTINCT instrument_id) as unique_instruments
FROM provider_symbol_mapping
GROUP BY provider;

\echo ''
\echo '=== Sample Provider Mappings ==='
SELECT psm.provider, psm.provider_symbol, i.canonical_id, psm.valid_from, psm.valid_to
FROM provider_symbol_mapping psm
JOIN instruments i ON psm.instrument_id = i.instrument_id
ORDER BY i.canonical_id
LIMIT 10;

\echo ''
\echo '=== minute_ohlcv New Columns ==='
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'minute_ohlcv'
  AND column_name IN ('adjustment_policy', 'session_type', 'provider_version', 'data_version', 'ingestion_timestamp')
ORDER BY column_name;

\echo ''
\echo '=== Indexes Summary ==='
SELECT tablename, COUNT(*) as index_count
FROM pg_indexes
WHERE schemaname = 'public'
  AND tablename IN ('instruments', 'provider_symbol_mapping', 'trading_calendars', 'trading_holidays', 'trading_sessions')
GROUP BY tablename
ORDER BY tablename;

\echo ''
\echo '=== Foreign Key Constraints ==='
SELECT
    tc.table_name,
    tc.constraint_name,
    kcu.column_name,
    ccu.table_name AS foreign_table_name,
    ccu.column_name AS foreign_column_name
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
    AND ccu.table_schema = tc.table_schema
WHERE tc.constraint_type = 'FOREIGN KEY'
    AND tc.table_name IN ('provider_symbol_mapping', 'trading_holidays', 'trading_sessions')
ORDER BY tc.table_name, tc.constraint_name;

\echo ''
\echo '=== Verification Complete ==='
