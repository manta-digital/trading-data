"""
Data Processing Pipeline Module

This module implements the synchronous data processing pipeline for transforming raw minute
data into storage-ready format. The processor handles CPU-bound operations including:
- Session classification (RTH vs ETH)
- Metadata enrichment (provider info, timestamps, adjustment policy)
- OHLCV validation (consistency checks, null detection, duplicate removal)

All processing is synchronous (no async) since these are CPU-bound operations.
The pipeline coordinates with:
- TradingCalendar: For session classification
- SessionClassifier: For session type determination
- InstrumentRegistry: For instrument/exchange lookup
"""

import logging
from datetime import datetime, timezone

import pandas as pd

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.base.session_classifier import classify_bar_session
from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.historical_minute.provider import RawDataResponse, ValidationResult


class DataProcessor:
    """
    Synchronous data processor for historical minute data.

    Transforms raw provider data through multiple stages:
    1. Format conversion (via provider's convert_to_standard_format)
    2. Session classification (RTH/ETH/CLOSED)
    3. Metadata enrichment (provider, adjustment policy, ingestion timestamp)
    4. OHLCV validation (consistency, nulls, duplicates)

    All methods are synchronous as they perform CPU-bound operations.
    """

    def __init__(
        self,
        calendar: TradingCalendar | None = None,
        registry: InstrumentRegistry | None = None
    ):
        """
        Initialize the data processor with required foundation components.

        Args:
            calendar: TradingCalendar instance (optional, for dependency injection)
            registry: InstrumentRegistry instance (optional, for dependency injection)

        Note:
            If calendar or registry are not provided, they must be set before calling
            processing methods, or those methods will fail.
        """
        self._calendar = calendar
        self._registry = registry
        self._logger = logging.getLogger(__name__)

    def classify_sessions(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        """
        Classify each bar as RTH (Regular Trading Hours) or ETH (Extended Trading Hours).

        If either `_calendar` or `_registry` is None, classification is skipped and
        the DataFrame is returned unchanged. The DB schema defaults `session_type`
        to 'RTH' for rows written without the column, and the column is not
        persisted by `write_minute_data_bulk` anyway — so skipping is lossless.
        session_type can be derived later from timestamps when a calendar is wired.

        Args:
            df: DataFrame with 'timestamp' column
            symbol: Stock symbol to lookup exchange

        Returns:
            DataFrame with added 'session_type' column (or unchanged if skipped)

        Raises:
            ValueError: If symbol not found in registry (when registry provided)
            KeyError: If 'timestamp' column missing
        """
        # Validate inputs
        if 'timestamp' not in df.columns:
            raise KeyError("DataFrame must have 'timestamp' column")

        # Skip gracefully when dependencies are absent (CLI path without calendar)
        if self._calendar is None or self._registry is None:
            self._logger.debug(
                "classify_sessions skipped for %s: calendar or registry is None",
                symbol,
            )
            return df

        # Lookup instrument to get exchange
        instrument = self._registry.get_instrument(symbol)
        if instrument is None:
            raise ValueError(f"Symbol '{symbol}' not found in InstrumentRegistry")

        # Classify each row using the classify_bar_session function
        session_types = []
        for timestamp in df['timestamp']:
            session = classify_bar_session(timestamp, self._calendar)
            session_types.append(session.value)

        df['session_type'] = session_types
        return df

    def enrich_metadata(
        self,
        df: pd.DataFrame,
        provider: str,
        provider_version: str = "1.0"
    ) -> pd.DataFrame:
        """
        Add metadata columns to the DataFrame.

        Args:
            df: DataFrame to enrich
            provider: Name of data provider (e.g., 'alphavantage')
            provider_version: Version of provider integration (default: '1.0')

        Returns:
            DataFrame with added metadata columns:
            - adjustment_policy: Always 'split_adjusted' for current implementation
            - provider: Name of data provider
            - provider_version: Version of provider integration
            - ingestion_timestamp: UTC timestamp when data was processed
        """
        df['adjustment_policy'] = 'split_adjusted'
        df['provider'] = provider
        df['provider_version'] = provider_version
        df['ingestion_timestamp'] = datetime.now(timezone.utc)
        return df

    def validate_ohlcv(self, df: pd.DataFrame) -> ValidationResult:
        """
        Validate OHLCV data for consistency, nulls, and duplicates.

        Checks performed:
        - OHLCV consistency: high >= low, high >= open/close, low <= open/close
        - Null timestamps and prices
        - Negative volume
        - Zero volume (warning only)
        - Duplicate timestamps

        Args:
            df: DataFrame with OHLCV columns

        Returns:
            ValidationResult with is_valid flag, errors list, and warnings list
        """
        errors = []
        warnings = []

        # Check for required columns
        required_cols = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        missing_cols = [col for col in required_cols if col not in df.columns]
        if missing_cols:
            return ValidationResult(
                is_valid=False,
                errors=[f"Missing required columns: {missing_cols}"],
                warnings=[]
            )

        # Check for null timestamps
        null_timestamps = df['timestamp'].isnull()
        if null_timestamps.any():
            null_rows = df.index[null_timestamps].tolist()
            errors.append(f"Null timestamps found in rows: {null_rows}")

        # Check for null prices
        for col in ['open', 'high', 'low', 'close']:
            null_prices = df[col].isnull()
            if null_prices.any():
                null_rows = df.index[null_prices].tolist()
                errors.append(f"Null {col} prices found in rows: {null_rows}")

        # OHLCV consistency checks (only for rows without nulls)
        valid_rows = ~(df[['open', 'high', 'low', 'close']].isnull().any(axis=1))
        valid_df = df[valid_rows]

        # high >= low
        invalid_hl = valid_df['high'] < valid_df['low']
        if invalid_hl.any():
            invalid_rows = valid_df.index[invalid_hl].tolist()
            errors.append(f"High < Low in rows: {invalid_rows}")

        # high >= open
        invalid_ho = valid_df['high'] < valid_df['open']
        if invalid_ho.any():
            invalid_rows = valid_df.index[invalid_ho].tolist()
            errors.append(f"High < Open in rows: {invalid_rows}")

        # high >= close
        invalid_hc = valid_df['high'] < valid_df['close']
        if invalid_hc.any():
            invalid_rows = valid_df.index[invalid_hc].tolist()
            errors.append(f"High < Close in rows: {invalid_rows}")

        # low <= open
        invalid_lo = valid_df['low'] > valid_df['open']
        if invalid_lo.any():
            invalid_rows = valid_df.index[invalid_lo].tolist()
            errors.append(f"Low > Open in rows: {invalid_rows}")

        # low <= close
        invalid_lc = valid_df['low'] > valid_df['close']
        if invalid_lc.any():
            invalid_rows = valid_df.index[invalid_lc].tolist()
            errors.append(f"Low > Close in rows: {invalid_rows}")

        # Check for negative volume
        negative_volume = df['volume'] < 0
        if negative_volume.any():
            invalid_rows = df.index[negative_volume].tolist()
            errors.append(f"Negative volume in rows: {invalid_rows}")

        # Check for zero volume (warning only - valid but unusual)
        zero_volume = df['volume'] == 0
        if zero_volume.any():
            warning_rows = df.index[zero_volume].tolist()
            warnings.append(f"Zero volume (unusual but valid) in rows: {warning_rows}")

        # Check for duplicate timestamps
        duplicates = df['timestamp'].duplicated()
        if duplicates.any():
            dup_rows = df.index[duplicates].tolist()
            errors.append(f"Duplicate timestamps in rows: {dup_rows}")

        is_valid = len(errors) == 0
        return ValidationResult(is_valid=is_valid, errors=errors, warnings=warnings)

    def process(
        self,
        raw_response: RawDataResponse,
        provider_instance
    ) -> tuple[pd.DataFrame, ValidationResult]:
        """
        Main processing pipeline coordinating all transformation steps.

        Pipeline stages:
        1. Convert raw data to standard format (via provider)
        2. Classify sessions (RTH/ETH/CLOSED)
        3. Enrich with metadata
        4. Validate OHLCV data

        Args:
            raw_response: RawDataResponse from provider's fetch_minute_data()
            provider_instance: Provider instance with convert_to_standard_format() method

        Returns:
            Tuple of (processed DataFrame, ValidationResult)

        Raises:
            Exception: If any processing stage fails
        """
        symbol = raw_response.symbol
        provider_name = raw_response.provider

        try:
            # Stage 1: Convert to standard format
            self._logger.info(f"Processing {symbol}: Converting to standard format")
            df = provider_instance.convert_to_standard_format(raw_response)
            input_rows = len(df)
            self._logger.info(f"Processing {symbol}: Input rows = {input_rows}")

            # Stage 2: Classify sessions
            self._logger.info(f"Processing {symbol}: Classifying sessions")
            df = self.classify_sessions(df, symbol)

            # Stage 3: Enrich metadata
            self._logger.info(f"Processing {symbol}: Enriching metadata")
            df = self.enrich_metadata(df, provider_name)

            # Stage 4: Validate OHLCV
            self._logger.info(f"Processing {symbol}: Validating OHLCV")
            validation_result = self.validate_ohlcv(df)

            output_rows = len(df)
            self._logger.info(
                f"Processing {symbol}: Complete - {output_rows} rows, "
                f"{len(validation_result.errors)} errors, "
                f"{len(validation_result.warnings)} warnings"
            )

            return df, validation_result

        except Exception as e:
            self._logger.error(f"Processing {symbol} failed: {e}", exc_info=True)
            raise


__all__ = ['DataProcessor']
