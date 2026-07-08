import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from manta_trading.news.newsfields import NewsFields
from manta_trading.news.newsservice import NewsService, NewsUpdateStatus
from manta_trading.util.datetimehelper import DateTimeHelper


class TestNewsService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mockDb = AsyncMock()
        self.mockDb.isConnected = AsyncMock(return_value=True)
        self.mockDb.readNewsUpdateMetadata = AsyncMock()
        self.mockDb.writeNewsUpdateMetadata = AsyncMock()
        self.mockDb.writeNews = AsyncMock()

        self.mockApi = AsyncMock()
        self.mockApi.getNewsSentiment = AsyncMock()

        self.newsService = NewsService(api=self.mockApi, db=self.mockDb)

    async def testUpdateNews(self):
        self.newsService.getUpdateStatus = AsyncMock(return_value=NewsUpdateStatus())
        self.newsService.fetchAndUpdateNews = AsyncMock()
        await self.newsService.updateNews()

        # Assert that getUpdateStatus and fetchAndUpdateNews were called
        self.newsService.getUpdateStatus.assert_awaited_once()
        self.newsService.fetchAndUpdateNews.assert_awaited_once()

    async def testFetchAndUpdateNews(self):
        status = NewsUpdateStatus()
        status.latest = datetime(2023, 1, 1, tzinfo=timezone.utc)

        # Mock fetchNewsInRange to return some sample data for the first call,
        # and then return data that doesn't advance the latest article time
        self.newsService.fetchNewsInRange = AsyncMock(side_effect=[
            [
                {'time_published': '2023-01-02T00:00:00+00:00'},
                {'time_published': '2023-01-03T00:00:00+00:00'}
            ],
            [{'time_published': '2023-01-03T00:00:00+00:00'}],
            [{'time_published': '2023-01-03T00:00:00+00:00'}],
            [{'time_published': '2023-01-03T00:00:00+00:00'}],
            []  # This should never be reached due to the no-progress limit
        ])

        self.newsService.processAndStoreNews = AsyncMock()
        self.newsService.setUpdateStatus = AsyncMock()

        await self.newsService.fetchAndUpdateNews(status)

        # Assert that fetchNewsInRange was called 4 times (3 no-progress attempts + 1 successful)
        self.assertEqual(self.newsService.fetchNewsInRange.call_count, 4)

        # Assert that processAndStoreNews was called only once (for the first, progressive batch)
        self.newsService.processAndStoreNews.assert_awaited_once()

        # Assert that setUpdateStatus was called twice (once for the progress, once for completion)
        self.assertEqual(self.newsService.setUpdateStatus.call_count, 2)

        # Assert that the final status is 'complete'
        final_call_args = self.newsService.setUpdateStatus.call_args_list[-1][0][0]
        self.assertEqual(final_call_args.status, 'complete')
        self.assertEqual(final_call_args.latest, datetime(2023, 1, 3, tzinfo=timezone.utc))

    async def testFetchNewsInRange(self):
        start_date = datetime(2023, 1, 1, tzinfo=timezone.utc)
        end_date = datetime(2023, 1, 2, tzinfo=timezone.utc)

        self.mockApi.getNewsSentiment.return_value = {
            NewsFields.DB_NEWSITEMS: [{'time_published': '2023-01-01T12:00:00+00:00'}]
        }

        result = await self.newsService.fetchNewsInRange(start_date, end_date)

        self.mockApi.getNewsSentiment.assert_awaited_once_with(
            dateEarliest=DateTimeHelper.toIso8601(start_date),
            dateLatest=DateTimeHelper.toIso8601(end_date),
            limit=1000,
        )
        self.assertEqual(len(result), 1)

    async def testProcessAndStoreNews(self):
        news_items = [{'time_published': '2023-01-01T12:00:00+00:00'}]

        await self.newsService.processAndStoreNews(news_items)
        self.mockDb.writeNews.assert_awaited_once_with({NewsFields.DB_NEWSITEMS: news_items})

    async def testGetUpdateStatus(self):
        self.mockDb.readNewsUpdateMetadata.return_value = {
            NewsFields.DB_META_STATUS: 'incomplete',
            NewsFields.DB_META_EARLIEST: '2023-01-01T00:00:00+00:00',
            NewsFields.DB_META_LATEST: '2023-01-31T23:59:59+00:00',
            NewsFields.DB_TIMESTAMP: '2023-02-01T00:00:00+00:00'
        }

        status = await self.newsService.getUpdateStatus()

        self.assertEqual(status.status, 'incomplete')
        self.assertEqual(status.earliest, datetime(2023, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(status.latest, datetime(2023, 1, 31, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(status.last_updated, datetime(2023, 2, 1, tzinfo=timezone.utc))

    async def testSetUpdateStatus(self):
        status = NewsUpdateStatus()
        status.status = 'complete'
        status.earliest = datetime(2023, 1, 1, tzinfo=timezone.utc)
        status.latest = datetime(2023, 1, 31, 23, 59, 59, tzinfo=timezone.utc)

        await self.newsService.setUpdateStatus(status)

        self.mockDb.writeNewsUpdateMetadata.assert_awaited_once()
        call_args = self.mockDb.writeNewsUpdateMetadata.call_args[0][0]
        self.assertEqual(call_args[NewsFields.DB_META_STATUS], 'complete')
        self.assertEqual(call_args[NewsFields.DB_META_EARLIEST], '2023-01-01T00:00:00+00:00')
        self.assertEqual(call_args[NewsFields.DB_META_LATEST], '2023-01-31T23:59:59+00:00')

if __name__ == '__main__':
    unittest.main()
