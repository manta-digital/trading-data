import unittest
from manta_trading.news.newsdbmigrationutility import NewsDbMigrationUtility


class TestTimeConversion(unittest.TestCase):

    def testConvertAlphaVantageToIso8601(self):
        testCases = [
            ("20230515T123456", "2023-05-15T16:34:56+00:00"),  # 12:34:56 EDT is 16:34:56 UTC
            ("20211231T235959", "2022-01-01T04:59:59+00:00"),  # 23:59:59 EST is 04:59:59 UTC (next day)
            ("20200301T000000", "2020-03-01T05:00:00+00:00"),  # 00:00:00 EST is 05:00:00 UTC
            ("20200301T030000", "2020-03-01T08:00:00+00:00"),  # 03:00:00 EDT is 07:00:00 UTC (after DST change)
        ]

        for av_time, expected_iso in testCases:
            with self.subTest(av_time=av_time):
                result = NewsDbMigrationUtility.convertAlphaVantageToIso8601(av_time)
                self.assertEqual(result, expected_iso)

    def testConvertAlphaVantageToIso8601_invalidInput(self):
        with self.assertRaises(ValueError):
            NewsDbMigrationUtility.convertAlphaVantageToIso8601("invalid_format")

    def testFixBrokenIsoFormat(self):
        testCases = [
            ("2024-08-03T09:24:16.000Z", "2024-08-03T09:24:16+00:00"),
            ("2023-12-25T12:00:00.000Z", "2023-12-25T12:00:00+00:00"),
            ("2022-07-04T04:30:45.000Z", "2022-07-04T04:30:45+00:00"),
            ("2021-01-01T00:00:00.000Z", "2021-01-01T00:00:00+00:00"),
        ]

        for broken_iso, expected_iso in testCases:
            with self.subTest(broken_iso=broken_iso):
                result = NewsDbMigrationUtility.fixBrokenIsoFormat(broken_iso)
                self.assertEqual(result, expected_iso)


if __name__ == '__main__':
    unittest.main()
