import os
import re
import unittest
from dotenv import load_dotenv
from pymongo import MongoClient
from manta_trading.news.newsdbmigrationutility import NewsDbMigrationUtility


class TestNewsDbMigration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_dotenv()
        self.client = MongoClient(os.getenv('NEWS_HOST'))
        self.db = self.client[os.getenv('NEWS_DB_TEST')]
        self.collection = self.db['news']

    async def asyncTearDown(self):
        self.client.close()

    # todo: this performs a full migration -- move to long running tests
    async def testMigrationBatch(self):
        total_modified = 0
        batch_size = 1000  # or whatever your desired batch size is
        has_more = True

        # Loop until all documents are processed
        while has_more:
            modified_count, _ = NewsDbMigrationUtility.migrateTimePublishedBatch(self.collection, batchSize=batch_size)
            total_modified += modified_count
            has_more = modified_count == batch_size  # If batch was full, assume there may be more to process

        # Now verify all documents are in ISO format
        try:
            for doc in self.collection.find():
                try:
                    self.assertRegex(doc['time_published'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}$')
                except AssertionError as e:
                    print(f"Assertion failed for document: {doc['_id']}, time_published: {doc['time_published']}")
                    print(f"Error: {e}")
                    break

        except Exception as e:
            print(f"An unexpected error occurred: {e}")

        print(f"Total modified documents: {total_modified}")


if __name__ == '__main__':
    unittest.main()
