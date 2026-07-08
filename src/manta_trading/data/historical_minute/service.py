"""
Historical Minute Data Service Module

This module implements the HistoricalMinuteService which orchestrates historical
minute data acquisition.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from manta_trading.data.base.service_interface import (
    GapInfo,
    HealthMetrics,
    QualityReport,
)
from manta_trading.data.base.instrument_registry import InstrumentRegistry
from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.historical_minute.data_structures import (
    AcquisitionResult,
    AcquisitionStatus,
    BatchAcquisitionResult,
)
from manta_trading.data.historical_minute.processor import DataProcessor
from manta_trading.data.historical_minute.provider import IMinuteDataProvider
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


class HistoricalMinuteService:
    """Orchestrates historical minute data acquisition and management."""

    def __init__(
        self,
        provider: IMinuteDataProvider,
        storage: TimescaleMinuteDataDB,
        registry: InstrumentRegistry,
        calendar: TradingCalendar,
        config: dict[str, Any] | None = None
    ):
        """Initialize the Historical Minute Service."""
        self._provider = provider
        self._storage = storage
        self._registry = registry
        self._calendar = calendar
        self._processor = DataProcessor(calendar=calendar, registry=registry)
        self._logger = logging.getLogger(__name__)

        config = config or {}
        self._retry_count = config.get("retry_count", 3)
        self._retry_backoff = config.get("retry_backoff", 2.0)
        self._batch_chunk_size = config.get("batch_chunk_size", 10)

        self._error_count = 0
        self._last_error: str | None = None
        self._last_update = datetime.now(timezone.utc)

    def _get_existing_coverage(
        self, symbol: str, start_date: datetime, end_date: datetime
    ) -> dict[str, Any]:
        """Get existing data coverage for a symbol."""
        return self._storage.get_coverage_analysis(symbol)

    def _calculate_missing_months(
        self, symbol: str, start_date: datetime, end_date: datetime,
        existing_coverage: dict[str, Any]
    ) -> list[tuple[datetime, datetime]]:
        """Calculate which months need to be fetched."""
        missing_months = []
        current = datetime(start_date.year, start_date.month, 1, tzinfo=timezone.utc)
        while current <= end_date:
            if current.month == 12:
                next_month = datetime(current.year + 1, 1, 1, tzinfo=timezone.utc)
            else:
                next_month = datetime(current.year, current.month + 1, 1, tzinfo=timezone.utc)
            month_end = next_month - pd.Timedelta(days=1)
            earliest = existing_coverage.get("earliest_data")
            latest = existing_coverage.get("latest_data")
            if earliest is None or latest is None:
                missing_months.append((current, month_end))
            elif current < earliest or month_end > latest:
                missing_months.append((current, month_end))
            current = next_month
        return missing_months

    async def acquire_symbol(
        self, symbol: str, start_date: datetime, end_date: datetime
    ) -> AcquisitionResult:
        """Acquire historical minute data for a single symbol."""
        start_time = datetime.now(timezone.utc)
        errors: list[str] = []
        warnings: list[str] = []
        rows_written = 0
        months_processed = 0

        self._logger.info(f"Starting acquisition for {symbol}")

        instrument = self._registry.get_instrument(symbol)
        if instrument is None:
            self._error_count += 1
            self._last_error = f"Invalid symbol: {symbol}"
            return AcquisitionResult(
                symbol=symbol, status=AcquisitionStatus.FAILED,
                rows_written=0, months_processed=0,
                errors=[f"Symbol {symbol} not found"],
                start_time=start_time, end_time=datetime.now(timezone.utc)
            )

        existing_coverage = self._get_existing_coverage(symbol, start_date, end_date)
        missing_months = self._calculate_missing_months(
            symbol, start_date, end_date, existing_coverage
        )

        if not missing_months:
            return AcquisitionResult(
                symbol=symbol, status=AcquisitionStatus.COMPLETED,
                rows_written=0, months_processed=0,
                warnings=["Data already complete"],
                start_time=start_time, end_time=datetime.now(timezone.utc)
            )

        all_dataframes: list[pd.DataFrame] = []
        for month_start, month_end in missing_months:
            retry_count = 0
            success = False
            while retry_count < self._retry_count and not success:
                try:
                    raw_response = await self._provider.fetch_minute_data(
                        symbol, month_start, month_end
                    )
                    validation = self._provider.validate_response(raw_response.raw_data)
                    if not validation.is_valid:
                        errors.extend(validation.errors)
                        break
                    df, proc_val = self._processor.process(raw_response, self._provider)
                    if not proc_val.is_valid:
                        warnings.extend(proc_val.warnings)
                        errors.extend(proc_val.errors)
                    all_dataframes.append(df)
                    months_processed += 1
                    success = True
                except TimeoutError as e:
                    retry_count += 1
                    if retry_count < self._retry_count:
                        await asyncio.sleep(self._retry_backoff ** retry_count)
                    else:
                        errors.append(f"Timeout: {e}")
                except Exception as e:
                    retry_count += 1
                    if retry_count < self._retry_count:
                        await asyncio.sleep(self._retry_backoff ** retry_count)
                    else:
                        errors.append(f"Failed: {e}")
                        self._error_count += 1
                        self._last_error = str(e)

        if all_dataframes:
            try:
                combined_df = pd.concat(all_dataframes, ignore_index=True)
                if "timestamp" in combined_df.columns:
                    combined_df.set_index("timestamp", inplace=True)
                if self._storage.write_minute_data_bulk(symbol, combined_df):
                    rows_written = len(combined_df)
                else:
                    errors.append("Storage write failed")
            except Exception as e:
                errors.append(f"Storage error: {e}")
                self._error_count += 1
                self._last_error = str(e)

        if errors and not rows_written:
            status = AcquisitionStatus.FAILED
        elif errors:
            status = AcquisitionStatus.PARTIALLY_COMPLETED
        else:
            status = AcquisitionStatus.COMPLETED

        self._last_update = datetime.now(timezone.utc)
        return AcquisitionResult(
            symbol=symbol, status=status, rows_written=rows_written,
            months_processed=months_processed, errors=errors, warnings=warnings,
            start_time=start_time, end_time=datetime.now(timezone.utc)
        )

    async def acquire_batch(
        self, symbols: list[str], start_date: datetime, end_date: datetime
    ) -> BatchAcquisitionResult:
        """Acquire historical minute data for multiple symbols."""
        start_time = datetime.now(timezone.utc)
        results: dict[str, AcquisitionResult] = {}
        successful = 0
        failed = 0

        valid_symbols = []
        for symbol in symbols:
            if self._registry.get_instrument(symbol) is None:
                results[symbol] = AcquisitionResult(
                    symbol=symbol, status=AcquisitionStatus.FAILED,
                    rows_written=0, months_processed=0,
                    errors=[f"Symbol {symbol} not found"],
                    start_time=start_time, end_time=datetime.now(timezone.utc)
                )
                failed += 1
            else:
                valid_symbols.append(symbol)

        for symbol in valid_symbols:
            try:
                result = await self.acquire_symbol(symbol, start_date, end_date)
                results[symbol] = result
                if result.status in (AcquisitionStatus.COMPLETED,
                                     AcquisitionStatus.PARTIALLY_COMPLETED):
                    successful += 1
                else:
                    failed += 1
            except Exception as e:
                results[symbol] = AcquisitionResult(
                    symbol=symbol, status=AcquisitionStatus.FAILED,
                    rows_written=0, months_processed=0,
                    errors=[f"Unexpected error: {e}"],
                    start_time=start_time, end_time=datetime.now(timezone.utc)
                )
                failed += 1

        return BatchAcquisitionResult(
            symbols=symbols, results=results, total_symbols=len(symbols),
            successful=successful, failed=failed,
            start_time=start_time, end_time=datetime.now(timezone.utc)
        )

    def get_health_metrics(self) -> HealthMetrics:
        """Get current health metrics for the service."""
        if self._error_count == 0:
            status, quality_score = "healthy", 1.0
        elif self._error_count < 5:
            status, quality_score = "degraded", 0.8
        else:
            status = "unhealthy"
            quality_score = max(0.0, 1.0 - (self._error_count * 0.1))
        return HealthMetrics(
            status=status, error_count=self._error_count,
            last_error=self._last_error, last_update=self._last_update,
            quality_score=quality_score
        )

    def detect_gaps(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[GapInfo]:
        """Detect gaps in data coverage (basic implementation)."""
        gaps: list[GapInfo] = []
        coverage = self._storage.get_coverage_analysis(symbol)
        earliest = coverage.get("earliest_data")
        latest = coverage.get("latest_data")
        if earliest is not None and start < earliest:
            gaps.append(GapInfo(
                symbol=symbol, gap_start=start, gap_end=earliest,
                expected_bars=0, gap_type="full_day"
            ))
        if latest is not None and end > latest:
            gaps.append(GapInfo(
                symbol=symbol, gap_start=latest, gap_end=end,
                expected_bars=0, gap_type="full_day"
            ))
        return gaps

    def get_quality_report(self, symbol: str) -> QualityReport:
        """Get data quality report (basic implementation)."""
        coverage = self._storage.get_coverage_analysis(symbol)
        total_rows = coverage.get("total_rows", 0)
        completeness = min(1.0, total_rows / 10000) if total_rows > 0 else 0.0
        return QualityReport(
            symbol=symbol, completeness_score=completeness,
            accuracy_score=1.0, timeliness_score=1.0, consistency_score=1.0,
            last_analyzed=datetime.now(timezone.utc)
        )


__all__ = ["HistoricalMinuteService"]
