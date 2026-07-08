import unittest
from unittest.mock import patch, AsyncMock, MagicMock
from manta_trading.news.news import News

class TestNews(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.news = News()

    @patch('manta_trading.news.news.NewsService')
    @patch('manta_trading.news.news.AlphavantageAPI')
    @patch('manta_trading.news.news.NewsDB')
    @patch('manta_trading.news.news.MarketDB')
    @patch('manta_trading.news.news.NewsAgent')
    async def testProcessCommandUpdate(self, MockNewsAgent, MockMarketDB, MockNewsDB, MockAPI, MockNewsService):
        # Setup mock return values
        mock_api = AsyncMock()
        mock_news_db = AsyncMock()
        mock_market_db = AsyncMock()
        mock_news_service = AsyncMock()
        mock_news_agent = AsyncMock()

        MockAPI.return_value = mock_api
        MockNewsDB.return_value = mock_news_db
        MockMarketDB.return_value = mock_market_db
        MockNewsService.return_value = mock_news_service
        MockNewsAgent.return_value = mock_news_agent

        # Set up the updateNews mock
        mock_news_service.updateNews = AsyncMock()

        # Manually set up the services
        self.news.api = mock_api
        self.news.newsDb = mock_news_db
        self.news.marketDb = mock_market_db
        self.news.newsService = mock_news_service
        self.news.newsAgent = mock_news_agent

        # Set up the options
        self.news.options.command = 'update'
        self.news.options.all = True
        self.news.options.valid = True

        # Call the method
        await self.news._processCommand()

        # Assert that updateNews was called
        mock_news_service.updateNews.assert_awaited_once()

if __name__ == '__main__':
    unittest.main()