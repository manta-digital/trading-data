"""
Unit tests for HistoricalMinuteService

Tests the service orchestration including:
- Service initialization and configuration
- Coverage checking helpers
- Single symbol acquisition
- Batch processing
- IDataService protocol methods (health, gaps, quality)
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd
import pytest

from manta_trading.data.base.instrument_registry import Instrument
from manta_trading.data.base.service_interface import GapInfo, HealthMetrics, QualityReport
from manta_trading.data.historical_minute.data_structures import (
    AcquisitionResult,
    AcquisitionStatus,
    BatchAcquisitionResult,
)
from manta_trading.data.historical_minute.provider import RawDataResponse, ValidationResult
from manta_trading.data.historical_minute.service import HistoricalMinuteService


@pytest.fixture
def mock_provider():
    """Create a mock provider."""
    provider = Mock()
    provider.fetch_minute_data = AsyncMock()
    provider.validate_response = Mock(return_value=ValidationResult(
        is_valid=True, errors=[], warnings=[]
    ))
    provider.convert_to_standard_format = Mock(return_value=pd.DataFrame({
        "timestamp": pd.to_datetime(["2024-01-15 10:00:00"], utc=True),
        "open": [150.0], "high": [151.0], "low": [149.0],
        "close": [150.5], "volume": [10000]
    }))
    return provider


@pytest.fixture
def mock_storage():
    """Create a mock storage."""
    storage = Mock()
    storage.get_coverage_analysis = Mock(return_value={
        "earliest_data": None, "latest_data": None, "total_rows": 0
    })
    storage.write_minute_data_bulk = Mock(return_value=True)
    return storage


@pytest.fixture
def mock_registry():
    """Create a mock registry."""
    registry = Mock()
    instrument = Mock()
    instrument.symbol = "AAPL"
    registry.get_instrument = Mock(return_value=instrument)
    return registry


@pytest.fixture
def mock_calendar():
    """Create a mock calendar."""
    return Mock()


@pytest.fixture
def service(mock_provider, mock_storage, mock_registry, mock_calendar):
    """Create a HistoricalMinuteService for testing."""
    return HistoricalMinuteService(
        provider=mock_provider, storage=mock_storage,
        registry=mock_registry, calendar=mock_calendar
    )


class TestServiceInit:
    """Test service initialization."""

    def test_initialization_defaults(self, service):
        """Test service initializes with default config."""
        assert service._retry_count == 3
        assert service._retry_backoff == 2.0
        assert service._batch_chunk_size == 10
        assert service._error_count == 0
        assert service._last_error is None

    def test_initialization_custom_config(
        self, mock_provider, mock_storage, mock_registry, mock_calendar
    ):
        """Test service initializes with custom config."""
        config = {"retry_count": 5, "retry_backoff": 3.0, "batch_chunk_size": 20}
        svc = HistoricalMinuteService(
            provider=mock_provider, storage=mock_storage,
            registry=mock_registry, calendar=mock_calendar, config=config
        )
        assert svc._retry_count == 5
        assert svc._retry_backoff == 3.0
        assert svc._batch_chunk_size == 20


class TestCalculateMissingMonths:
    """Test _calculate_missing_months helper."""

    def test_no_existing_data(self, service):
        """Test all months returned when no existing data."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 3, 31, tzinfo=timezone.utc)
        coverage = {"earliest_data": None, "latest_data": None}
        
        result = service._calculate_missing_months("AAPL", start, end, coverage)
        
        assert len(result) == 3  # Jan, Feb, Mar

    def test_complete_coverage(self, service):
        """Test empty list when data is complete."""
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 31, tzinfo=timezone.utc)
        coverage = {
            "earliest_data": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "latest_data": datetime(2024, 12, 31, tzinfo=timezone.utc)
        }
        
        result = service._calculate_missing_months("AAPL", start, end, coverage)
        
        assert len(result) == 0


class TestAcquireSymbol:
    """Test acquire_symbol method."""

    @pytest.mark.asyncio
    async def test_invalid_symbol(self, service, mock_registry):
        """Test failure for invalid symbol."""
        mock_registry.get_instrument.return_value = None
        
        result = await service.acquire_symbol(
            "INVALID",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc)
        )
        
        assert result.status == AcquisitionStatus.FAILED
        assert len(result.errors) > 0

    @pytest.mark.asyncio
    async def test_data_already_complete(self, service, mock_storage):
        """Test early return when data is complete."""
        mock_storage.get_coverage_analysis.return_value = {
            "earliest_data": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "latest_data": datetime(2025, 1, 1, tzinfo=timezone.utc),
            "total_rows": 10000
        }
        
        result = await service.acquire_symbol(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc)
        )
        
        assert result.status == AcquisitionStatus.COMPLETED
        assert result.rows_written == 0

    @pytest.mark.asyncio
    @patch("manta_trading.data.historical_minute.service.DataProcessor")
    async def test_successful_acquisition(
        self, mock_processor_class, service, mock_provider, mock_storage
    ):
        """Test successful data acquisition."""
        mock_processor = Mock()
        mock_processor.process = Mock(return_value=(
            pd.DataFrame({
                "timestamp": pd.to_datetime(["2024-01-15 10:00:00"], utc=True),
                "open": [150.0], "high": [151.0], "low": [149.0],
                "close": [150.5], "volume": [10000]
            }),
            ValidationResult(is_valid=True, errors=[], warnings=[])
        ))
        service._processor = mock_processor

        mock_provider.fetch_minute_data.return_value = RawDataResponse(
            symbol="AAPL", provider="test",
            start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
            raw_data={}, metadata={}
        )
        
        result = await service.acquire_symbol(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc)
        )
        
        assert result.status == AcquisitionStatus.COMPLETED
        assert result.rows_written > 0


class TestAcquireBatch:
    """Test acquire_batch method."""

    @pytest.mark.asyncio
    async def test_batch_with_invalid_symbols(self, service, mock_registry):
        """Test batch handles invalid symbols."""
        mock_registry.get_instrument.side_effect = lambda s: None if s == "BAD" else Mock()
        
        result = await service.acquire_batch(
            ["AAPL", "BAD"],
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 31, tzinfo=timezone.utc)
        )
        
        assert result.failed >= 1
        assert "BAD" in result.results


class TestHealthMetrics:
    """Test get_health_metrics method."""

    def test_healthy_status(self, service):
        """Test healthy status when no errors."""
        metrics = service.get_health_metrics()
        
        assert metrics.status == "healthy"
        assert metrics.error_count == 0
        assert metrics.quality_score == 1.0

    def test_degraded_status(self, service):
        """Test degraded status with few errors."""
        service._error_count = 3
        service._last_error = "Test error"
        
        metrics = service.get_health_metrics()
        
        assert metrics.status == "degraded"
        assert metrics.error_count == 3
        assert metrics.quality_score == 0.8

    def test_unhealthy_status(self, service):
        """Test unhealthy status with many errors."""
        service._error_count = 10
        
        metrics = service.get_health_metrics()
        
        assert metrics.status == "unhealthy"
        assert metrics.quality_score == 0.0


class TestDetectGaps:
    """Test detect_gaps method."""

    def test_gap_at_start(self, service, mock_storage):
        """Test detection of gap at start of range."""
        mock_storage.get_coverage_analysis.return_value = {
            "earliest_data": datetime(2024, 2, 1, tzinfo=timezone.utc),
            "latest_data": datetime(2024, 3, 1, tzinfo=timezone.utc)
        }
        
        gaps = service.detect_gaps(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 2, 15, tzinfo=timezone.utc)
        )
        
        assert len(gaps) == 1
        assert gaps[0].gap_type == "full_day"

    def test_no_gaps(self, service, mock_storage):
        """Test no gaps when data is complete."""
        mock_storage.get_coverage_analysis.return_value = {
            "earliest_data": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "latest_data": datetime(2025, 1, 1, tzinfo=timezone.utc)
        }
        
        gaps = service.detect_gaps(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 2, 1, tzinfo=timezone.utc)
        )
        
        assert len(gaps) == 0


class TestQualityReport:
    """Test get_quality_report method."""

    def test_empty_data(self, service, mock_storage):
        """Test report for symbol with no data."""
        mock_storage.get_coverage_analysis.return_value = {"total_rows": 0}
        
        report = service.get_quality_report("AAPL")
        
        assert report.completeness_score == 0.0

    def test_partial_data(self, service, mock_storage):
        """Test report for symbol with some data."""
        mock_storage.get_coverage_analysis.return_value = {"total_rows": 5000}
        
        report = service.get_quality_report("AAPL")
        
        assert 0.0 < report.completeness_score < 1.0
