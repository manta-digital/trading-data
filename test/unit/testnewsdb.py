import os
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from dotenv import load_dotenv
import logging
logger = logging.getLogger(__name__)
from pymongo.errors import PyMongoError

from manta_trading.news.newsdb import NewsDB
from manta_trading.news.newsfields import NewsFields
from manta_trading.news.newsutility import NewsUtility
from manta_trading.util.datetimehelper import DateTimeHelper


class TestNewsDB(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_dotenv()
        self.dbname = os.getenv('NEWS_DB_TEST')
        self.host = os.getenv('NEWS_HOST')
        self.newsDb = NewsDB(self.dbname, self.host)
        self.newsDb.db = AsyncMock()
        self.newsDb.connect = AsyncMock()
        self.newsDb.close = AsyncMock()
        self.mockCollection = AsyncMock()
        self.newsDb.db.__getitem__.return_value = self.mockCollection

    async def asyncTearDown(self):
        pass

    def testArticleHash(self):
        newsArticle = {NewsFields.DB_TIME_PUBLISHED: '20230101T0000', NewsFields.DB_SENTIMENT: 0.01234, NewsFields.DB_SUMMARY: 'This is a test article.'}
        hashValue = NewsUtility.generateArticleHash(newsArticle)
        self.assertEqual(hashValue, '42659b6d17231ae3bcf7b401937597aa')

    def testArticleHashInvalid(self):
        newsArticle = {NewsFields.DB_TIMESTAMP: '20230101T0000'}
        hashValue = NewsUtility.generateArticleHash(newsArticle)
        self.assertIsNone(hashValue)

    async def testWriteNewsUpdateMetadata(self):
        # Arrange
        testData = {
            NewsFields.DB_TYPE: NewsFields.DB_UPDATE_TYPE_HISTORICAL,
            NewsFields.DB_META_STATUS: "complete",
            NewsFields.DB_META_EARLIEST: datetime(2020, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_LATEST: datetime(2023, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_COMPLETE: datetime(2023, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_TIMESTAMP: datetime.now(timezone.utc)
        }

        mockCollection = AsyncMock()
        self.newsDb.db.__getitem__.return_value = mockCollection
        mockUpdateResult = MagicMock()
        mockUpdateResult.modified_count = 1
        mockUpdateResult.upserted_id = None
        mockCollection.update_one.return_value = mockUpdateResult

        # Act
        result = await self.newsDb.writeNewsUpdateMetadata(testData)

        # Assert
        self.assertTrue(result, "Expected writeNewsUpdateMetadata to return True")
        mockCollection.update_one.assert_called_once()
        callArgs = mockCollection.update_one.call_args

        logger.info("update_one called with args: %s", callArgs)

        self.assertIn(NewsFields.DB_TYPE, callArgs[0][0])
        self.assertEqual(callArgs[0][0][NewsFields.DB_TYPE], NewsFields.DB_UPDATE_TYPE_HISTORICAL)
        self.assertEqual(callArgs[0][0][NewsFields.DB_API_SOURCE], "ALPHA")

        # Check that all fields in testData are present in the $set operation
        setOperation = callArgs[0][1]['$set']
        for key, value in testData.items():
            self.assertIn(key, setOperation)
            if isinstance(value, datetime):
                self.assertEqual(setOperation[key], DateTimeHelper.toIso8601(value))
            else:
                self.assertEqual(setOperation[key], value)

    async def testReadNewsUpdateMetadata(self):
        # Arrange
        updateType = NewsFields.DB_UPDATE_TYPE_HISTORICAL
        mockData = {
            NewsFields.DB_TYPE: updateType,
            NewsFields.DB_META_STATUS: "complete",
            NewsFields.DB_META_EARLIEST: "20200101T0000",
            NewsFields.DB_META_LATEST: "20230101T0000",
            NewsFields.DB_META_COMPLETE: "20230101T0000",
            NewsFields.DB_TIMESTAMP: "20230601T1200"
        }

        mockCollection = AsyncMock()
        self.newsDb.db.__getitem__.return_value = mockCollection
        mockCollection.find_one.return_value = mockData

        # Convert string dates to datetime with UTC timezone
        expectedEarliest = datetime(2020, 1, 1, 0, 0)
        expectedLatest = datetime(2023, 1, 1, 0, 0)
        expectedComplete = datetime(2023, 1, 1, 0, 0)
        expectedTimestamp = datetime(2023, 6, 1, 12, 0)

        # Act
        result = await self.newsDb.readNewsUpdateMetadata(updateType)

        # Assert
        self.assertIsNotNone(result)
        self.assertEqual(result[NewsFields.DB_TYPE], updateType)
        self.assertEqual(result[NewsFields.DB_META_STATUS], "complete")
        self.assertEqual(result[NewsFields.DB_META_EARLIEST], expectedEarliest)
        self.assertEqual(result[NewsFields.DB_META_LATEST], expectedLatest)
        self.assertEqual(result[NewsFields.DB_META_COMPLETE], expectedComplete)
        self.assertEqual(result[NewsFields.DB_TIMESTAMP], expectedTimestamp)

        mockCollection.find_one.assert_called_once_with({
            NewsFields.DB_TYPE: updateType,
            NewsFields.DB_API_SOURCE: "ALPHA"
        }, projection={NewsFields.DB_ID: False})

    async def testReadNonExistentMetadata(self):
        # Arrange
        updateType = NewsFields.DB_UPDATE_TYPE_HISTORICAL
        mockCollection = AsyncMock()
        self.newsDb.db.__getitem__.return_value = mockCollection
        mockCollection.find_one.return_value = None

        # Act
        result = await self.newsDb.readNewsUpdateMetadata(updateType)

        # Assert
        self.assertIsNone(result)
        mockCollection.find_one.assert_called_once()

    async def testMigrateNewsMetaDataV0(self):
        legacyDocV0 = {
            "api_source": "ALPHA",
            "statusHistoricalUpdate": "incomplete",
            "timestampHistoricalEarliest": "20220228T1946",
            "timestampHistoricalLatest": "20240329T1646",
            "type": "newsLastUpdated"
        }
        migratedDoc = {
            "type": "newsUpdateHistorical",
            "api_source": "ALPHA",
            "status": "incomplete",
            "earliest": "20220228T1946",
            "latest": "20240329T1646",
            "complete": None,
            "timestamp": None
        }
        self.mockCollection.find_one.side_effect = [legacyDocV0, migratedDoc]
        self.mockCollection.update_one.return_value.raw_result = {"nModified": 1}

        result = await self.newsDb.migrateNewsMetaDataV0()
        self.assertEqual(result, migratedDoc)

    async def testMigrateNewsMetaDataV1(self):
        v1Doc = {
            NewsFields.DB_TYPE: "newsLastUpdated",
            NewsFields.DB_API_SOURCE: "ALPHA",
            NewsFields.DB_V1_READ_TIMESTAMP: "20240401T1200",
            NewsFields.DB_V1_READ_COMPLETE: "20240331T2359",
            NewsFields.DB_V1_READ_EARLIEST: "20220101T0000",
            NewsFields.DB_V1_READ_LATEST: "20240331T2359",
            NewsFields.DB_V1_READ_STATUS: "complete"
        }
        oldestNewsItem = {
            NewsFields.DB_TIMESTAMP: datetime.now(timezone.utc) - timedelta(days=3 * 365)
        }
        latestNewsItem = {
            NewsFields.DB_TIMESTAMP: datetime.now(timezone.utc)
        }

        self.mockCollection.find_one.side_effect = [v1Doc, oldestNewsItem, latestNewsItem]
        self.mockCollection.update_one.return_value.raw_result = {"nModified": 1}
        self.mockCollection.delete_one.return_value.raw_result = {"n": 1}

        result = await self.newsDb.migrateNewsMetaDataV1()
        self.assertTrue(result)

        # Simplified assertion to check the presence of necessary keys
        update_calls = self.mockCollection.update_one.call_args_list
        historical_update_call = update_calls[0]
        current_update_call = update_calls[1]

        self.assertEqual(historical_update_call[0][0], {NewsFields.DB_TYPE: "newsUpdateHistorical", NewsFields.DB_API_SOURCE: "ALPHA"})
        self.assertEqual(current_update_call[0][0], {NewsFields.DB_TYPE: "newsUpdateCurrent", NewsFields.DB_API_SOURCE: "ALPHA"})

        historical_update_data = historical_update_call[0][1]["$set"]
        current_update_data = current_update_call[0][1]["$set"]

        self.assertIn(NewsFields.DB_META_STATUS, historical_update_data)
        self.assertIn(NewsFields.DB_META_EARLIEST, historical_update_data)
        self.assertIn(NewsFields.DB_META_LATEST, historical_update_data)
        self.assertIn(NewsFields.DB_META_COMPLETE, historical_update_data)
        self.assertIn(NewsFields.DB_TIMESTAMP, historical_update_data)

        self.assertIn(NewsFields.DB_META_STATUS, current_update_data)
        self.assertIn(NewsFields.DB_META_EARLIEST, current_update_data)
        self.assertIn(NewsFields.DB_META_LATEST, current_update_data)
        self.assertIn(NewsFields.DB_META_COMPLETE, current_update_data)
        self.assertIn(NewsFields.DB_TIMESTAMP, current_update_data)

        self.mockCollection.delete_one.assert_called_once_with({NewsFields.DB_TYPE: "newsLastUpdated"})

    async def testMigrateNewsMetaData(self):
        # Arrange
        self.newsDb.migrateNewsMetaDataV0 = AsyncMock()
        self.newsDb.migrateNewsMetaDataV1 = AsyncMock()
        self.newsDb._validateMetaDataTz = AsyncMock()
        self.newsDb.migrateNewsMetaDataV0.return_value = None
        self.newsDb.migrateNewsMetaDataV1.return_value = True
        self.newsDb._validateMetaDataTz.return_value = True

        # Act
        result = await self.newsDb.migrateNewsMetaData()

        # Assert
        self.assertTrue(result)
        self.newsDb.migrateNewsMetaDataV0.assert_called_once()
        self.newsDb.migrateNewsMetaDataV1.assert_called_once()

    async def testMigrateNewsMetaDataFailureV1(self):
        # Arrange
        self.newsDb.migrateNewsMetaDataV0 = AsyncMock()
        self.newsDb.migrateNewsMetaDataV1 = AsyncMock()
        self.newsDb._validateMetaDataTz = AsyncMock()
        self.newsDb.migrateNewsMetaDataV0.return_value = None
        self.newsDb.migrateNewsMetaDataV1.return_value = False
        self.newsDb._validateMetaDataTz.return_value = False

        # Act
        result = await self.newsDb.migrateNewsMetaData()

        # Assert
        self.assertFalse(result)
        self.newsDb.migrateNewsMetaDataV0.assert_called_once()
        self.newsDb.migrateNewsMetaDataV1.assert_called_once()

    # Note that limit:1 does not appear to work and instead keeps default limit of 50.
    # in fact nothing other than 50 (default) or 1000 appears to have any effect.
    async def testWriteNewsArticle(self):
        # Create a mock object to simulate the behavior of the api.getNewsSentiment() method.
        mock_items = [
            {NewsFields.DB_TIME_PUBLISHED: '20230101T0000', NewsFields.DB_SENTIMENT: 0.01234, NewsFields.DB_SUMMARY: 'This is a test article.'},
            {NewsFields.DB_TIME_PUBLISHED: '20230101T0100', NewsFields.DB_SENTIMENT: 0.31234, NewsFields.DB_SUMMARY: 'This is another test article.'},
        ]
        news = {
            NewsFields.DB_NEWSITEMS: mock_items
        }

        # With a NewsDB context manager, write the first news item from the news object to the database.
        async with NewsDB(self.host, self.dbname) as db:
            # Access the first news item from the news object.
            result = await db.writeNews(news)
            self.assertTrue(len(result.inserted_ids) == len(mock_items))

            # Delete the first news item from the database.
            # if we want to delete this we need to know the article hash.  an overload would be useful.
            for item in mock_items:
                article_hash = NewsUtility.generateArticleHash(item)
                result = await db.deleteNews(article_hash)
                self.assertEqual(result.deleted_count, 1)

    async def testWriteHandleDuplicateArticle(self):
        mock_items = [
            {NewsFields.DB_TIME_PUBLISHED: '20230101T0000', NewsFields.DB_SENTIMENT: 0.01234, NewsFields.DB_SUMMARY: 'This is a test article.'},
            {NewsFields.DB_TIME_PUBLISHED: '20230101T0000', NewsFields.DB_SENTIMENT: 0.01234, NewsFields.DB_SUMMARY: 'This is a test article.'},
        ]
        news = {
            NewsFields.DB_NEWSITEMS: mock_items
        }
        self.assertTrue(len(news) > 0)

        async with NewsDB(self.host, self.dbname) as db:
            article = news[NewsFields.DB_NEWSITEMS][0]
            result = await db.writeNews(article)
            self.assertTrue(result.matched_count == 1 or result.upserted_count == 1)

            result = await db.writeNews(article)
            self.assertEqual(result.matched_count, 1)
            self.assertEqual(result.upserted_count, 0)

            await db.deleteNews(article[NewsFields.DB_ID])
            self.assertEqual(result.deleted_count, 1)

    async def testWriteReturnsNoneForEmptyList(self):
        async with NewsDB(self.host, self.dbname) as db:
            result = await db.writeNews([])
            self.assertIsNone(result)

    async def testReadNewsSentiment(self):
        async with NewsDB(self.host, self.dbname) as db:
            result = await db.readNewsSentiment(symbols=['TSLA'], earliest='2024-01-01', batchSize=50)
            self.assertTrue(len(result) == 50)

    async def _clearMetadata(self):
        async with NewsDB(self.host, self.dbname) as db:
            await db.db[NewsFields.DB_COLL_META].delete_many({})

    # No longer a test but this will write a V1 (not v0) legacy update record.
    async def _writeLegacyUpdate(self):
        async with NewsDB(self.host, self.dbname) as db:
            result = await self._writeLastUpdated({
                NewsFields.DB_API_SOURCE: 'ALPHA',
                NewsFields.DB_V1_READ_TIMESTAMP: datetime.now(timezone.utc).strftime("%Y%m%dT%H%M"),
                NewsFields.DB_V1_READ_STATUS: 'incomplete',
                NewsFields.DB_V1_READ_LATEST: datetime.now(timezone.utc).strftime("%Y%m%dT%H%M"),
                NewsFields.DB_V1_READ_EARLIEST: '20230101T0000',
            })

            self.assertTrue(result.modified_count == 1 or result.matched_count == 1)

    async def _writeLastUpdated(self, updateStatus):
        async with NewsDB(self.host, self.dbname) as db:
            try:
                source = updateStatus.get(NewsFields.DB_API_SOURCE, 'ALPHA')
                document = {
                    NewsFields.DB_TYPE: NewsFields.DB_META_LAST_UPDATED,
                    NewsFields.DB_API_SOURCE: source,
                }

                updateFields = [
                    NewsFields.DB_TIMESTAMP,
                    NewsFields.DB_V1_READ_COMPLETE,
                    NewsFields.DB_V1_READ_EARLIEST,
                    NewsFields.DB_V1_READ_LATEST,
                    NewsFields.DB_V1_READ_TARGET,
                    NewsFields.DB_V1_READ_STATUS,
                ]

                # Include fields from params that are also in updateFields
                for field in updateFields:
                    if field in updateStatus:
                        document[field] = updateStatus[field]

                result = await db[NewsFields.DB_COLL_META].update_one(
                    {
                        NewsFields.DB_TYPE: NewsFields.DB_META_LAST_UPDATED,
                        NewsFields.DB_API_SOURCE: source,
                    },
                    {"$set": document},
                    upsert=True
                )

            except PyMongoError as e:
                logger.error("An error occurred when accessing the database: %s", e)
                return None

            return result


if __name__ == '__main__':
    unittest.main()
