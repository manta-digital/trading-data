"""
Unit tests for DataProcessor

Tests the data processing pipeline including:
- Session classification (RTH/ETH/CLOSED)
- Metadata enrichment (provider, adjustment policy, timestamps)
- OHLCV validation (consistency, nulls, duplicates)
- Full processing pipeline coordination
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pandas as pd
import pytest

from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.instrument_registry import Instrument, InstrumentRegistry
from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.historical_minute.processor import DataProcessor
from manta_trading.data.historical_minute.provider import RawDataResponse, ValidationResult


@pytest.fixture
def mock_calendar():
    """Create a mock TradingCalendar for testing."""
    calendar = Mock(spec=TradingCalendar)
    return calendar


@pytest.fixture
def mock_registry():
    """Create a mock InstrumentRegistry for testing."""
    registry = Mock(spec=InstrumentRegistry)
    return registry


@pytest.fixture
def processor(mock_calendar, mock_registry):
    """Create a DataProcessor instance for testing with mocked dependencies."""
    return DataProcessor(calendar=mock_calendar, registry=mock_registry)


@pytest.fixture
def sample_df():
    """Create a sample DataFrame with valid OHLCV data."""
    return pd.DataFrame({
        'timestamp': pd.to_datetime([
            '2024-01-15 10:00:00',
            '2024-01-15 10:01:00',
            '2024-01-15 10:02:00',
        ], utc=True),
        'open': [150.0, 150.5, 151.0],
        'high': [150.5, 151.0, 151.5],
        'low': [149.5, 150.0, 150.5],
        'close': [150.25, 150.75, 151.25],
        'volume': [10000, 15000, 12000]
    })


@pytest.fixture
def invalid_ohlcv_df():
    """Create a DataFrame with various OHLCV validation errors."""
    return pd.DataFrame({
        'timestamp': pd.to_datetime([
            '2024-01-15 10:00:00',
            '2024-01-15 10:01:00',
            '2024-01-15 10:02:00',
            '2024-01-15 10:03:00',
            '2024-01-15 10:04:00',
            '2024-01-15 10:05:00',
        ], utc=True),
        'open': [150.0, 150.5, 151.0, None, 152.0, 153.0],
        'high': [150.5, 149.0, 151.5, 153.0, 152.5, 153.5],  # Row 1: high < low
        'low': [149.5, 150.0, 150.5, 152.0, 152.0, 153.0],
        'close': [150.25, 150.75, 151.25, 152.5, 152.25, 153.25],
        'volume': [10000, -5000, 12000, 8000, 0, 9000]  # Row 1: negative, Row 4: zero
    })


@pytest.fixture
def duplicate_timestamp_df():
    """Create a DataFrame with duplicate timestamps."""
    return pd.DataFrame({
        'timestamp': pd.to_datetime([
            '2024-01-15 10:00:00',
            '2024-01-15 10:01:00',
            '2024-01-15 10:01:00',  # Duplicate
        ], utc=True),
        'open': [150.0, 150.5, 150.6],
        'high': [150.5, 151.0, 151.1],
        'low': [149.5, 150.0, 150.1],
        'close': [150.25, 150.75, 150.8],
        'volume': [10000, 15000, 15500]
    })


@pytest.fixture
def mock_instrument():
    """Create a mock Instrument for testing."""
    return Instrument(
        instrument_id=1,
        canonical_id='AAPL.NASDAQ',
        symbol='AAPL',
        asset_class='stock',
        venue='NASDAQ',
        currency='USD',
    )


@pytest.fixture
def raw_data_response(sample_df):
    """Create a RawDataResponse for testing."""
    return RawDataResponse(
        symbol='AAPL',
        provider='alphavantage',
        start_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2024, 1, 31, tzinfo=timezone.utc),
        raw_data={'test': 'data'},
        metadata={}
    )


@pytest.fixture
def mock_provider():
    """Create a mock provider with convert_to_standard_format method."""
    provider = Mock()
    provider.convert_to_standard_format = Mock(return_value=pd.DataFrame({
        'timestamp': pd.to_datetime([
            '2024-01-15 10:00:00',
            '2024-01-15 10:01:00',
        ], utc=True),
        'open': [150.0, 150.5],
        'high': [150.5, 151.0],
        'low': [149.5, 150.0],
        'close': [150.25, 150.75],
        'volume': [10000, 15000]
    }))
    return provider


class TestDataProcessorInit:
    """Test DataProcessor initialization."""

    def test_initialization(self, processor):
        """Test that DataProcessor initializes with required components."""
        assert processor._calendar is not None
        assert processor._registry is not None
        assert processor._logger is not None


class TestClassifySessions:
    """Test session classification functionality."""

    @patch('manta_trading.data.historical_minute.processor.classify_bar_session')
    def test_classify_sessions_success(self, mock_classify, processor, sample_df, mock_instrument):
        """Test successful session classification."""
        # Setup mocks
        processor._registry.get_instrument = Mock(return_value=mock_instrument)
        mock_classify.return_value = SessionType.RTH

        # Classify sessions
        result_df = processor.classify_sessions(sample_df.copy(), 'AAPL')

        # Verify session_type column added
        assert 'session_type' in result_df.columns
        assert all(result_df['session_type'] == SessionType.RTH.value)

        # Verify instrument lookup called
        processor._registry.get_instrument.assert_called_once_with('AAPL')

        # Verify classify_bar_session called for each row
        assert mock_classify.call_count == len(sample_df)

    def test_classify_sessions_invalid_symbol(self, processor, sample_df):
        """Test that invalid symbol raises ValueError."""
        processor._registry.get_instrument = Mock(return_value=None)

        with pytest.raises(ValueError, match="Symbol 'INVALID' not found"):
            processor.classify_sessions(sample_df, 'INVALID')

    def test_classify_sessions_missing_timestamp(self, processor):
        """Test that missing timestamp column raises KeyError."""
        df = pd.DataFrame({'open': [150.0], 'close': [150.5]})

        with pytest.raises(KeyError, match="must have 'timestamp' column"):
            processor.classify_sessions(df, 'AAPL')

    @patch('manta_trading.data.historical_minute.processor.classify_bar_session')
    def test_classify_sessions_mixed_types(self, mock_classify, processor, sample_df, mock_instrument):
        """Test classification with mixed RTH and ETH sessions."""
        processor._registry.get_instrument = Mock(return_value=mock_instrument)

        # Mock different session types for different timestamps
        session_types = [SessionType.RTH, SessionType.ETH, SessionType.RTH]
        mock_classify.side_effect = session_types

        result_df = processor.classify_sessions(sample_df.copy(), 'AAPL')

        assert 'session_type' in result_df.columns
        assert result_df['session_type'].tolist() == ['RTH', 'ETH', 'RTH']


class TestEnrichMetadata:
    """Test metadata enrichment functionality."""

    def test_enrich_metadata_default_version(self, processor, sample_df):
        """Test metadata enrichment with default version."""
        result_df = processor.enrich_metadata(sample_df.copy(), 'alphavantage')

        # Verify all metadata columns added
        assert 'adjustment_policy' in result_df.columns
        assert 'provider' in result_df.columns
        assert 'provider_version' in result_df.columns
        assert 'ingestion_timestamp' in result_df.columns

        # Verify values
        assert all(result_df['adjustment_policy'] == 'split_adjusted')
        assert all(result_df['provider'] == 'alphavantage')
        assert all(result_df['provider_version'] == '1.0')

        # Verify ingestion_timestamp is recent
        for ts in result_df['ingestion_timestamp']:
            assert (datetime.now(timezone.utc) - ts).total_seconds() < 5

    def test_enrich_metadata_custom_version(self, processor, sample_df):
        """Test metadata enrichment with custom version."""
        result_df = processor.enrich_metadata(sample_df.copy(), 'polygon', '2.5')

        assert all(result_df['provider'] == 'polygon')
        assert all(result_df['provider_version'] == '2.5')


class TestValidateOHLCV:
    """Test OHLCV validation functionality."""

    def test_validate_valid_data(self, processor, sample_df):
        """Test validation passes for valid OHLCV data."""
        result = processor.validate_ohlcv(sample_df)

        assert result.is_valid is True
        assert len(result.errors) == 0
        assert len(result.warnings) == 0

    def test_validate_missing_columns(self, processor):
        """Test validation fails with missing required columns."""
        df = pd.DataFrame({'timestamp': [datetime.now(timezone.utc)], 'open': [150.0]})

        result = processor.validate_ohlcv(df)

        assert result.is_valid is False
        assert 'Missing required columns' in result.errors[0]

    def test_validate_high_less_than_low(self, processor, invalid_ohlcv_df):
        """Test validation catches high < low."""
        result = processor.validate_ohlcv(invalid_ohlcv_df)

        assert result.is_valid is False
        assert any('High < Low' in error for error in result.errors)

    def test_validate_null_prices(self, processor, invalid_ohlcv_df):
        """Test validation catches null prices."""
        result = processor.validate_ohlcv(invalid_ohlcv_df)

        assert result.is_valid is False
        assert any('Null open prices' in error for error in result.errors)

    def test_validate_negative_volume(self, processor, invalid_ohlcv_df):
        """Test validation catches negative volume."""
        result = processor.validate_ohlcv(invalid_ohlcv_df)

        assert result.is_valid is False
        assert any('Negative volume' in error for error in result.errors)

    def test_validate_zero_volume_warning(self, processor, invalid_ohlcv_df):
        """Test that zero volume generates warning (not error)."""
        result = processor.validate_ohlcv(invalid_ohlcv_df)

        assert any('Zero volume' in warning for warning in result.warnings)

    def test_validate_duplicate_timestamps(self, processor, duplicate_timestamp_df):
        """Test validation catches duplicate timestamps."""
        result = processor.validate_ohlcv(duplicate_timestamp_df)

        assert result.is_valid is False
        assert any('Duplicate timestamps' in error for error in result.errors)

    def test_validate_null_timestamps(self, processor, sample_df):
        """Test validation catches null timestamps."""
        df = sample_df.copy()
        df.loc[1, 'timestamp'] = None

        result = processor.validate_ohlcv(df)

        assert result.is_valid is False
        assert any('Null timestamps' in error for error in result.errors)

    def test_validate_high_less_than_open(self, processor):
        """Test validation catches high < open."""
        df = pd.DataFrame({
            'timestamp': pd.to_datetime(['2024-01-15 10:00:00'], utc=True),
            'open': [151.0],
            'high': [150.0],  # high < open
            'low': [149.0],
            'close': [150.5],
            'volume': [10000]
        })

        result = processor.validate_ohlcv(df)

        assert result.is_valid is False
        assert any('High < Open' in error for error in result.errors)

    def test_validate_low_greater_than_close(self, processor):
        """Test validation catches low > close."""
        df = pd.DataFrame({
            'timestamp': pd.to_datetime(['2024-01-15 10:00:00'], utc=True),
            'open': [150.0],
            'high': [151.0],
            'low': [150.8],  # low > close
            'close': [150.5],
            'volume': [10000]
        })

        result = processor.validate_ohlcv(df)

        assert result.is_valid is False
        assert any('Low > Close' in error for error in result.errors)


class TestProcessPipeline:
    """Test full processing pipeline."""

    @patch('manta_trading.data.historical_minute.processor.classify_bar_session')
    def test_process_success(self, mock_classify, processor, raw_data_response, mock_provider, mock_instrument):
        """Test successful full pipeline processing."""
        # Setup mocks
        processor._registry.get_instrument = Mock(return_value=mock_instrument)
        mock_classify.return_value = SessionType.RTH

        # Process data
        df, validation = processor.process(raw_data_response, mock_provider)

        # Verify pipeline stages completed
        assert 'session_type' in df.columns  # Session classification
        assert 'provider' in df.columns  # Metadata enrichment
        assert 'adjustment_policy' in df.columns
        assert 'ingestion_timestamp' in df.columns

        # Verify validation ran
        assert isinstance(validation, ValidationResult)
        assert validation.is_valid is True

        # Verify provider called
        mock_provider.convert_to_standard_format.assert_called_once()

    def test_process_invalid_symbol(self, processor, raw_data_response, mock_provider):
        """Test that processing fails with invalid symbol."""
        processor._registry.get_instrument = Mock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            processor.process(raw_data_response, mock_provider)

    @patch('manta_trading.data.historical_minute.processor.classify_bar_session')
    def test_process_logs_stages(self, mock_classify, processor, raw_data_response, mock_provider, mock_instrument):
        """Test that processing completes all stages successfully."""
        processor._registry.get_instrument = Mock(return_value=mock_instrument)
        mock_classify.return_value = SessionType.RTH

        df, validation = processor.process(raw_data_response, mock_provider)

        # Verify all stages completed by checking output
        assert df is not None
        assert validation is not None
        assert 'session_type' in df.columns
        assert 'provider' in df.columns
        assert validation.is_valid is True

    @patch('manta_trading.data.historical_minute.processor.classify_bar_session')
    def test_process_with_validation_errors(self, mock_classify, processor, raw_data_response, mock_instrument):
        """Test processing with data that has validation errors."""
        # Create provider that returns invalid data
        bad_provider = Mock()
        bad_provider.convert_to_standard_format = Mock(return_value=pd.DataFrame({
            'timestamp': pd.to_datetime(['2024-01-15 10:00:00'], utc=True),
            'open': [150.0],
            'high': [149.0],  # high < low - invalid!
            'low': [150.0],
            'close': [150.5],
            'volume': [10000]
        }))

        processor._registry.get_instrument = Mock(return_value=mock_instrument)
        mock_classify.return_value = SessionType.RTH

        df, validation = processor.process(raw_data_response, bad_provider)

        # Pipeline should complete but validation should fail
        assert validation.is_valid is False
        assert len(validation.errors) > 0
