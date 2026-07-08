-- Validation Script for Slice 750 Calendar Data
-- Run this in DataGrip to verify calendar data matches official NYSE calendar
-- Expected to find all official holidays for 2024-2025

\echo '=========================================='
\echo '  Task 4.1.1: Verify 2024 NYSE Holidays'
\echo '=========================================='

SELECT
    holiday_date,
    holiday_name,
    market_status,
    early_close_time,
    CASE
        WHEN holiday_date = '2024-01-01' THEN '✓ New Years Day (Mon)'
        WHEN holiday_date = '2024-01-15' THEN '✓ MLK Day (Mon)'
        WHEN holiday_date = '2024-02-19' THEN '✓ Presidents Day (Mon)'
        WHEN holiday_date = '2024-03-29' THEN '✓ Good Friday'
        WHEN holiday_date = '2024-05-27' THEN '✓ Memorial Day (Mon)'
        WHEN holiday_date = '2024-06-19' THEN '✓ Juneteenth (Wed)'
        WHEN holiday_date = '2024-07-04' THEN '✓ Independence Day (Thu)'
        WHEN holiday_date = '2024-09-02' THEN '✓ Labor Day (Mon)'
        WHEN holiday_date = '2024-11-28' THEN '✓ Thanksgiving (Thu)'
        WHEN holiday_date = '2024-11-29' THEN '✓ Day after Thanksgiving - EARLY CLOSE'
        WHEN holiday_date = '2024-12-25' THEN '✓ Christmas (Wed)'
        ELSE '⚠ UNEXPECTED HOLIDAY'
    END as validation
FROM trading_holidays
WHERE calendar_id = 'NYSE'
  AND EXTRACT(YEAR FROM holiday_date) = 2024
ORDER BY holiday_date;

\echo ''
\echo 'Expected: 11 holidays (10 closed + 1 early close)'
SELECT COUNT(*) as holiday_count_2024
FROM trading_holidays
WHERE calendar_id = 'NYSE'
  AND EXTRACT(YEAR FROM holiday_date) = 2024;

\echo ''
\echo '=========================================='
\echo '  Task 4.1.2: Verify 2025 NYSE Holidays'
\echo '=========================================='

SELECT
    holiday_date,
    holiday_name,
    market_status,
    early_close_time,
    CASE
        WHEN holiday_date = '2025-01-01' THEN '✓ New Years Day (Wed)'
        WHEN holiday_date = '2025-01-20' THEN '✓ MLK Day (Mon)'
        WHEN holiday_date = '2025-02-17' THEN '✓ Presidents Day (Mon)'
        WHEN holiday_date = '2025-04-18' THEN '✓ Good Friday'
        WHEN holiday_date = '2025-05-26' THEN '✓ Memorial Day (Mon)'
        WHEN holiday_date = '2025-06-19' THEN '✓ Juneteenth (Thu)'
        WHEN holiday_date = '2025-07-04' THEN '✓ Independence Day (Fri)'
        WHEN holiday_date = '2025-09-01' THEN '✓ Labor Day (Mon)'
        WHEN holiday_date = '2025-11-27' THEN '✓ Thanksgiving (Thu)'
        WHEN holiday_date = '2025-11-28' THEN '✓ Day after Thanksgiving - EARLY CLOSE'
        WHEN holiday_date = '2025-12-25' THEN '✓ Christmas (Thu)'
        ELSE '⚠ UNEXPECTED HOLIDAY'
    END as validation
FROM trading_holidays
WHERE calendar_id = 'NYSE'
  AND EXTRACT(YEAR FROM holiday_date) = 2025
ORDER BY holiday_date;

\echo ''
\echo 'Expected: 11 holidays (10 closed + 1 early close)'
SELECT COUNT(*) as holiday_count_2025
FROM trading_holidays
WHERE calendar_id = 'NYSE'
  AND EXTRACT(YEAR FROM holiday_date) = 2025;

\echo ''
\echo '=========================================='
\echo '  Task 4.1.3: Verify Market Hours'
\echo '=========================================='

SELECT
    calendar_id,
    calendar_name,
    timezone,
    market_open_time,
    market_close_time,
    has_extended_hours,
    extended_open_time,
    extended_close_time,
    CASE
        WHEN calendar_id = 'NYSE' AND market_open_time = '09:30:00' AND market_close_time = '16:00:00'
            AND extended_open_time = '04:00:00' AND extended_close_time = '20:00:00'
            THEN '✓ NYSE hours correct'
        WHEN calendar_id = 'NASDAQ' AND market_open_time = '09:30:00' AND market_close_time = '16:00:00'
            AND extended_open_time = '04:00:00' AND extended_close_time = '20:00:00'
            THEN '✓ NASDAQ hours correct'
        WHEN calendar_id = 'CME' THEN '✓ CME (futures - 24hr)'
        ELSE '⚠ CHECK HOURS'
    END as validation
FROM trading_calendars
ORDER BY calendar_id;

\echo ''
\echo '=========================================='
\echo '  Task 4.1.4: Verify Early Close Times'
\echo '=========================================='

SELECT
    calendar_id,
    holiday_date,
    holiday_name,
    market_status,
    early_close_time,
    CASE
        WHEN early_close_time = '13:00:00' THEN '✓ Early close at 1:00 PM'
        WHEN market_status = 'early_close' AND early_close_time IS NULL THEN '⚠ Missing early close time'
        ELSE 'N/A'
    END as validation
FROM trading_holidays
WHERE market_status = 'early_close'
ORDER BY calendar_id, holiday_date;

\echo ''
\echo '=========================================='
\echo '  Validation Summary'
\echo '=========================================='

SELECT
    'NYSE 2024 Holidays' as check_item,
    COUNT(*) as count,
    CASE WHEN COUNT(*) = 11 THEN '✓ PASS' ELSE '✗ FAIL' END as status
FROM trading_holidays
WHERE calendar_id = 'NYSE' AND EXTRACT(YEAR FROM holiday_date) = 2024

UNION ALL

SELECT
    'NYSE 2025 Holidays' as check_item,
    COUNT(*) as count,
    CASE WHEN COUNT(*) = 11 THEN '✓ PASS' ELSE '✗ FAIL' END as status
FROM trading_holidays
WHERE calendar_id = 'NYSE' AND EXTRACT(YEAR FROM holiday_date) = 2025

UNION ALL

SELECT
    'NYSE Market Hours' as check_item,
    1 as count,
    CASE
        WHEN market_open_time = '09:30:00' AND market_close_time = '16:00:00'
        THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM trading_calendars
WHERE calendar_id = 'NYSE'

UNION ALL

SELECT
    'NYSE Extended Hours' as check_item,
    1 as count,
    CASE
        WHEN extended_open_time = '04:00:00' AND extended_close_time = '20:00:00'
        THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM trading_calendars
WHERE calendar_id = 'NYSE'

UNION ALL

SELECT
    'Early Close Times' as check_item,
    COUNT(*) as count,
    CASE
        WHEN COUNT(*) = COUNT(CASE WHEN early_close_time = '13:00:00' THEN 1 END)
        THEN '✓ PASS'
        ELSE '✗ FAIL'
    END as status
FROM trading_holidays
WHERE market_status = 'early_close';

\echo ''
\echo '=========================================='
\echo '  Validation Complete'
\echo '=========================================='
