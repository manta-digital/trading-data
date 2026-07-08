-- Validation Script for Slice 750 Instrument Data
-- Run this in DataGrip to verify instrument metadata is correct
-- Expected to validate top 10 stocks and their provider mappings

\echo '=========================================='
\echo '  Task 4.2.1: Verify Top 10 Stock Metadata'
\echo '=========================================='

SELECT
    canonical_id,
    symbol,
    asset_class,
    venue,
    trading_calendar_id,
    active,
    CASE
        -- NASDAQ stocks
        WHEN symbol = 'AAPL' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ AAPL correct'
        WHEN symbol = 'MSFT' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ MSFT correct'
        WHEN symbol = 'GOOGL' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ GOOGL correct'
        WHEN symbol = 'AMZN' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ AMZN correct'
        WHEN symbol = 'NVDA' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ NVDA correct'
        WHEN symbol = 'META' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ META correct'
        WHEN symbol = 'TSLA' AND venue = 'NASDAQ' AND trading_calendar_id = 'NASDAQ' THEN '✓ TSLA correct'
        -- NYSE stocks
        WHEN symbol = 'BRK.B' AND venue = 'NYSE' AND trading_calendar_id = 'NYSE' THEN '✓ BRK.B correct'
        WHEN symbol = 'V' AND venue = 'NYSE' AND trading_calendar_id = 'NYSE' THEN '✓ V correct'
        WHEN symbol = 'JPM' AND venue = 'NYSE' AND trading_calendar_id = 'NYSE' THEN '✓ JPM correct'
        ELSE '⚠ CHECK METADATA'
    END as validation
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
ORDER BY canonical_id;

\echo ''
\echo 'Canonical ID Format Check (should be SYMBOL.VENUE):'
SELECT
    canonical_id,
    symbol,
    venue,
    CASE
        WHEN canonical_id = symbol || '.' || venue THEN '✓ Format correct'
        ELSE '✗ Format incorrect'
    END as validation
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
ORDER BY canonical_id;

\echo ''
\echo 'Asset Class Check (all should be stock):'
SELECT
    DISTINCT asset_class,
    COUNT(*) as count,
    CASE
        WHEN asset_class = 'stock' THEN '✓ Correct'
        ELSE '⚠ Unexpected asset class'
    END as validation
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
GROUP BY asset_class;

\echo ''
\echo 'Active Status Check (all should be active):'
SELECT
    COUNT(*) as total_count,
    COUNT(CASE WHEN active = true THEN 1 END) as active_count,
    CASE
        WHEN COUNT(*) = COUNT(CASE WHEN active = true THEN 1 END) THEN '✓ All active'
        ELSE '⚠ Some inactive'
    END as validation
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM');

\echo ''
\echo '=========================================='
\echo '  Task 4.2.2: Verify Provider Symbol Mappings'
\echo '=========================================='

SELECT
    i.symbol,
    psm.provider,
    psm.provider_symbol,
    psm.valid_from,
    psm.valid_to,
    CASE
        WHEN psm.provider = 'alphavantage' AND psm.provider_symbol = i.symbol THEN '✓ Mapping correct'
        WHEN psm.provider = 'alphavantage' AND psm.provider_symbol != i.symbol THEN '⚠ Symbol mismatch'
        ELSE '⚠ Unknown provider'
    END as validation
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE i.symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
ORDER BY i.symbol;

\echo ''
\echo 'Valid Date Range Check (valid_to should be NULL for current mappings):'
SELECT
    i.symbol,
    psm.valid_from,
    psm.valid_to,
    CASE
        WHEN psm.valid_to IS NULL THEN '✓ Current mapping'
        ELSE '⚠ Historical mapping'
    END as validation
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE i.symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
ORDER BY i.symbol;

\echo ''
\echo 'Valid From Date Check (should be reasonable, e.g., 2020-01-01):'
SELECT
    COUNT(*) as total_mappings,
    COUNT(CASE WHEN valid_from >= '2020-01-01' AND valid_from <= CURRENT_DATE THEN 1 END) as reasonable_dates,
    CASE
        WHEN COUNT(*) = COUNT(CASE WHEN valid_from >= '2020-01-01' AND valid_from <= CURRENT_DATE THEN 1 END)
        THEN '✓ All dates reasonable'
        ELSE '⚠ Some dates questionable'
    END as validation
FROM provider_symbol_mapping psm
JOIN instruments i ON psm.instrument_id = i.instrument_id
WHERE i.symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM');

\echo ''
\echo '=========================================='
\echo '  Additional Checks'
\echo '=========================================='

\echo 'Total Instruments Seeded:'
SELECT
    COUNT(*) as total_instruments,
    CASE
        WHEN COUNT(*) >= 50 THEN '✓ Expected ~50 stocks'
        ELSE '⚠ Fewer than expected'
    END as validation
FROM instruments
WHERE asset_class = 'stock';

\echo ''
\echo 'Provider Mappings Coverage:'
SELECT
    COUNT(DISTINCT i.instrument_id) as instruments_with_mappings,
    (SELECT COUNT(*) FROM instruments WHERE asset_class = 'stock') as total_instruments,
    CASE
        WHEN COUNT(DISTINCT i.instrument_id) = (SELECT COUNT(*) FROM instruments WHERE asset_class = 'stock')
        THEN '✓ All instruments have mappings'
        ELSE '⚠ Some instruments missing mappings'
    END as validation
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE i.asset_class = 'stock';

\echo ''
\echo '=========================================='
\echo '  Validation Summary'
\echo '=========================================='

SELECT
    'Top 10 Stocks Exist' as check_item,
    COUNT(*) as count,
    CASE WHEN COUNT(*) = 10 THEN '✓ PASS' ELSE '✗ FAIL' END as status
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')

UNION ALL

SELECT
    'Canonical ID Format' as check_item,
    COUNT(*) as count,
    CASE
        WHEN COUNT(*) = COUNT(CASE WHEN canonical_id = symbol || '.' || venue THEN 1 END)
        THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')

UNION ALL

SELECT
    'Trading Calendar Matches Venue' as check_item,
    COUNT(*) as count,
    CASE
        WHEN COUNT(*) = COUNT(CASE WHEN trading_calendar_id = venue THEN 1 END)
        THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM instruments
WHERE symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')

UNION ALL

SELECT
    'Provider Mappings Exist' as check_item,
    COUNT(*) as count,
    CASE WHEN COUNT(*) = 10 THEN '✓ PASS' ELSE '✗ FAIL' END as status
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE i.symbol IN ('AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'META', 'TSLA', 'BRK.B', 'V', 'JPM')
  AND psm.valid_to IS NULL

UNION ALL

SELECT
    'All Stocks Have Mappings' as check_item,
    COUNT(DISTINCT i.instrument_id) as count,
    CASE
        WHEN COUNT(DISTINCT i.instrument_id) = (SELECT COUNT(*) FROM instruments WHERE asset_class = 'stock')
        THEN '✓ PASS'
        ELSE '⚠ WARNING'
    END as status
FROM instruments i
JOIN provider_symbol_mapping psm ON i.instrument_id = psm.instrument_id
WHERE i.asset_class = 'stock';

\echo ''
\echo '=========================================='
\echo '  Validation Complete'
\echo '=========================================='
