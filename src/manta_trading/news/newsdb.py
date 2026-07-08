import copy
import os
import hashlib
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from datetime import datetime, timedelta, timezone
from manta_trading.logging import get_logger
from dotenv import load_dotenv

_logger = get_logger(__name__)
from pymongo.errors import BulkWriteError, PyMongoError, ServerSelectionTimeoutError

from manta_trading.news.newsfields import NewsFields
from manta_trading.news.newsutility import NewsUtility
from manta_trading.util.datetimehelper import DateTimeHelper


# News DB.
class NewsDB:

    def __init__(self, _host, _dbname, _user=None, _password=None, _batchSize=1000, _port=27017):
        self.dbname = _dbname
        self.user = _user
        self.password = _password
        self.host = _host
        self.port = _port
        self.batchSize = _batchSize
        self.mongoUri = self.getUri()
        self.client = None
        self.db = None
        self.timeFormat = "%Y%m%dT%H%M"

    # In general the MongoClient will manage connections.
    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
        return self

    def getUri(self):
        return f"mongodb://{self.host}/{self.dbname}?retryWrites=true&w=majority"

    async def connect(self):
        try:
            if self.client is None:
                self.client = AsyncIOMotorClient(self.mongoUri)
                self.db = self.client[self.dbname]

        except Exception as error:
            self.client = None
            self.db = None
            print(error)

    # In general you don't need to call this explicitly, and it is intentionally
    # not called on __exit__.
    async def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None
            self.db = None

    def isConnected(self):
        return self.client is not None and self.db is not None

    async def readNews(self, earliest=None, latest=None, tickers=None, topics=None, sentiment=None, apiSource=None, batchSize=None, page=1):
        """
        Read news from the database.  Return in batches controlled by batchSize and page.

        :param earliest: earliest timestamp in DB format, may be None
        :param latest: latest timestamp in Db format, uses now if None
        :param tickers: array of tickers to filter on.
        :param topics:
        :param sentiment:
        :param apiSource: currently only 'ALPHA' is supported
        :param batchSize: batch size.  only 50 and 1000 are relevant for AlphaVantage
        :param page:
        :return:

        """
        if batchSize is None:
            batchSize = self.batchSize

        if page < 1 or batchSize < 1:
            return None

        query = {}
        if earliest:
            query[NewsFields.DB_TIMESTAMP] = {"$gte": earliest}
        if latest:
            query[NewsFields.DB_TIMESTAMP] = query.get(NewsFields.DB_TIMESTAMP, {})
            query[NewsFields.DB_TIMESTAMP]["$lte"] = latest
        if tickers:
            query[NewsFields.DB_TICKER] = {"$in": tickers}
        if topics:
            query[NewsFields.DB_TOPIC] = {"$in": topics}
        if sentiment:
            query[NewsFields.DB_SENTIMENT] = sentiment
        if apiSource:
            query[NewsFields.DB_API_SOURCE] = apiSource

        skip = (page - 1) * batchSize
        news = self.db[NewsFields.DB_COLL_NEWS]

        # for large result sets may want to change this to async for:
        cursor = news.find(query).skip(skip).limit(batchSize)
        return await cursor.to_list(length=None)

    async def readNewsSentiment(self, symbols=None, earliest=None, latest=None, batchSize=None, page=1):
        """
        Read news from the database. Return in batches controlled by batchSize and page.

        :param symbols: array of tickers to filter on.
        :param earliest: start date for filtering news.
        :param latest: end date for filtering news.
        :param batchSize: number of records per batch.
        :param page: page number for pagination.
        """

        # Convert single ticker to a list if it's not a list
        if symbols is None:
            symbols = []
        if isinstance(symbols, str):
            symbols = [symbols]

        matchConditions = {'ticker_sentiment.ticker': {'$in': symbols}}

        if earliest:
            matchConditions['time_published'] = {'$gte': earliest}
        if latest:
            if 'time_published' in matchConditions:
                matchConditions['time_published']['$lte'] = latest
            else:
                matchConditions['time_published'] = {'$lte': latest}

        pipeline = [
            {'$match': matchConditions},
            {'$sort': {'time_published': 1}},
            {
                '$project': {
                    '_id': 1,
                    'title': 1,
                    'summary': 1,
                    'source': 1,
                    'time_published': 1,
                    'tickerSentiment': {
                        '$arrayElemAt': [
                            {
                                '$filter': {
                                    'input': '$ticker_sentiment',
                                    'as': 'ticker',
                                    'cond': {'$in': ['$$ticker.ticker', symbols]}
                                }
                            },
                            0
                        ]
                    }
                }
            },
            {
                '$project': {
                    '_id': 1,
                    'title': 1,
                    'source': 1,
                    'time_published': 1,
                    'relevance_score': '$tickerSentiment.relevance_score',
                    'ticker_sentiment_score': '$tickerSentiment.ticker_sentiment_score'
                }
            },
        ]
        # Conditionally add the $limit stage if batchSize is not None
        if batchSize is not None:
            skip = (page - 1) * batchSize

            pipeline.append({'$skip': skip})
            pipeline.append({'$limit': batchSize})

        news = self.db[NewsFields.DB_COLL_NEWS]
        cursor = news.aggregate(pipeline)
        result = await cursor.to_list(length=None)

        # Conversion back to ISO-8601 strings in the result not needed when db format is ISO-8601.
        # for item in result:
        #     item['time_published'] = DateTimeHelper.toIso8601(item['time_published'])

        return result

    # Write news items to the database.  Expects newsItems to bee contained in the DB_NEWSITEMS field.
    # Uses ordered=False to avoid failing on duplicates.
    async def writeNews(self, news):

        def _getNewsItems(news):
            if news is not None and NewsFields.DB_NEWSITEMS in news:
                return news[NewsFields.DB_NEWSITEMS]
            elif NewsFields.DB_SUMMARY in news and NewsFields.DB_TIMESTAMP and NewsFields.DB_TICKER in news:
                return [news]
            else:
                return None

        def _assignIdsToItems(collection):
            for item in collection:
                if NewsFields.DB_ID not in item:
                    item[NewsFields.DB_ID] = NewsUtility.generateArticleHash(item)
            return collection

        newsItems = _getNewsItems(news)
        if newsItems is None or len(newsItems) == 0:
            return None

        await self.connect()
        newsCollection = self.db[NewsFields.DB_COLL_NEWS]

        try:
            newsItems = _assignIdsToItems(newsItems)
            return await newsCollection.insert_many(newsItems, ordered=False)

        except BulkWriteError as error:
            errors = error.details.get('writeErrors', [])
            for e in errors:
                error_code = e.get('code')
                if error_code != 11000:  # Duplicate key error
                    _logger.error("Error code: %s, Error message: %s", error_code, e.get('errmsg'))
            return error.details

    # Delete news from the database by id (hash value).
    async def deleteNews(self, id):
        newsCollection = self.db[NewsFields.DB_COLL_NEWS]
        result = await newsCollection.delete_one({NewsFields.DB_ID: id})
        return result

    # Migrate old to new status metadata.
    # actual data, legacy version: [
    #   {
    #     "_id": {"$oid": "6606f0693953844c785667e9"},
    #     "api_source": "ALPHA",
    #     "statusHistoricalUpdate": "incomplete",
    #     "timestampHistoricalEarliest": "20220228T1946",
    #     "timestampHistoricalLatest": "20240329T1646",
    #     "type": "newsLastUpdated"
    #   }
    # ]

    async def createNewsDatabase(self):
        try:
            await self.connect()
            await self.db[NewsFields.DB_COLL_META].create_index([("type", 1), ("api_source", 1)], unique=True)

            # Note setOnInsert to prevent overwriting existing records.
            for update_type in ["newsUpdateHistorical", "newsUpdateCurrent"]:
                await self.db[NewsFields.DB_COLL_META].update_one(
                    {NewsFields.DB_TYPE: update_type, NewsFields.DB_API_SOURCE: "ALPHA"},
                    {"$setOnInsert": {
                        NewsFields.DB_META_STATUS: "incomplete",
                        NewsFields.DB_META_EARLIEST: None,
                        NewsFields.DB_META_LATEST: None,
                        NewsFields.DB_META_COMPLETE: None,
                        NewsFields.DB_TIMESTAMP: None
                    }},
                    upsert=True
                )
            return True
        except Exception as e:
            _logger.error("Error creating news database: %s", e)
            return False
        finally:
            await self.close()

    async def migrateNewsMetaDataV0(self):
        try:
            await self.connect()

            legacy_doc = await self.db[NewsFields.DB_COLL_META].find_one({
                NewsFields.DB_TYPE: NewsFields.DB_META_LAST_UPDATED,
                NewsFields.DB_API_SOURCE: 'ALPHA'
            })
            _logger.debug("Legacy doc before update: %s", legacy_doc)

            legacy_field_mapping = {
                'timestampHistoricalLatest': NewsFields.DB_V1_READ_LATEST,
                'timestampHistoricalEarliest': NewsFields.DB_V1_READ_EARLIEST,
                'timestampHistoricalComplete': NewsFields.DB_V1_READ_COMPLETE,
                'statusHistoricalUpdate': NewsFields.DB_V1_READ_STATUS,
            }

            if legacy_doc and any(field in legacy_doc for field in legacy_field_mapping):
                update_fields = {}
                for legacy_field, new_field in legacy_field_mapping.items():
                    if legacy_field in legacy_doc:
                        update_fields[new_field] = legacy_doc[legacy_field]

                if update_fields:
                    update_fields[NewsFields.DB_V1_READ_TARGET] = None
                    _logger.debug("Update fields: %s", update_fields)
                    await self.db[NewsFields.DB_COLL_META].update_one(
                        {NewsFields.DB_TYPE: NewsFields.DB_META_LAST_UPDATED, NewsFields.DB_API_SOURCE: 'ALPHA'},
                        {'$set': update_fields, '$unset': {key: "" for key in legacy_field_mapping.keys() if key in legacy_doc}}
                    )
                    _logger.info("Legacy metadata migrated successfully.")

                migrated_doc = await self.db[NewsFields.DB_COLL_META].find_one({
                    NewsFields.DB_TYPE: NewsFields.DB_META_LAST_UPDATED,
                    NewsFields.DB_API_SOURCE: 'ALPHA',
                })

                _logger.debug("Migrated doc after update: %s", migrated_doc)
                return migrated_doc

            _logger.debug("No V0 legacy document found to migrate.  Returning as-is")
            return legacy_doc

        except Exception as e:
            _logger.error("Error migrating news database from v0: %s", e)
            return None
        finally:
            await self.close()

    async def migrateNewsMetaDataV1(self):
        try:
            await self.connect()

            # Look for v1 document
            v1Doc = await self.db[NewsFields.DB_COLL_META].find_one({
                NewsFields.DB_TYPE: "newsLastUpdated",
                NewsFields.DB_API_SOURCE: 'ALPHA'
            })
            _logger.debug("V1 doc before migration: %s", v1Doc)

            if v1Doc:
                # Query the news data to find the oldest and latest time_published
                oldestNewsItem = await self.db[NewsFields.DB_COLL_NEWS].find_one(
                    {}, sort=[(NewsFields.DB_TIMESTAMP, 1)], projection={NewsFields.DB_TIMESTAMP: 1}
                )
                latestNewsItem = await self.db[NewsFields.DB_COLL_NEWS].find_one(
                    {}, sort=[(NewsFields.DB_TIMESTAMP, -1)], projection={NewsFields.DB_TIMESTAMP: 1}
                )

                if oldestNewsItem and latestNewsItem:
                    oldestTime = DateTimeHelper.parseTimestampAsDatetime(oldestNewsItem[NewsFields.DB_TIMESTAMP])
                    latestTime = DateTimeHelper.parseTimestampAsDatetime(latestNewsItem[NewsFields.DB_TIMESTAMP])
                    currentTime = datetime.now(timezone.utc)
                    twoYearsAgo = currentTime - timedelta(days=2 * 365)

                    _logger.debug("Oldest time: %s", oldestTime)
                    _logger.debug("Latest time: %s", latestTime)
                    _logger.debug("Current time: %s", currentTime)
                    _logger.debug("Two years ago: %s", twoYearsAgo)

                    # Ensure all datetimes are timezone-aware
                    oldestTime = oldestTime.replace(tzinfo=timezone.utc) if oldestTime.tzinfo is None else oldestTime
                    latestTime = latestTime.replace(tzinfo=timezone.utc) if latestTime.tzinfo is None else latestTime

                    status = "complete" if oldestTime < twoYearsAgo else "incomplete"

                    historicalRecord = {
                        NewsFields.DB_TYPE: "newsUpdateHistorical",
                        NewsFields.DB_API_SOURCE: "ALPHA",
                        NewsFields.DB_META_STATUS: status,
                        NewsFields.DB_META_EARLIEST: oldestTime,
                        NewsFields.DB_META_LATEST: latestTime,
                        NewsFields.DB_META_COMPLETE: latestTime if status == "complete" else None,
                        NewsFields.DB_TIMESTAMP: currentTime
                    }

                    currentRecord = {
                        NewsFields.DB_TYPE: "newsUpdateCurrent",
                        NewsFields.DB_API_SOURCE: "ALPHA",
                        NewsFields.DB_META_STATUS: "incomplete",
                        NewsFields.DB_META_EARLIEST: None,
                        NewsFields.DB_META_LATEST: None,
                        NewsFields.DB_META_COMPLETE: None,
                        NewsFields.DB_TIMESTAMP: currentTime
                    }

                    _logger.debug("Historical record to be inserted: %s", historicalRecord)
                    _logger.debug("Current record to be inserted: %s", currentRecord)

                    await self.db[NewsFields.DB_COLL_META].update_one(
                        {NewsFields.DB_TYPE: "newsUpdateHistorical", NewsFields.DB_API_SOURCE: "ALPHA"},
                        {"$set": historicalRecord},
                        upsert=True
                    )

                    await self.db[NewsFields.DB_COLL_META].update_one(
                        {NewsFields.DB_TYPE: "newsUpdateCurrent", NewsFields.DB_API_SOURCE: "ALPHA"},
                        {"$set": currentRecord},
                        upsert=True
                    )

                    await self.db[NewsFields.DB_COLL_META].delete_one({NewsFields.DB_TYPE: "newsLastUpdated"})
                    _logger.info("Migration to new schema completed successfully.")
                    return True
                else:
                    _logger.error("Could not find news items to determine historical metadata.")
                    return False
            else:
                _logger.info("No v1 document found. Migration not needed.")
                return True

        except Exception as e:
            _logger.error("Error migrating news database from v1: %s", str(e))
            _logger.exception("Full exception details:")
            return False
        finally:
            await self.close()

    async def migrateNewsMetaData(self):
        """
            Migrate the news database to the new schema. This function first checks for and migrates really old metadata,
            then proceeds with migration to the new schema.

            :param self: Instance of the NewsDB class.
            :return: A boolean value indicating whether the migration was successful.

            The function uses the MongoClient to connect to the database and perform the necessary operations.
            It first checks if there is any legacy metadata present in the database. If so, it updates the legacy metadata to the new schema.
            Then, it proceeds with the migration to the new schema. If the migration is successful, it returns True; otherwise, it returns False.
            """
        try:
            await self.connect()

            # Migrate from v0 to v1 if needed
            await self.migrateNewsMetaDataV0()

            # Migrate from v1 to v2
            await self.migrateNewsMetaDataV1()

            # ensure metadata has property timezone information
            result = await self._validateMetaDataTz()

            return result

        except Exception as e:
            _logger.error("Error migrating news database: %s", e)
            return False
        finally:
            await self.close()

    # Now defaults to historical during migration to a single type.
    async def readNewsUpdateMetadata(self, updateType=NewsFields.DB_UPDATE_TYPE_HISTORICAL):
        try:
            await self.connect()
            document = await self.db[NewsFields.DB_COLL_META].find_one({
                NewsFields.DB_TYPE: updateType,
                NewsFields.DB_API_SOURCE: "ALPHA"
            }, projection={NewsFields.DB_ID: False})

            if document:
                result = {NewsFields.DB_TYPE: updateType}
                for field in [
                    NewsFields.DB_META_STATUS,
                    NewsFields.DB_META_EARLIEST,
                    NewsFields.DB_META_LATEST,
                    NewsFields.DB_META_TARGET,
                    NewsFields.DB_META_COMPLETE,
                    NewsFields.DB_TIMESTAMP
                ]:

                    value = document.get(field)
                    if isinstance(value, str) and field != NewsFields.DB_META_STATUS:
                        result[field] = DateTimeHelper.parseTimestampAsDatetime(value)
                    else:
                        result[field] = value
                return result
            else:
                return None

        except Exception as e:
            _logger.error("Error reading news update metadata: %s", e)
            return None
        finally:
            await self.close()

    async def writeNewsUpdateMetadata(self, data):
        try:
            await self.connect()

            update_fields = {}
            for field in [
                NewsFields.DB_META_STATUS,
                NewsFields.DB_META_EARLIEST,
                NewsFields.DB_META_LATEST,
                NewsFields.DB_META_TARGET,
                NewsFields.DB_META_COMPLETE,
                NewsFields.DB_META_TIMESTAMP,
                NewsFields.DB_TYPE]:

                if field in data:
                    value = data[field]
                    if value is None:
                        # If the value is None, we'll use None in MongoDB as well
                        update_fields[field] = None
                    elif isinstance(value, datetime):
                        update_fields[field] = DateTimeHelper.toIso8601(value)
                    else:
                        update_fields[field] = value

            # Ensure that DB_TYPE is present in the update_fields
            if NewsFields.DB_TYPE not in update_fields:
                raise ValueError("DB_TYPE must be provided in the data")

            result = await self.db[NewsFields.DB_COLL_META].update_one(
                {
                    NewsFields.DB_TYPE: update_fields[NewsFields.DB_TYPE],
                    NewsFields.DB_API_SOURCE: "ALPHA"
                },
                {"$set": update_fields},
                upsert=True
            )
            return result.modified_count > 0 or result.upserted_id is not None
        except Exception as e:
            _logger.error("Error writing news update metadata: %s", e)
            return False
        finally:
            await self.close()

    # Generate a hash for the article.  Initial method of minimizing duplicate articles.
    @staticmethod
    def generateArticleHash(item):

        if NewsFields.DB_TIME_PUBLISHED not in item or NewsFields.DB_SUMMARY not in item:
            return None

        unique_string = f"{item[NewsFields.DB_TIME_PUBLISHED][:6]}{item[NewsFields.DB_TITLE][:32]}"
        return hashlib.md5(unique_string.encode()).hexdigest()

    async def _validateMetaDataTz(self):
        try:
            if not self.isConnected():
                await self.connect()

            result = False

            historicalUpdate = await self.readNewsUpdateMetadata(NewsFields.DB_UPDATE_TYPE_HISTORICAL)
            currentUpdate = await self.readNewsUpdateMetadata(NewsFields.DB_UPDATE_TYPE_CURRENT)

            # If current has no target, set its target to historical.latest
            if NewsFields.DB_META_TARGET not in currentUpdate or currentUpdate[NewsFields.DB_META_TARGET] is None:
                currentUpdate[NewsFields.DB_META_TARGET] = historicalUpdate.get(NewsFields.DB_META_LATEST)

            historicalUpdateUpdated = copy.deepcopy(historicalUpdate)
            currentUpdateUpdated = copy.deepcopy(currentUpdate)

            historicalUpdateUpdated = NewsDB._validateUpdateTzInfo(historicalUpdateUpdated)
            currentUpdateUpdated = NewsDB._validateUpdateTzInfo(currentUpdateUpdated)

            if historicalUpdate != historicalUpdateUpdated:
                await self.writeNewsUpdateMetadata(historicalUpdateUpdated)
                result = True

            if currentUpdate != currentUpdateUpdated:
                await self.writeNewsUpdateMetadata(currentUpdateUpdated)
                result = True

            return result

        except Exception as e:
            _logger.error("Error validating timezone information: %s", e)
            return False

    @staticmethod
    def _validateUpdateTzInfo(updateData):
        def ensureUtcTz(dt):
            if dt is not None and dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        updateData[NewsFields.DB_META_EARLIEST] = ensureUtcTz(updateData.get(NewsFields.DB_META_EARLIEST))
        updateData[NewsFields.DB_META_LATEST] = ensureUtcTz(updateData.get(NewsFields.DB_META_LATEST))
        updateData[NewsFields.DB_META_COMPLETE] = ensureUtcTz(updateData.get(NewsFields.DB_META_COMPLETE))

        return updateData


async def main():
    try:
        load_dotenv()
        dbname = os.getenv('NEWS_DB_TEST')
        host = os.getenv('NEWS_HOST')

        # Create database if it doesn't exist.
        dbInit = NewsDB(_dbname=dbname, _host=host)
        await dbInit.createNewsDatabase()
        await dbInit.migrateNewsMetaData()
        await dbInit.close()

    except (Exception) as error:
        print(error)


# Run this if this file is called directly as the main file.
if __name__ == "__main__":
    asyncio.run(main())
