"""
Unit tests for session_classifier module.

Tests session classification for individual bars and DataFrames.
"""

import pytest
from datetime import datetime, date, time
from unittest.mock import Mock, MagicMock
import pandas as pd
import pytz

from manta_trading.data.base.session_classifier import (
    classify_bar_session,
    split_bars_by_session,
    add_session_column
)
from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.data.base.trading_calendar import TradingHours


@pytest.fixture
def mock_calendar():
    """Create a mock TradingCalendar."""
    calendar = Mock()
    calendar.timezone = pytz.timezone('America/New_York')
    calendar.has_extended_hours = True
    return calendar


@pytest.fixture
def sample_dataframe():
    """Create a sample DataFrame with timestamps."""
    tz = pytz.timezone('America/New_York')
    data = {
        'timestamp': [
            tz.localize(datetime(2024, 1, 2, 10, 0)),  # RTH
            tz.localize(datetime(2024, 1, 2, 14, 30)),  # RTH
            tz.localize(datetime(2024, 1, 2, 7, 0)),   # ETH (pre-market)
            tz.localize(datetime(2024, 1, 2, 18, 0)),  # ETH (after-hours)
        ],
        'price': [100.0, 101.0, 99.5, 100.5]
    }
    return pd.DataFrame(data)


class TestClassifyBarSession:
    """Tests for classify_bar_session function."""

    def test_rth_classification(self, mock_calendar):
        """Test that bar during RTH is classified as RTH."""
        tz = pytz.timezone('America/New_York')
        timestamp = tz.localize(datetime(2024, 1, 2, 10, 0))  # 10:00 AM ET

        # Mock trading day check
        mock_calendar.is_trading_day.return_value = True

        # Mock RTH hours (9:30 AM - 4:00 PM)
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result = classify_bar_session(timestamp, mock_calendar)
        assert result == SessionType.RTH

    def test_eth_pre_market_classification(self, mock_calendar):
        """Test that pre-market bar is classified as ETH."""
        tz = pytz.timezone('America/New_York')
        timestamp = tz.localize(datetime(2024, 1, 2, 7, 0))  # 7:00 AM ET

        mock_calendar.is_trading_day.return_value = True

        # Mock RTH hours (9:30 AM - 4:00 PM) - timestamp is before this
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        rth_hours = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        # Mock ETH hours (4:00 AM - 8:00 PM)
        eth_start = tz.localize(datetime(2024, 1, 2, 4, 0))
        eth_end = tz.localize(datetime(2024, 1, 2, 20, 0))
        eth_hours = TradingHours(
            session_start=eth_start,
            session_end=eth_end,
            session_type=SessionType.ETH,
            is_trading_day=True
        )

        mock_calendar.get_trading_hours.side_effect = [rth_hours, eth_hours]

        result = classify_bar_session(timestamp, mock_calendar)
        assert result == SessionType.ETH

    def test_eth_after_hours_classification(self, mock_calendar):
        """Test that after-hours bar is classified as ETH."""
        tz = pytz.timezone('America/New_York')
        timestamp = tz.localize(datetime(2024, 1, 2, 18, 0))  # 6:00 PM ET

        mock_calendar.is_trading_day.return_value = True

        # Mock RTH hours - timestamp is after this
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        rth_hours = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        # Mock ETH hours
        eth_start = tz.localize(datetime(2024, 1, 2, 4, 0))
        eth_end = tz.localize(datetime(2024, 1, 2, 20, 0))
        eth_hours = TradingHours(
            session_start=eth_start,
            session_end=eth_end,
            session_type=SessionType.ETH,
            is_trading_day=True
        )

        mock_calendar.get_trading_hours.side_effect = [rth_hours, eth_hours]

        result = classify_bar_session(timestamp, mock_calendar)
        assert result == SessionType.ETH

    def test_weekend_classification(self, mock_calendar):
        """Test that weekend bar is classified as ETH."""
        tz = pytz.timezone('America/New_York')
        timestamp = tz.localize(datetime(2024, 1, 6, 10, 0))  # Saturday

        mock_calendar.is_trading_day.return_value = False

        result = classify_bar_session(timestamp, mock_calendar)
        assert result == SessionType.ETH

    def test_naive_timestamp_raises_error(self, mock_calendar):
        """Test that naive timestamp raises ValueError."""
        timestamp = datetime(2024, 1, 2, 10, 0)  # No timezone

        with pytest.raises(ValueError, match="timezone-aware"):
            classify_bar_session(timestamp, mock_calendar)

    def test_timezone_conversion(self, mock_calendar):
        """Test that timestamp is converted to calendar timezone."""
        # Create timestamp in UTC
        utc_tz = pytz.timezone('UTC')
        timestamp = utc_tz.localize(datetime(2024, 1, 2, 15, 0))  # 3:00 PM UTC = 10:00 AM ET

        et_tz = pytz.timezone('America/New_York')
        mock_calendar.is_trading_day.return_value = True

        # Mock RTH hours in ET
        rth_start = et_tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = et_tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result = classify_bar_session(timestamp, mock_calendar)
        assert result == SessionType.RTH


class TestSplitBarsBySession:
    """Tests for split_bars_by_session function."""

    def test_split_mixed_sessions(self, mock_calendar, sample_dataframe):
        """Test splitting DataFrame with both RTH and ETH bars."""
        tz = pytz.timezone('America/New_York')
        mock_calendar.is_trading_day.return_value = True

        def mock_get_trading_hours(trade_date, session_type):
            if session_type == SessionType.RTH:
                return TradingHours(
                    session_start=tz.localize(datetime.combine(trade_date, time(9, 30))),
                    session_end=tz.localize(datetime.combine(trade_date, time(16, 0))),
                    session_type=SessionType.RTH,
                    is_trading_day=True
                )
            else:  # ETH
                return TradingHours(
                    session_start=tz.localize(datetime.combine(trade_date, time(4, 0))),
                    session_end=tz.localize(datetime.combine(trade_date, time(20, 0))),
                    session_type=SessionType.ETH,
                    is_trading_day=True
                )

        mock_calendar.get_trading_hours.side_effect = mock_get_trading_hours

        result = split_bars_by_session(sample_dataframe, mock_calendar)

        assert SessionType.RTH in result
        assert SessionType.ETH in result
        assert len(result[SessionType.RTH]) == 2  # 10:00 and 14:30
        assert len(result[SessionType.ETH]) == 2  # 7:00 and 18:00

    def test_empty_dataframe(self, mock_calendar):
        """Test that empty DataFrame returns empty dict."""
        empty_df = pd.DataFrame()
        result = split_bars_by_session(empty_df, mock_calendar)
        assert result == {}

    def test_missing_timestamp_column(self, mock_calendar):
        """Test that missing timestamp column raises ValueError."""
        df = pd.DataFrame({'price': [100, 101]})

        with pytest.raises(ValueError, match="not found"):
            split_bars_by_session(df, mock_calendar)

    def test_custom_timestamp_column(self, mock_calendar):
        """Test using custom timestamp column name."""
        tz = pytz.timezone('America/New_York')
        df = pd.DataFrame({
            'time': [tz.localize(datetime(2024, 1, 2, 10, 0))],
            'price': [100]
        })

        mock_calendar.is_trading_day.return_value = True
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result = split_bars_by_session(df, mock_calendar, timestamp_column='time')
        assert SessionType.RTH in result
        assert len(result[SessionType.RTH]) == 1

    def test_all_rth_bars(self, mock_calendar):
        """Test DataFrame with only RTH bars."""
        tz = pytz.timezone('America/New_York')
        df = pd.DataFrame({
            'timestamp': [
                tz.localize(datetime(2024, 1, 2, 10, 0)),
                tz.localize(datetime(2024, 1, 2, 11, 0)),
                tz.localize(datetime(2024, 1, 2, 14, 0))
            ],
            'price': [100, 101, 102]
        })

        mock_calendar.is_trading_day.return_value = True
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result = split_bars_by_session(df, mock_calendar)
        assert SessionType.RTH in result
        assert SessionType.ETH not in result
        assert len(result[SessionType.RTH]) == 3


class TestAddSessionColumn:
    """Tests for add_session_column function."""

    def test_add_session_column(self, mock_calendar, sample_dataframe):
        """Test adding session_type column to DataFrame."""
        tz = pytz.timezone('America/New_York')
        mock_calendar.is_trading_day.return_value = True

        def mock_get_trading_hours(trade_date, session_type):
            if session_type == SessionType.RTH:
                return TradingHours(
                    session_start=tz.localize(datetime.combine(trade_date, time(9, 30))),
                    session_end=tz.localize(datetime.combine(trade_date, time(16, 0))),
                    session_type=SessionType.RTH,
                    is_trading_day=True
                )
            else:
                return TradingHours(
                    session_start=tz.localize(datetime.combine(trade_date, time(4, 0))),
                    session_end=tz.localize(datetime.combine(trade_date, time(20, 0))),
                    session_type=SessionType.ETH,
                    is_trading_day=True
                )

        mock_calendar.get_trading_hours.side_effect = mock_get_trading_hours

        result_df = add_session_column(sample_dataframe, mock_calendar)

        assert 'session_type' in result_df.columns
        assert len(result_df) == 4
        # Check that we have both RTH and ETH values
        assert 'RTH' in result_df['session_type'].values
        assert 'ETH' in result_df['session_type'].values

    def test_custom_column_names(self, mock_calendar):
        """Test using custom column names."""
        tz = pytz.timezone('America/New_York')
        df = pd.DataFrame({
            'time': [tz.localize(datetime(2024, 1, 2, 10, 0))],
            'price': [100]
        })

        mock_calendar.is_trading_day.return_value = True
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result_df = add_session_column(
            df,
            mock_calendar,
            timestamp_column='time',
            session_column='session'
        )

        assert 'session' in result_df.columns
        assert result_df['session'].iloc[0] == 'RTH'

    def test_empty_dataframe(self, mock_calendar):
        """Test that empty DataFrame is returned unchanged."""
        empty_df = pd.DataFrame()
        result = add_session_column(empty_df, mock_calendar)
        assert result.empty

    def test_missing_timestamp_column(self, mock_calendar):
        """Test that missing timestamp column raises ValueError."""
        df = pd.DataFrame({'price': [100]})

        with pytest.raises(ValueError, match="not found"):
            add_session_column(df, mock_calendar)

    def test_session_values_are_strings(self, mock_calendar):
        """Test that session_type column contains string values."""
        tz = pytz.timezone('America/New_York')
        df = pd.DataFrame({
            'timestamp': [tz.localize(datetime(2024, 1, 2, 10, 0))],
            'price': [100]
        })

        mock_calendar.is_trading_day.return_value = True
        rth_start = tz.localize(datetime(2024, 1, 2, 9, 30))
        rth_end = tz.localize(datetime(2024, 1, 2, 16, 0))
        mock_calendar.get_trading_hours.return_value = TradingHours(
            session_start=rth_start,
            session_end=rth_end,
            session_type=SessionType.RTH,
            is_trading_day=True
        )

        result_df = add_session_column(df, mock_calendar)

        assert isinstance(result_df['session_type'].iloc[0], str)
        assert result_df['session_type'].iloc[0] == 'RTH'
