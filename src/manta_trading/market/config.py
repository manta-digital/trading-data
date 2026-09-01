import os

from manta_trading.logging import get_logger

_logger = get_logger(__name__)


class ChunkingConfig:
    """
    Configuration class for intelligent chunking parameters.
    Centralizes all hard-coded values related to data fetching and chunking logic.
    """

    def __init__(self):
        # Core chunking parameters
        self.CHUNK_SIZE_DAYS = int(os.getenv("CHUNK_SIZE_DAYS", "100"))
        self.MIN_DAYS_THRESHOLD = int(os.getenv("MIN_DAYS_THRESHOLD", "2"))
        self.MAX_ERROR_COUNT = int(os.getenv("MAX_ERROR_COUNT", "3"))
        self.RECENT_DAYS_THRESHOLD = int(os.getenv("RECENT_DAYS_THRESHOLD", "100"))

        # Gap analysis parameters
        self.MAX_LOOKBACK_DAYS = int(
            os.getenv("MAX_LOOKBACK_DAYS", "200")
        )  # For gap detection

        # Performance tuning parameters
        self.CHUNK_DELAY_SECONDS = float(os.getenv("CHUNK_DELAY_SECONDS", "0.1"))
        self.BATCH_SIZE = int(os.getenv("BATCH_SIZE", "500"))

        # Validate configuration
        self._validate_config()

    def _validate_config(self):
        """Validate configuration values to prevent invalid settings."""
        if self.CHUNK_SIZE_DAYS <= 0:
            raise ValueError(
                f"CHUNK_SIZE_DAYS must be positive, got {self.CHUNK_SIZE_DAYS}"
            )

        if self.MIN_DAYS_THRESHOLD < 0:
            raise ValueError(
                f"MIN_DAYS_THRESHOLD must be non-negative, got {self.MIN_DAYS_THRESHOLD}"
            )

        if self.MAX_ERROR_COUNT <= 0:
            raise ValueError(
                f"MAX_ERROR_COUNT must be positive, got {self.MAX_ERROR_COUNT}"
            )

        if self.RECENT_DAYS_THRESHOLD <= 0:
            raise ValueError(
                f"RECENT_DAYS_THRESHOLD must be positive, got {self.RECENT_DAYS_THRESHOLD}"
            )

        if self.MAX_LOOKBACK_DAYS <= 0:
            raise ValueError(
                f"MAX_LOOKBACK_DAYS must be positive, got {self.MAX_LOOKBACK_DAYS}"
            )

        if self.CHUNK_DELAY_SECONDS < 0:
            raise ValueError(
                f"CHUNK_DELAY_SECONDS must be non-negative, got {self.CHUNK_DELAY_SECONDS}"
            )

        if self.BATCH_SIZE <= 0:
            raise ValueError(f"BATCH_SIZE must be positive, got {self.BATCH_SIZE}")

        # Log configuration for debugging
        _logger.debug(
            "ChunkingConfig initialized: CHUNK_SIZE_DAYS=%s, MIN_DAYS_THRESHOLD=%s, RECENT_DAYS_THRESHOLD=%s",
            self.CHUNK_SIZE_DAYS,
            self.MIN_DAYS_THRESHOLD,
            self.RECENT_DAYS_THRESHOLD,
        )
