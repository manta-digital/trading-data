import os
import re
import traceback

import pytz
import time

from dotenv import load_dotenv
from manta_trading.logging import get_logger
from pymongo import MongoClient, UpdateOne

_logger = get_logger(__name__)
from datetime import datetime


class NewsDbMigrationUtility:
    @staticmethod
    def convertAlphaVantageToIso8601(av_time):
        # Parse AlphaVantage format (assumed to be in US/Eastern time)
        eastern = pytz.timezone('US/Eastern')
        dt = eastern.localize(datetime.strptime(av_time, "%Y%m%dT%H%M%S"))

        # Convert to UTC and format as ISO-8601
        utc_dt = dt.astimezone(pytz.UTC)
        return utc_dt.isoformat()

    @staticmethod
    def fixBrokenIsoFormat(iso_time):
        # Parse the broken ISO format string
        dt = datetime.strptime(iso_time, "%Y-%m-%dT%H:%M:%S.%fZ")

        # Localize to UTC
        utc_dt = pytz.UTC.localize(dt)

        # Return the ISO-8601 formatted string
        return utc_dt.isoformat()

    @staticmethod
    def migrateTimePublishedBatch(collection, batchSize=1000):
        query = {
            "$or": [
                {"time_published": {"$type": "date"}},  # Documents with datetime.datetime
                {"time_published": {"$regex": r"^\d{8}T\d{6}$"}}  # Documents with AlphaVantage format
            ]
        }

        cursor = collection.find(query).limit(batchSize)
        batch = list(cursor)

        if not batch:
            return 0, []  # No more documents to update

        bulk_ops = []
        error_docs = []
        for doc in batch:
            try:
                time_published = doc['time_published']

                # Handle datetime.datetime objects
                if isinstance(time_published, datetime):
                    iso_time = time_published.isoformat()

                # Handle AlphaVantage string format
                elif isinstance(time_published, str) and re.match(r"^\d{8}T\d{6}$", time_published):
                    iso_time = NewsDbMigrationUtility.convertAlphaVantageToIso8601(time_published)

                else:
                    # Skip documents that do not match the known patterns
                    continue

                bulk_ops.append(
                    UpdateOne(
                        {'_id': doc['_id']},
                        {'$set': {'time_published': iso_time}}
                    )
                )
            except ValueError as e:
                error_docs.append((doc['_id'], doc['time_published'], str(e)))

        if bulk_ops:
            result = collection.bulk_write(bulk_ops)
            return result.modified_count, error_docs
        else:
            return 0, error_docs

# this is terrible code that should never ever happen, and it is good we found it now.
# the DB environment variable is hard coded to a production database.  since we do not have
# separate servers yet, it is very easy to make unintended changes here.  In fact that is
# probably happening as I type this.  These should be set in an environment, and tests should
# always use a test environment.  There should be no default environment, and it should be an
# error not to provide an environment.  That way things are explicit.

    @staticmethod
    def runTimestampMigration():
        load_dotenv()

        client = MongoClient(os.getenv('NEWS_HOST'))
        db = client[os.getenv('NEWS_DB')]
        collection = db['news']

        _logger.info("Starting timestamp migration process...")

        try:
            total_updated = 0
            batch_count = 0
            start_time = time.time()
            all_error_docs = []

            while True:
                updated_count, error_docs = NewsDbMigrationUtility.migrateTimePublishedBatch(collection)
                if updated_count == 0 and not error_docs:
                    break

                total_updated += updated_count
                batch_count += 1
                all_error_docs.extend(error_docs)
                elapsed_time = time.time() - start_time

                _logger.info(
                    "Batch %s completed. Updated %s documents in this batch. "
                    "Errors: %s. Total updated: %s. Elapsed time: %.2f seconds",
                    batch_count, updated_count, len(error_docs), total_updated, elapsed_time
                )

            _logger.info("Migration complete. Total documents updated: %s", total_updated)
            _logger.info("Total batches: %s", batch_count)
            _logger.info("Total elapsed time: %.2f seconds", time.time() - start_time)

            if all_error_docs:
                _logger.warning("Total documents with errors: %s", len(all_error_docs))
                for doc_id, time_published, error_msg in all_error_docs:
                    _logger.warning("Error in document %s: time_published '%s' - %s", doc_id, time_published, error_msg)

        except Exception as e:
            _logger.error("An error occurred during migration: %s", str(e))
            _logger.error("%s", traceback.format_exc())

        finally:
            client.close()
            _logger.info("Database connection closed.")


def main():
    NewsDbMigrationUtility.runTimestampMigration()


if __name__ == "__main__":
    main()
