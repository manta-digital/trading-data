import os
import tempfile
import unittest
from datetime import datetime, timezone

from dotenv import load_dotenv
from manta_trading.news.news import News, NewsCommandOptions


class TestNewsIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        load_dotenv()

        self.tempPath = os.path.join(tempfile.gettempdir(), 'test_output.csv')
        self.news = News()
        await self.news._createServices()

    async def asyncTearDown(self):
        # Close any open connections
        if hasattr(self.news, 'api'):
            await self.news.api.close()

    async def test_agent_command(self):
        self.news.options = NewsCommandOptions()
        self.news.options.command = 'agent'
        self.news.options.symbol = 'AAPL'
        self.news.options.dateFrom = datetime(2023, 1, 1, tzinfo=timezone.utc)
        self.news.options.dateTo = datetime(2024, 1, 31, tzinfo=timezone.utc)
        self.news.options.batchSize = 100
        self.news.options.outputFile = self.tempPath
        self.news.options.valid = True

        result = await self.news._processCommand()
        self.assertIsNone(result)  # The agent command doesn't return a result
        self.assertTrue(os.path.exists(self.tempPath))
        os.remove(self.tempPath)  # Clean up the test output file

    async def test_invalid_command(self):
        self.news.options = NewsCommandOptions()
        self.news.options.command = 'invalid'
        self.news.options.valid = True

        result = await self.news._processCommand()
        self.assertIsNone(result)

    async def test_verify_news_db(self):
        result = await self.news._verifyNewsDb()
        self.assertTrue(result)


if __name__ == '__main__':
    unittest.main()
