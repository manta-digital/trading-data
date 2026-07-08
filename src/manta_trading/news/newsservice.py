import pytz
from datetime import datetime, timezone, timedelta
from dateutil.relativedelta import relativedelta

from enum import Enum
from manta_trading.logging import get_logger
from typing import Optional

_logger = get_logger(__name__)

from manta_trading.news.newsfields import NewsFields
from manta_trading.util.asynceventemitter import AsyncEventEmitter
from manta_trading.util.datetimehelper import DateTimeHelper

# todo: move these to own file.
class NewsUpdateType(Enum):
    HISTORICAL = "historical"
    CURRENT = "current"

class NewsUpdateStatus:
    def __init__(self):
        self.type: NewsUpdateType = None
        self.status: str = "incomplete"
        self.earliest: Optional[datetime] = None
        self.latest: Optional[datetime] = None
        self.last_updated: Optional[datetime] = None

class NewsService:
    """
    NewsService: provides high-level functions for interacting with news data.  Wraps API and database.
    """
    TIMESTAMP = 'time_published'
    EARLIEST = 'earliest'
    LATEST = 'latest'
    TARGET = 'target'
    COMPLETE = 'complete'
    STATUS = 'status'
    EVENT_READ_NEWS_OP = 'readNewsOperation'
    EVENT_READ_STATUS_OP = 'readStatusOperation'
    TYPE = 'type'

    def __init__(self, api=None, db=None):
        self.api = api
        self.db = db
        self.events = AsyncEventEmitter()
        self.print = True
        self.timeFormat = "%Y-%m-%dT%H:%M:%S%z"  # ISO-8601 format
        self.max_retries = 3
        self.time_window = timedelta(hours=12)

    # AlphaVantage shim
    @staticmethod
    def _now():
        return datetime.now(pytz.timezone('US/Eastern'))

    # functions needed:
    # 1. get news from API
    #    1. automatic/update
    #    2. defined time interval?  we probably don't care just get the stuff
    # 2. write news to database
    #    should happen as we are getting this from api
    # 3. read news from database
    #    this should happen over a defined interval.
    async def updateNews(self):
        status = await self.getUpdateStatus()
        await self.fetchAndUpdateNews(status)

    async def fetchAndUpdateNews(self, status: NewsUpdateStatus):
        start_date = status.latest or status.earliest or datetime(2020, 1, 1, tzinfo=timezone.utc)
        end_date = datetime.now(timezone.utc)

        no_progress_count = 0
        max_no_progress_attempts = 3  # Adjust this value as needed

        while start_date < end_date:
            window_end = min(start_date + self.time_window, end_date)
            news_items = await self.fetchNewsInRange(start_date, window_end)

            if not news_items:
                start_date = window_end
                no_progress_count += 1
            else:
                latest_article_time = max(
                    DateTimeHelper.parseTimestampAsDatetime(item[NewsFields.DB_TIME_PUBLISHED]) for item in news_items)

                if latest_article_time <= start_date:
                    no_progress_count += 1
                else:
                    no_progress_count = 0  # Reset the counter as we made progress
                    await self.processAndStoreNews(news_items)
                    start_date = latest_article_time + timedelta(seconds=1)
                    status.latest = latest_article_time
                    await self.setUpdateStatus(status)

                _logger.info("Processed batch of %s articles. Latest article from %s", len(news_items), latest_article_time)

            if no_progress_count >= max_no_progress_attempts:
                _logger.warning("No progress made after %s attempts. Stopping update.", max_no_progress_attempts)
                break

        status.status = "complete"
        await self.setUpdateStatus(status)

    async def fetchNewsInRange(self, start: datetime, end: datetime, batchsize: int = 1000) -> list[dict]:
        """
        Fetch news articles within a specified date range.
        
        Args:
            start: The earliest date to fetch news from
            end: The latest date to fetch news to
            batchsize: Maximum number of news items to fetch
            
        Returns:
            List of news items, or empty list if error or no items
        """
        from manta_trading.api.apireadstatus import ErrorHandler
        
        try:
            batch = await self.api.getNewsSentiment(
                dateEarliest=DateTimeHelper.toIso8601(start),
                dateLatest=DateTimeHelper.toIso8601(end),
                limit=batchsize,
            )
            
            # Check if result is an error dictionary
            if ErrorHandler.is_error(batch):
                ErrorHandler.log_error(batch, log_level='warning')
                _logger.warning("Error fetching news between %s and %s: %s", start, end, batch.get('message'))
                return []

            if not batch or NewsFields.DB_NEWSITEMS not in batch:
                _logger.info("No news items found between %s and %s", start, end)
                return []
                
            return batch[NewsFields.DB_NEWSITEMS]
            
        except Exception as e:
            # Convert exception to standardized error and log it
            from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
            
            error = ErrorHandler.handle_exception(
                e,
                context={
                    "start": start.isoformat() if start else None, 
                    "end": end.isoformat() if end else None, 
                    "batchsize": batchsize
                }
            )
            _logger.error("Failed to fetch news: %s", error.get('message'))
            return []

    async def processAndStoreNews(self, news_items: list[dict]):
        if not self.db.isConnected():
            await self.db.connect()

        await self.db.writeNews({NewsFields.DB_NEWSITEMS: news_items})

    async def getUpdateStatus(self) -> NewsUpdateStatus:
        """
        Retrieve update status from the database.
        """
        status = NewsUpdateStatus()
        metadata = await self.db.readNewsUpdateMetadata()
        if metadata:
            status.status = metadata.get(NewsFields.DB_META_STATUS, "incomplete")
            status.earliest = DateTimeHelper.parseTimestampAsDatetime(metadata.get(NewsFields.DB_META_EARLIEST))
            status.latest = DateTimeHelper.parseTimestampAsDatetime(metadata.get(NewsFields.DB_META_LATEST))
            status.last_updated = DateTimeHelper.parseTimestampAsDatetime(metadata.get(NewsFields.DB_TIMESTAMP))

        return status

    async def setUpdateStatus(self, status: NewsUpdateStatus):
        """
        Store or update status in the database.
        """
        metadata = {
            NewsFields.DB_TYPE: NewsFields.DB_UPDATE_TYPE_HISTORICAL,
            NewsFields.DB_META_STATUS: status.status,
            NewsFields.DB_META_EARLIEST: DateTimeHelper.toIso8601(status.earliest) if status.earliest else None,
            NewsFields.DB_META_LATEST: DateTimeHelper.toIso8601(status.latest) if status.latest else None,
            NewsFields.DB_TIMESTAMP: DateTimeHelper.toIso8601(datetime.now(timezone.utc))
        }
        await self.db.writeNewsUpdateMetadata(metadata)

    def log(self, message):
        if self.print:
            _logger.info("%s", message)

    # todo: this expects timestamp as an 8601 str and fails otherwise lets get rid of this
    @staticmethod
    def formatTimestamp(timestamp):
        """
        Format a timestamp to ISO-8601 format.
        
        Args:
            timestamp: Either a string timestamp or a datetime object
            
        Returns:
            str: ISO-8601 formatted timestamp, or original string if parsing fails
        """
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        if timestamp is None:
            return None
            
        if isinstance(timestamp, str):
            try:
                # Try to parse the string as a datetime
                dt = datetime.fromisoformat(timestamp)
                return DateTimeHelper.toIso8601(dt)
            except ValueError as e:
                # Log the error but return the original string
                error = ErrorHandler.handle_exception(
                    e,
                    context={"timestamp": timestamp},
                    expected_exceptions={
                        ValueError: ApiReadStatus.VALIDATION_ERROR
                    },
                    log_level='warning'
                )
                _logger.warning("Failed to parse timestamp: %s", error.get('message'))
                return timestamp
                
        return DateTimeHelper.toIso8601(timestamp)
        
    async def cleanup(self):
        """Clean up resources including API and database connections"""
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        try:
            # Close API connection if it exists
            if self.api and hasattr(self.api, 'cleanup') and callable(self.api.cleanup):
                await self.api.cleanup()
                
            # Close database connection if it exists
            if self.db and hasattr(self.db, 'close') and callable(self.db.close):
                await self.db.close()
                
        except Exception as e:
            error = ErrorHandler.handle_exception(
                e,
                context="NewsService cleanup",
                expected_exceptions={
                    ConnectionError: ApiReadStatus.NETWORK_ERROR,
                    asyncio.CancelledError: ApiReadStatus.PROCESSING_ERROR
                }
            )
            _logger.error("Cleanup error: %s", error.get('message', str(e)))



    async def readNews(self, symbol=None, latest=None, earliest=None, batchSize=None):
        """
        Read news for specified symbol over specified range from the database.  Note that batchSize may not be
        large enough to return all news between rangeFrom and rangTo.  In this case results will start at
        rangeFrom and contain at most batchSize items.

        :param symbol:      the symbol to read.
        :param latest:     the latest point, i.e. where we start, which feels like backwards naming.  May be None.
        :param earliest:   the earliest point, i.e. where we end.  if None, read back until no more data.
        :param batchSize:   the number of items to read at a time.
        """
        if not self.db.isConnected():
            await self.db.connect()

        latest = DateTimeHelper.convertToDbFormatString(latest)
        earliest = DateTimeHelper.convertToDbFormatString(earliest)

        return await self.db.readNews(tickers=symbol, earliest=earliest, latest=latest, batchSize=batchSize)

    async def readNewsSentiment(self, symbols=None, earliest=None, latest=None, batchSize=1000):
        """
        Read news sentiment for specified symbols over specified range from the database.  Note that batchSize may not be
        large enough to return all news between rangeFrom and rangTo.  In this case results will start at
        rangeFrom and contain at most batchSize items.

        :param symbols:     the symbol(s) to read.
        :param latest:      the latest point, i.e. where we start.
        :param earliest:    the earliest point, i.e. where we end.
        :param batchSize:   the number of items to read at a time.

        Returns:            news items according to the params above.
        """
        await self.db.connect()
        earliest = DateTimeHelper.convertToDbFormatString(earliest) if earliest else None
        latest = DateTimeHelper.convertToDbFormatString(latest) if latest else None

        return await self.db.readNewsSentiment(symbols=symbols, earliest=earliest, latest=latest, batchSize=batchSize)






