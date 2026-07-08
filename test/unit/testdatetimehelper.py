import unittest
from datetime import datetime, timedelta, timezone, date

import pytz

from manta_trading.util.datetimehelper import DateTimeHelper


class TestDateTimeHelper(unittest.TestCase):

    def test_getDefaultDateInterval_with_no_dates(self):
        start, end = DateTimeHelper.getDefaultDateInterval(None, None)
        self.assertEqual(start, '2020-01-01')
        self.assertEqual(end, '2020-12-31')

    def test_getDefaultDateInterval_with_start_date_only(self):
        start, end = DateTimeHelper.getDefaultDateInterval('2021-02-01', None)
        self.assertEqual(start, '2021-02-01')
        self.assertEqual(end, '2022-01-31')

    def test_getDefaultDateInterval_with_end_date_only(self):
        start, end = DateTimeHelper.getDefaultDateInterval(None, '2023-12-31')
        self.assertEqual(start, '2020-01-01')
        self.assertEqual(end, '2023-12-31')

    def test_getDefaultDateInterval_with_both_dates(self):
        start, end = DateTimeHelper.getDefaultDateInterval('2022-03-05', '2023-03-04')
        self.assertEqual(start, '2022-03-05')
        self.assertEqual(end, '2023-03-04')

    def test_getDefaultDateInterval(self):
        # Test cases for getDefaultDateInterval
        test_cases = [
            (None, None, ('2020-01-01', '2020-12-31')),
            ('2021-05-15', None, ('2021-05-15', '2022-05-14')),
            ('2023-03-01', '2024-04-30', ('2023-03-01', '2024-04-30')),
            (None, '2022-08-15', ('2020-01-01', '2022-08-15'))
        ]

        for start_date, end_date, expected in test_cases:
            result = DateTimeHelper.getDefaultDateInterval(start_date, end_date)
            self.assertEqual(result, expected, f"Failed for start_date: {start_date}, end_date: {end_date}")

    def test_asDateTime_with_datetime(self):
        now = datetime.now()
        result = DateTimeHelper.asDateTime(now, DateTimeHelper.DATE_FORMAT)
        self.assertEqual(result, now)

    def test_asDateTime_with_string(self):
        date_str = '2022-03-05'
        expected_datetime = datetime(2022, 3, 5)
        result = DateTimeHelper.asDateTime(date_str, DateTimeHelper.DATE_FORMAT)
        self.assertEqual(result, expected_datetime)

    def test_asDateTime_with_invalid_input(self):
        with self.assertRaises(ValueError):
            DateTimeHelper.asDateTime(12345, DateTimeHelper.DATE_FORMAT)  # Not a datetime object or a proper string

    def test_asDateTime(self):
        # Test cases for asDateTime
        test_cases = [
            ('2024-04-30', '%Y-%m-%d', datetime(2024, 4, 30)),
            ('30-04-2024', '%d-%m-%Y', datetime(2024, 4, 30)),
            (datetime(2024, 4, 30), '%Y-%m-%d', datetime(2024, 4, 30)),
            (None, '%Y-%m-%d', None),
        ]

        for date_test, date_format, expected in test_cases:
            result = DateTimeHelper.asDateTime(date_test, date_format)
            self.assertEqual(result, expected, f"Failed for date_test: {date_test}, date_format: {date_format}")

        # Test with invalid input
        with self.assertRaises(ValueError):
            DateTimeHelper.asDateTime(12345, DateTimeHelper.DATE_FORMAT)

    def test_convertDateToAPIFormat(self):
        test_cases = [
            ('2024-04-30', '20240430T0000'),
            ('20240430T000000', '20240430T0000'),
            ('20240430T0000', '20240430T0000'),
            ('2024/04/30', '20240430T0000'),
            ('30-04-2024', '20240430T0000'),
            ('30/04/2024', '20240430T0000'),
            ('2024-04-30 15:30:45', '20240430T1530'),
        ]

        for input_str, expected_output in test_cases:
            with self.subTest(input_str=input_str):
                result = DateTimeHelper.convertToApiFormatString(input_str, tz='US/Mountain')
                self.assertEqual(result, expected_output)

        # Test with invalid date format
        with self.assertRaises(ValueError):
            DateTimeHelper.convertToApiFormatString('invalid-date-format')

    def test_convertDateToDbFormat(self):
        # Test cases with seconds (default behavior)
        test_cases_with_seconds = [
            ('2024-04-30', '20240430T000000'),
            ('20240430T0000', '20240430T000000'),
            ('20240430T000000', '20240430T000000'),
            ('2024/04/30', '20240430T000000'),
            ('30-04-2024', '20240430T000000'),
            ('30/04/2024', '20240430T000000'),
            ('2024-04-30 15:30:45', '20240430T153045'),
        ]

        for dateStr, expected in test_cases_with_seconds:
            with self.subTest(f"{dateStr} with seconds"):
                result = DateTimeHelper.convertToDbFormatString(dateStr, DateTimeHelper.DB_FORMAT_SECONDS)
                self.assertEqual(result, expected, f"Failed for date: {dateStr}")

        # Test cases without seconds
        test_cases_without_seconds = [
            ('2024-04-30', '20240430T0000'),
            ('20240430T0000', '20240430T0000'),
            ('20240430T000000', '20240430T0000'),
            ('2024/04/30', '20240430T0000'),
            ('30-04-2024', '20240430T0000'),
            ('30/04/2024', '20240430T0000'),
            ('2024-04-30 15:30:45', '20240430T1530'),
        ]

        for dateStr, expected in test_cases_without_seconds:
            with self.subTest(f"{dateStr} without seconds"):
                result = DateTimeHelper.convertToDbFormatString(dateStr, DateTimeHelper.DB_FORMAT)
                self.assertEqual(result, expected, f"Failed for date: {dateStr}")

        # Test with invalid date format
        with self.assertRaises(ValueError):
            DateTimeHelper.convertToDbFormatString('invalid-date-format')

    def testToUtcTimestamp(self):
        # Test with datetime object
        dt = datetime(2023, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
        timestamp = DateTimeHelper.toUtcTimestamp(dt)
        self.assertAlmostEqual(timestamp, 1690808096, delta=3600)  # Allow 1 hour difference

        # Test with string
        timestamp = DateTimeHelper.toUtcTimestamp("2023-07-31T12:34:56Z")
        self.assertAlmostEqual(timestamp, 1690808096, delta=3600)  # Allow 1 hour difference

        # Test with naive datetime
        naive_dt = datetime(2023, 7, 31, 12, 34, 56)
        timestamp = DateTimeHelper.toUtcTimestamp(naive_dt)
        self.assertAlmostEqual(timestamp, 1690808096, delta=3600)  # Allow 1 hour difference

    def testFromUtcTimestamp(self):
        timestamp = 1690808096
        dt = DateTimeHelper.fromUtcTimestamp(timestamp)
        expected_dt = datetime(2023, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
        self.assertAlmostEqual(dt, expected_dt, delta=timedelta(hours=1))  # Allow 1 hour difference

    def testToIso8601(self):
        # Test with datetime
        dt = datetime(2023, 7, 31, 12, 54, 56, tzinfo=timezone.utc)
        iso_string = DateTimeHelper.toIso8601(dt)
        self.assertEqual(iso_string, "2023-07-31T12:54:56+00:00")

        # Test with timestamp
        iso_string = DateTimeHelper.toIso8601(1690808096)
        self.assertEqual(iso_string, "2023-07-31T12:54:56+00:00")

    def testParseFlexibleTimestamp(self):
        # Test with int (timestamp)
        dt = DateTimeHelper.parseTimestampAsDatetime(1690808096)
        expected_dt = datetime(2023, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
        self.assertAlmostEqual(dt, expected_dt, delta=timedelta(hours=1))  # Allow 1 hour difference

        # Test with ISO 8601 string
        dt = DateTimeHelper.parseTimestampAsDatetime("2023-07-31T12:34:56Z")
        self.assertAlmostEqual(dt, expected_dt, delta=timedelta(hours=1))  # Allow 1 hour difference

        # Test with datetime object
        input_dt = datetime(2023, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
        dt = DateTimeHelper.parseTimestampAsDatetime(input_dt)
        self.assertEqual(dt, expected_dt)

        # Test ISO-8601 parsing
        tz_eastern = pytz.timezone('US/Eastern')
        expected_dt = tz_eastern.localize(datetime(2023, 7, 31, 12, 34, 56))
        input_dt = tz_eastern.localize(datetime(2023, 7, 31, 12, 34, 56))
        input_timestr = input_dt.isoformat()
        dt = DateTimeHelper.parseTimestampAsDatetime(input_timestr)
        self.assertEqual(dt, expected_dt)

        # Test with invalid input
        with self.assertRaises(ValueError):
            DateTimeHelper.parseTimestampAsDatetime([1, 2, 3])

    # todo: test some additional cases here, but this hits the basic ones.
    def testParseUtc(self):
        time_str = '2024-12-31'
        expected_dt = datetime(2024, 12, 31, tzinfo=timezone.utc)
        result = DateTimeHelper.toDateTime(time_str)
        self.assertEqual(result, expected_dt)

        result = DateTimeHelper.toDateTime(None)
        self.assertEqual(result, None)

    def testRoundTrip(self):
        original_dt = datetime(2023, 7, 31, 12, 34, 56, tzinfo=timezone.utc)
        timestamp = DateTimeHelper.toUtcTimestamp(original_dt)
        roundtrip_dt = DateTimeHelper.fromUtcTimestamp(timestamp)
        self.assertAlmostEqual(original_dt, roundtrip_dt, delta=timedelta(hours=1))  # Allow 1 hour difference

    def testapplyDateTimeDefaults(self):
        # Test with None input
        self.assertIsNone(DateTimeHelper.applyDateTimeDefaults(None))

        # Test with date object
        d = date(2024, 7, 1)
        result = DateTimeHelper.applyDateTimeDefaults(d)
        self.assertEqual(result, datetime(2024, 7, 1, tzinfo=timezone.utc))

        # Test with naive datetime at midnight
        dt_midnight = datetime(2024, 7, 1)
        result = DateTimeHelper.applyDateTimeDefaults(dt_midnight)
        self.assertEqual(result, datetime(2024, 7, 1, tzinfo=timezone.utc))

        # Test with naive datetime not at midnight
        dt = datetime(2024, 7, 1, 12, 30)
        result = DateTimeHelper.applyDateTimeDefaults(dt)
        local_dt = datetime.now(timezone.utc).astimezone().tzinfo
        expected = dt.replace(tzinfo=local_dt).astimezone(timezone.utc)
        self.assertEqual(result, expected)

        # Test with aware datetime
        aware_dt = datetime(2024, 7, 1, 12, 30, tzinfo=timezone.utc)
        result = DateTimeHelper.applyDateTimeDefaults(aware_dt)
        self.assertEqual(result, aware_dt)

        # Test with date string
        date_str = '2024-07-01'
        result = DateTimeHelper.applyDateTimeDefaults(date_str)
        self.assertEqual(result, datetime(2024, 7, 1, tzinfo=timezone.utc))

        # Test with datetime string
        dt_str = '2024-07-01 12:30:00'
        result = DateTimeHelper.applyDateTimeDefaults(dt_str)
        local_dt = datetime.now(timezone.utc).astimezone().tzinfo
        expected = datetime(2024, 7, 1, 12, 30, tzinfo=local_dt).astimezone(timezone.utc)
        self.assertEqual(result, expected)

        # Test with ISO format string
        iso_str = '2024-07-01T12:30:00+00:00'
        result = DateTimeHelper.applyDateTimeDefaults(iso_str)
        self.assertEqual(result, datetime(2024, 7, 1, 12, 30, tzinfo=timezone.utc))

        # Test with timestamp (int)
        timestamp = int(datetime(2024, 7, 1, 12, 30, tzinfo=timezone.utc).timestamp())
        result = DateTimeHelper.applyDateTimeDefaults(timestamp)
        self.assertEqual(result, datetime(2024, 7, 1, 12, 30, tzinfo=timezone.utc))
if __name__ == '__main__':
    unittest.main()
