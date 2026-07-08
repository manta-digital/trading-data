from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass, field

from manta_trading.logging import get_logger
from dotenv import load_dotenv

_logger = get_logger(__name__)

from manta_trading.agents.newsagent import NewsAgent
# MarketDB removed in slice 152. News via MarketDB is no longer functional.
class MarketDB:  # type: ignore[no-redef]  # noqa: N801
    """Stub — MarketDB was removed in slice 152."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("MarketDB was removed in slice 152.")
    def __enter__(self): return self
    def __exit__(self, *_): pass
from manta_trading.news.newsdb import NewsDB
from manta_trading.news.newsdbmigrationutility import NewsDbMigrationUtility
from manta_trading.news.newsservice import NewsService
# AlphaVantage removed in slice 152. News fetching via AV is no longer functional.
class AlphavantageAPI:  # type: ignore[no-redef]  # noqa: N801
    """Stub — AlphaVantage was removed in slice 152."""
    def __init__(self, *args, **kwargs):
        raise RuntimeError("AlphaVantage was removed in slice 152. News via AV is not supported.")
from manta_trading.util.pathutil import PathUtil


@dataclass
class NewsCommandOptions:
    """Options for News command dispatch — replaces old argparse NewsOptions."""

    command: str | None = None
    historical: bool = False
    current: bool = False
    all: bool = False
    read: bool = False
    verify: bool = False
    symbol: str | None = None
    batchSize: int | None = None
    outputFile: str | None = None
    outputDir: str | None = None
    dateFrom: str | None = None
    dateTo: str | None = None
    valid: bool = False


class News:
    def __init__(self, options: NewsCommandOptions | None = None):
        self.api = None
        self.newsDb = None
        self.newsService = None
        self.options = options or NewsCommandOptions()
        self.newsAgent = None

    async def _verifyNewsDb(self):
        """
        Verify and migrate the news database if needed
        
        Returns:
            bool: True if verification and migration succeeded, False otherwise
        """
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        _logger.info("Starting newsdb verification and migration...")
        try:
            # Connect to the database
            await self.newsDb.connect()
            if not self.newsDb.isConnected():
                error = ErrorHandler.create_error(
                    ApiReadStatus.DB_ERROR,
                    "Failed to connect to news database",
                    "Database connection could not be established"
                )
                ErrorHandler.log_error(error, log_level='error')
                return False
            
            # Migrate metadata
            result = await self.newsDb.migrateNewsMetaData()
            if result is None:
                error = ErrorHandler.create_error(
                    ApiReadStatus.DB_ERROR,
                    "Failed to migrate news database",
                    "Migration returned None result"
                )
                ErrorHandler.log_error(error, log_level='error')
                return False
                
            # Run timestamp migration
            NewsDbMigrationUtility.runTimestampMigration()
            return True
            
        except Exception as e:
            error = ErrorHandler.handle_exception(
                e,
                context="News database verification",
                expected_exceptions={
                    ConnectionError: ApiReadStatus.DB_ERROR,
                    asyncio.TimeoutError: ApiReadStatus.TIMEOUT_ERROR
                }
            )
            _logger.error("Database verification failed: %s", error.get('message'))
            return False

    async def run(self):
        """
        Main execution method for the News application.
        Sets up services and processes command line arguments.
        
        Returns:
            The result of the command processing or an error status
        """
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        try:
            # Initialize all required services
            await self._createServices()
            
            # Process the command according to user options
            result = await self._processCommand()
            return result
            
        except Exception as e:
            error = ErrorHandler.handle_exception(
                e,
                context="Application run",
            )
            _logger.error("Run failed: %s", error.get('message'))
            return {"status": "error", "message": error.get('message')}

    # update this allow reading specific news, historical news, current news, or all news.
    # add tests for this and release.  then you should be able to start playing with stuff.
    async def _processCommand(self):
        """
        Process the command line arguments and execute the requested command.
        
        Returns:
            Various: The result of the executed command or None if invalid/error
        """
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        _logger.debug("Processing command line arguments: %s", self.options)
        result = None

        try:
            if not self.options.valid:
                _logger.warning("Invalid command options provided")
                return None

            # Process Update command
            if self.options.command == 'update':
                if self.options.verify:
                    verify_result = await self._verifyNewsDb()
                    if not verify_result:
                        return {"status": "error", "message": "Database verification failed"}

                if self.options.historical:
                    _logger.info("Historical update requested but not yet implemented")
                    return {"status": "not_implemented", "message": "Separate update categories not yet supported. Call with --all."}

                elif self.options.current:
                    _logger.info("Current update requested but not yet implemented")
                    return {"status": "not_implemented", "message": "Separate update categories not yet supported. Call with --all."}

                elif self.options.all:
                    _logger.info("Starting full news update")
                    result = await self.newsService.updateNews()

                elif self.options.read:
                    _logger.info("Running command: read, dateFrom:%s, dateTo: %s.", self.options.dateFrom, self.options.dateTo)
                    result = await self.newsService.readNews(rangeFrom=self.options.dateFrom, rangeTo=self.options.dateTo)
                    print(result)

                return result

            # Process Agent command
            elif self.options.command == 'agent':
                _logger.info("Starting news agent for symbol: %s", self.options.symbol)
                data = await self.newsAgent.readMergedData(
                    symbol=self.options.symbol,
                    dateFrom=self.options.dateFrom,
                    dateTo=self.options.dateTo,
                    batchSize=self.options.batchSize
                )
    
                # just CSV for now.  other file types...sometime.
                if data:
                    self.newsAgent.writeCSV(data, self.options.outputFile)
                    return {"status": "success", "rows": len(data), "file": self.options.outputFile}
                else:
                    return {"status": "no_data", "message": "No data found for the specified parameters"}
                    
        except Exception as e:
            error = ErrorHandler.handle_exception(
                e,
                context={
                    "command": self.options.command,
                    "options": str(self.options)
                }
            )
            _logger.error("Command processing failed: %s", error.get('message'))
            return {"status": "error", "message": error.get('message'), "details": error.get('details', None)}

    async def _createServices(self):
        """
        Initialize all required services and connections.
        Sets up API client, databases, and service layers.
        """
        from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
        
        _logger.info("manta news app v0.0.5. Initializing services...")

        try:
            # Set up output directory and file
            self.outputDir = os.getenv('NEWS_AGENT_OUTPUT_DIR')
            if not self.outputDir:
                _logger.warning("NEWS_AGENT_OUTPUT_DIR environment variable not set")
                
            if self.options.outputFile:
                self.options.outputFile = PathUtil.generateMergedOutputFilename(
                    self.options.symbol,
                    self.options.dateFrom,
                    outputPath=self.outputDir,
                    dateFormat='%Y%m'
                )
                _logger.info("Output file set to: %s", self.options.outputFile)
    
            # Initialize API client
            api_key = os.getenv('ALPHAVANTAGE_API_KEY')
            if not api_key:
                error = ErrorHandler.create_error(
                    ApiReadStatus.UNAUTHORIZED,
                    "API key not found",
                    "ALPHAVANTAGE_API_KEY environment variable is not set"
                )
                ErrorHandler.log_error(error, log_level='error')
                raise ValueError("API key not found in environment variables")
                
            self.api = AlphavantageAPI(apiKey=api_key)
            
            # Initialize databases
            self.newsDb = NewsDB(
                _dbname=os.getenv('NEWS_DB'), 
                _host=os.getenv('NEWS_HOST')
            )
            
            market_db_url = os.getenv('MT_MARKET_DB_URL')
            if not market_db_url:
                raise ValueError(
                    "MT_MARKET_DB_URL not configured. "
                    "Set the environment variable or add it to your .env file."
                )
            self.marketDb = MarketDB(conninfo=market_db_url)
    
            # Initialize service layer
            self.newsService = NewsService(api=self.api, db=self.newsDb)
            self.newsAgent = NewsAgent(
                newsService=self.newsService,
                marketDb=self.marketDb,
                outputDir=self.outputDir
            )
    
            # Set up database schema
            db_result = await self.newsDb.createNewsDatabase()
            if not db_result:
                _logger.warning("News database may not have been created properly")

            migration_result = await self.newsDb.migrateNewsMetaData()
            if not migration_result:
                _logger.warning("News database migrations may not have completed properly")

            _logger.info("All services initialized successfully")
            
        except Exception as e:
            error = ErrorHandler.handle_exception(
                e,
                context="Service initialization",
                expected_exceptions={
                    ValueError: ApiReadStatus.VALIDATION_ERROR,
                    ConnectionError: ApiReadStatus.DB_ERROR,
                    FileNotFoundError: ApiReadStatus.NOT_FOUND
                }
            )
            _logger.error("Failed to initialize services: %s", error.get('message'))
            # We don't re-raise here because we want to allow partial operation
            # even if some components fail to initialize


async def main():
    """
    Main entry point for the News application.
    Handles initialization and graceful error handling.
    
    Returns:
        The result of the application run or an error status
    """
    from manta_trading.api.apireadstatus import ApiReadStatus, ErrorHandler
    
    try:
        # Load environment variables
        load_dotenv()
        
        # Initialize and run application
        app = News()
        result = await app.run()
        
        # Cleanup resources
        if hasattr(app, 'api') and app.api:
            await app.api.cleanup()
        if hasattr(app, 'newsService') and app.newsService:
            if hasattr(app.newsService, 'cleanup') and callable(app.newsService.cleanup):
                await app.newsService.cleanup()
        if hasattr(app, 'marketDb') and app.marketDb:
            if hasattr(app.marketDb, 'close'):
                app.marketDb.close()
            
        return result
        
    except Exception as e:
        error = ErrorHandler.handle_exception(
            e,
            context="Main application execution",
        )
        _logger.critical("Application failed: %s", error.get('message'))
        return {"status": "fatal_error", "message": error.get('message')}


if __name__ == "__main__":
    try:
        result = asyncio.run(main())
        if isinstance(result, dict) and result.get('status') == 'error':
            _logger.error("Application completed with errors: %s", result.get('message'))
            exit(1)
    except KeyboardInterrupt:
        _logger.info("Application terminated by user")
    except Exception as e:
        _logger.critical("Unhandled exception: %s", str(e))
        exit(2)
