import os
import pandas as pd

from manta_trading.logging import get_logger
from manta_trading.news.newsfields import NewsFields

_logger = get_logger(__name__)
from manta_trading.util.datetimehelper import DateTimeHelper
from manta_trading.util.pathutil import PathUtil


class NewsAgent:
    def __init__(self, marketDb=None, newsService=None, outputDir=None):
        self.marketDb = marketDb
        self.newsService = newsService
        self.outputDir = outputDir

    # This one does weighted mean by relevance.
    async def readMergedData(self, symbol=None, dateFrom=None, dateTo=None, batchSize=None):
        """
        Return merged news and market data for a given symbol and date range. Merged news is grouped
        by day but this version does not consider news when market is closed.  Updated version in
        development
        """

        # Function to calculate weighted mean sentiment
        def weighted_mean(data):
            weights = data['relevance']
            sentiment = data['sentiment_mean']
            if weights.sum() == 0:
                return 0  # Avoid division by zero
            return (sentiment * weights).sum() / weights.sum()

        newsData = await self.newsService.readNewsSentiment(symbols=symbol, earliest=dateFrom, latest=dateTo,
                                                            batchSize=batchSize)

        # Check if newsData is empty or not in the expected format
        if not newsData or not isinstance(newsData, (list, dict)):
            _logger.warning("No news data available or unexpected format for symbol: %s", symbol)
            return None

        # Convert newsData to a list if it's a single item
        if isinstance(newsData, dict):
            newsData = [newsData]

        if len(newsData) == 0:
            _logger.warning("No news data available for symbol: %s", symbol)
            return None

        earliestDate = newsData[0].get(NewsFields.DB_TIME_PUBLISHED, None)
        latestDate = newsData[-1].get(NewsFields.DB_TIME_PUBLISHED, None)

        if earliestDate is not None:
            earliestDate = DateTimeHelper.convertToDbFormatString(earliestDate)

        if latestDate is not None:
            latestDate = DateTimeHelper.convertToDbFormatString(latestDate)

        newsDf = pd.DataFrame(newsData)
        newsDf['time_published'] = pd.to_datetime(newsDf['time_published']).dt.date
        newsDf['sentiment_mean'] = pd.to_numeric(newsDf['ticker_sentiment_score'], errors='coerce')
        newsDf['relevance'] = pd.to_numeric(newsDf['relevance_score'], errors='coerce')

        # Aggregate with min, max, count, and weighted mean
        groupedNews = newsDf.groupby('time_published').agg({
            'sentiment_mean': [('min', 'min'), ('max', 'max'), ('count', 'count'),
                               ('weighted_mean', lambda x: weighted_mean(newsDf.loc[x.index]))]
        }).reset_index()

        # Flatten MultiIndex in columns and rename appropriately
        groupedNews.columns = ['date', 'sentiment_min', 'sentiment_max', 'sentiment_count', 'weighted_sentiment_mean']
        groupedNews['date'] = pd.to_datetime(groupedNews['date'])

        marketData = self.marketDb.readDailyOHLCVAdjusted(symbol, earliestDate, latestDate)
        marketDf = pd.DataFrame(marketData, columns=['symbol', 'date', 'open', 'high', 'low', 'close', 'adj_close', 'drop1', 'drop2', 'volume'])
        marketDf.drop(columns=['drop1', 'drop2'], inplace=True)
        marketDf['date'] = pd.to_datetime(marketDf['date'])

        # Merge the news data with market data
        mergedData = pd.merge(marketDf, groupedNews, on='date', how='left')

        # Fill NaN values if any, because of non-trading days
        mergedData.ffill(inplace=True)

        return mergedData

    def writeCSV(self, data, outputFilename):
        if outputFilename is None or outputFilename == "":
            print("No output file specified. Writing to console.")
            print(data)
        else:
            filePath = outputFilename if self.outputDir is None else os.path.join(self.outputDir, outputFilename)
            PathUtil.createDirectories(filePath)
            data.to_csv(filePath, index=False)
            _logger.info("Output written to: %s", filePath)

    def readCSV(self, inputPath):
        df = pd.read_csv(inputPath, usecols=[
            'symbol', 'date', 'open', 'high', 'low', 'close',
            'adj_close', 'volume', 'sentiment_min', 'sentiment_max',
            'sentiment_count', 'weighted_sentiment_mean',
        ], parse_dates=['date'])

        # Display the first few rows of the dataframe
        df.head()

    def calculateCorrelation(self, marketData, newsData):
        # A method to calculate and return correlations
        pass
