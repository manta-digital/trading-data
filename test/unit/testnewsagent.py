import unittest
from unittest.mock import Mock, AsyncMock, patch
import pandas as pd
from datetime import datetime
from manta_trading.agents.newsagent import NewsAgent


class TestNewsAgent(unittest.IsolatedAsyncioTestCase):
    async def test_mergeData(self):
        # Create mock services
        mockMarketDb = Mock()
        newsService = AsyncMock()

        # Set up the NewsAgent
        agent = NewsAgent(mockMarketDb, newsService)

        # Mock market data
        marketData = [
            ('TSLA', datetime(2020, 1, 1), 100, 120, 90, 105, 105, 0, 1, 1000000),
            ('TSLA', datetime(2020, 1, 2), 106, 110, 101, 110, 110, 0, 1, 1200000)
        ]

        # Mock news data
        newsData = [
            {'time_published': '20200101T120000', 'ticker_sentiment_score': 0.5, 'headline': 'Company A gains', 'relevance_score': 0.25},
            {'time_published': '20200102T140000', 'ticker_sentiment_score': 0.6, 'headline': 'Company A record profits', 'relevance_score': 0.7}
        ]

        # Set up the mock returns
        mockMarketDb.readDailyOHLCVAdjusted.return_value = marketData
        newsService.readNewsSentiment.return_value = newsData

        # Patch DateTimeHelper.convertToDbFormat to return the input unchanged
        with patch('manta_trading.util.datetimehelper.DateTimeHelper.convertToDbFormatString', side_effect=lambda x: x):
            # Run the method
            result = await agent.readMergedData('TSLA', '20200101', '20200102')

        # Ensure that readNewsSentiment was called
        newsService.readNewsSentiment.assert_awaited_once()

        # Check that the result is a DataFrame
        self.assertIsInstance(result, pd.DataFrame)

        # Check that the DataFrame has the expected columns
        expected_columns = ['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'volume', 'sentiment_min', 'sentiment_max', 'sentiment_count',
                            'weighted_sentiment_mean']
        self.assertListEqual(list(result.columns), expected_columns)

        # Check that the DataFrame has the expected number of rows
        self.assertEqual(len(result), 2)

        # Check some specific values
        self.assertEqual(result.loc[0, 'open'], 100)
        self.assertEqual(result.loc[1, 'close'], 110)
        self.assertEqual(result.loc[0, 'sentiment_min'], 0.5)
        self.assertEqual(result.loc[1, 'sentiment_max'], 0.6)
        self.assertEqual(result.loc[0, 'sentiment_count'], 1)
        self.assertAlmostEqual(result.loc[1, 'weighted_sentiment_mean'], 0.6, places=2)


if __name__ == '__main__':
    unittest.main()
