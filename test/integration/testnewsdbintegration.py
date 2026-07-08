import os
import pymongo
import unittest
from datetime import datetime, timezone

from bson import ObjectId
from dotenv import load_dotenv
import logging
logger = logging.getLogger(__name__)
from pymongo import MongoClient, ReadPreference

from manta_trading.news.newsdb import NewsDB
from manta_trading.news.newsfields import NewsFields


class TestNewsDBIntegration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv()
        cls.dbname = os.getenv('NEWS_DB_TEST')
        cls.host = os.getenv('NEWS_HOST')

    async def asyncSetUp(self):
        self.newsDb = NewsDB(self.host, self.dbname)
        await self.newsDb.connect()

    async def asyncTearDown(self):
        await self.newsDb.close()

    async def testConnectAndClose(self):
        self.assertTrue(self.newsDb.isConnected())
        await self.newsDb.close()
        self.assertFalse(self.newsDb.isConnected())

    async def testCreateNewsDatabase(self):
        result = await self.newsDb.createNewsDatabase()
        self.assertTrue(result)

    async def testMigrateNewsMetaData(self):
        async with self.newsDb:  # This will ensure the connection is open and closed properly
            try:
                self.assertIsNotNone(self.newsDb.db, "Database connection failed")

                # First, ensure we have a v1 document to migrate
                v1Doc = {
                    NewsFields.DB_TYPE: "newsLastUpdated",
                    NewsFields.DB_API_SOURCE: "ALPHA",
                    "readStatus": "incomplete",
                    "readStatusEarliest": "20240724T0317",
                    "readStatusLatest": "20240724T1505",
                    "readStatusComplete": None,
                    "readStatusTarget": "20240714T0435",
                    "time_published": "20240724T1506"
                }
                await self.newsDb.db[NewsFields.DB_COLL_META].update_one(
                    {NewsFields.DB_TYPE: "newsLastUpdated", NewsFields.DB_API_SOURCE: "ALPHA"},
                    {"$set": v1Doc},
                    upsert=True
                )

                # Add some sample news items
                sampleNews = [
                    {NewsFields.DB_TIMESTAMP: "20240101T000000"},
                    {NewsFields.DB_TIMESTAMP: "20240724T150500"}
                ]
                await self.newsDb.db[NewsFields.DB_COLL_NEWS].insert_many(sampleNews)

                # Perform migration
                result = await self.newsDb.migrateNewsMetaData()
                self.assertTrue(result)

                # Verify migration results
                historicalDoc = await self.newsDb.db[NewsFields.DB_COLL_META].find_one({
                    NewsFields.DB_TYPE: "newsUpdateHistorical",
                    NewsFields.DB_API_SOURCE: "ALPHA"
                })
                currentDoc = await self.newsDb.db[NewsFields.DB_COLL_META].find_one({
                    NewsFields.DB_TYPE: "newsUpdateCurrent",
                    NewsFields.DB_API_SOURCE: "ALPHA"
                })

                if historicalDoc and currentDoc:
                    self.assertEqual(historicalDoc[NewsFields.DB_META_STATUS], "incomplete")
                    self.assertEqual(currentDoc[NewsFields.DB_META_STATUS], "incomplete")

                    # Check that the old document was deleted
                    oldDoc = await self.newsDb.db[NewsFields.DB_COLL_META].find_one({
                        NewsFields.DB_TYPE: "newsLastUpdated",
                        NewsFields.DB_API_SOURCE: "ALPHA"
                    })
                    self.assertIsNone(oldDoc)
                else:
                    # If no migration was needed, the original document should still exist
                    oldDoc = await self.newsDb.db[NewsFields.DB_COLL_META].find_one({
                        NewsFields.DB_TYPE: "newsLastUpdated",
                        NewsFields.DB_API_SOURCE: "ALPHA"
                    })
                    self.assertIsNotNone(oldDoc)

            except Exception as e:
                self.fail(f"Test failed with exception: {str(e)}")

            finally:
                # Clean up
                await self.newsDb.db[NewsFields.DB_COLL_META].delete_many({})
                await self.newsDb.db[NewsFields.DB_COLL_NEWS].delete_many({})

    async def testWriteAndReadNewsUpdateMetadata(self):
        testData = {
            NewsFields.DB_TYPE: NewsFields.DB_UPDATE_TYPE_HISTORICAL,
            NewsFields.DB_META_STATUS: "complete",
            NewsFields.DB_META_EARLIEST: datetime(2020, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_LATEST: datetime(2023, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_TARGET: datetime(2020, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_COMPLETE: datetime(2023, 1, 1, tzinfo=timezone.utc),
            NewsFields.DB_META_TIMESTAMP: datetime.now(timezone.utc)
        }

        writeResult = await self.newsDb.writeNewsUpdateMetadata(testData)
        self.assertTrue(writeResult)

        readResult = await self.newsDb.readNewsUpdateMetadata(NewsFields.DB_UPDATE_TYPE_HISTORICAL)
        self.assertIsNotNone(readResult)
        self.assertEqual(readResult[NewsFields.DB_META_STATUS], "complete")
        self.assertEqual(readResult[NewsFields.DB_META_EARLIEST], testData[NewsFields.DB_META_EARLIEST])
        self.assertEqual(readResult[NewsFields.DB_META_LATEST], testData[NewsFields.DB_META_LATEST])
        self.assertEqual(readResult[NewsFields.DB_META_TARGET], testData[NewsFields.DB_META_TARGET])
        self.assertEqual(readResult[NewsFields.DB_META_COMPLETE], testData[NewsFields.DB_META_COMPLETE])

    # Additional methods that can be run to diagnose database errors.
    def _testDirectDatabaseQuery(self):
        client = MongoClient(self.newsDb.mongoUri, read_preference=ReadPreference.PRIMARY)
        db = client[self.newsDb.dbname]
        news_collection = db['news']

        # Log initial count
        initial_count = news_collection.count_documents({})
        logger.debug("Initial count of documents in news: %d", initial_count)

        # Insert a test document
        test_doc = {
            "_id": ObjectId(),
            "title": "Test Document",
            "content": "This is a test document.",
            "timestamp": datetime.now()
        }
        insert_result = news_collection.insert_one(test_doc)
        logger.debug("Inserted document ID: %s", insert_result.inserted_id)

        # Verify the document was inserted
        new_count = news_collection.count_documents({})
        logger.debug("Count after insertion: %d", new_count)

        # Try to retrieve the inserted document
        retrieved_doc = news_collection.find_one({"_id": insert_result.inserted_id})
        if retrieved_doc:
            logger.debug("Retrieved test document: %s", retrieved_doc)
        else:
            logger.warning("Failed to retrieve the inserted test document")

        # Delete the test document
        delete_result = news_collection.delete_one({"_id": insert_result.inserted_id})
        logger.debug("Deleted %d document(s)", delete_result.deleted_count)

        # Verify the document was deleted
        final_count = news_collection.count_documents({})
        logger.debug("Final count of documents in news: %d", final_count)

        # Try distinct query without limit
        try:
            distinct_result = news_collection.distinct('_id')
            logger.debug("Distinct _ids: %s", distinct_result)
        except Exception as e:
            logger.error("Error in distinct query: %s", str(e))

        client.close()

    def _testDirectPyMongoConnection(self):
        client = MongoClient(self.newsDb.mongoUri)
        db = client[self.newsDb.dbname]
        count = db["news"].count_documents({})
        logger.debug("PyMongo direct count: %d", count)
        client.close()

    def _testCheckAllDatabases(self):
        client = MongoClient(self.newsDb.mongoUri, read_preference=ReadPreference.PRIMARY)

        logger.debug("Checking all databases on the server")

        for db_name in client.list_database_names():
            db = client[db_name]
            logger.debug("Database: %s", db_name)

            for collection_name in db.list_collection_names():
                collection = db[collection_name]
                count = collection.count_documents({})
                logger.debug("  Collection '%s' contains %d documents", collection_name, count)

                if count > 0:
                    sample_doc = collection.find_one()
                    logger.debug("  Sample document from '%s': %s", collection_name, sample_doc)

        client.close()

    async def _testVerifyDatabaseState(self):
        try:
            await self.newsDb.connect()
            logger.debug("Connected to database for debugging. DB name: %s", self.newsDb.db.name)
            logger.debug("Database URI: %s", self.newsDb.mongoUri)
            logger.debug("Database name: %s", self.newsDb.dbname)

            user_info = await self.newsDb.db.command("connectionStatus")
            logger.debug("User authentication info: %s", user_info)

            server_info = await self.newsDb.db.command("serverStatus")
            logger.debug("MongoDB server version: %s", server_info['version'])
            logger.debug("PyMongo version: %s", pymongo.__version__)
            #logger.debug("Motor version: %s", motor.__version__)

            # List all collections
            collections = await self.newsDb.db.list_collection_names()
            logger.debug("Collections in the database: %s", collections)
            exact_coll_name = await self.newsDb.db.command("listCollections", filter={"name": NewsFields.DB_COLL_NEWS})
            logger.debug("Exact collection info: %s", exact_coll_name)

            db_stats = await self.newsDb.db.command("dbStats")
            logger.debug("Database stats: %s", db_stats)

            pipeline = [{"$match": {}}, {"$limit": 1}]
            result = await self.newsDb.db[NewsFields.DB_COLL_NEWS].aggregate(pipeline).to_list(length=None)
            logger.debug("Aggregation result: %s", result)

            # Check if news collection exists
            if NewsFields.DB_COLL_NEWS in collections:
                coll_stats = await self.newsDb.db.command("collStats", NewsFields.DB_COLL_NEWS)
                logger.debug("Collection stats: %s", coll_stats)

                # Count documents in the news collection
                count = await self.newsDb.db[NewsFields.DB_COLL_NEWS].count_documents({})
                logger.debug("Number of documents in %s: %d", NewsFields.DB_COLL_NEWS, count)

                # Try a synchronous count as well
                sync_count = self.newsDb.db[NewsFields.DB_COLL_NEWS].count_documents({})
                logger.debug("Synchronous count of documents in %s: %d", NewsFields.DB_COLL_NEWS, sync_count)

                # Get a sample of documents from the news collection
                sample_docs = await self.newsDb.db[NewsFields.DB_COLL_NEWS].find().limit(5).to_list(length=5)
                logger.debug("Sample documents from %s:", NewsFields.DB_COLL_NEWS)
                for doc in sample_docs:
                    logger.debug("%s", doc)

                # Check the keys of the first document
                if sample_docs:
                    logger.debug("Keys in the first document: %s", list(sample_docs[0].keys()))
                else:
                    logger.warning("No documents found in %s", NewsFields.DB_COLL_NEWS)

                # Try a distinct query
                distinct_ids = await self.newsDb.db[NewsFields.DB_COLL_NEWS].distinct('_id', limit=5)
                logger.debug("Distinct _ids (up to 5): %s", distinct_ids)

                # check for any filters or views affecting the result:
                pipeline = [{"$match": {}}, {"$limit": 1}]
                result = await self.newsDb.db[NewsFields.DB_COLL_NEWS].aggregate(pipeline).to_list(length=None)
                logger.debug("Aggregation result: %s", result)

            else:
                logger.error("%s collection does not exist", NewsFields.DB_COLL_NEWS)

            # Log connection details (be careful not to log sensitive information)
            logger.debug("Database connection details: Host: %s, Port: %s, DB: %s", self.newsDb.host, self.newsDb.port, self.dbname)

        except Exception as e:
            logger.error("Error during database debugging: %s", e)
            logger.exception("Full exception details:")
        finally:
            await self.newsDb.close()


if __name__ == '__main__':
    unittest.main()
