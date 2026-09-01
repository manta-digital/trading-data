"""
Session Classifier module for categorizing trading bars by session type.

This module provides functions to classify individual bars or entire DataFrames
as Regular Trading Hours (RTH) or Extended Trading Hours (ETH) based on
trading calendar data.
"""

from datetime import datetime
from typing import Dict
import pandas as pd

from manta_trading.data.base.trading_calendar import TradingCalendar
from manta_trading.data.base.adjustment_policy import SessionType
from manta_trading.logging import get_logger

_logger = get_logger(__name__)


def classify_bar_session(timestamp: datetime, calendar: TradingCalendar) -> SessionType:
    """
    Classify a single bar's session type based on timestamp and trading calendar.

    Args:
        timestamp: Timestamp of the bar (must be timezone-aware)
        calendar: TradingCalendar instance for the instrument's exchange

    Returns:
        SessionType (RTH, ETH, or ALL)

    Raises:
        ValueError: If timestamp is not timezone-aware
    """
    if timestamp.tzinfo is None:
        raise ValueError("Timestamp must be timezone-aware")

    # Convert to calendar's timezone
    local_timestamp = timestamp.astimezone(calendar.timezone)
    trade_date = local_timestamp.date()

    # Check if it's a trading day
    if not calendar.is_trading_day(trade_date):
        # Non-trading day (weekend/holiday) - consider it ETH if we have the timestamp
        return SessionType.ETH

    # Get trading hours for RTH
    rth_hours = calendar.get_trading_hours(trade_date, SessionType.RTH)
    if rth_hours:
        if rth_hours.session_start <= local_timestamp < rth_hours.session_end:
            return SessionType.RTH

    # If we have extended hours, check if it's in ETH
    if calendar.has_extended_hours:
        eth_hours = calendar.get_trading_hours(trade_date, SessionType.ETH)
        if eth_hours:
            if eth_hours.session_start <= local_timestamp < eth_hours.session_end:
                return SessionType.ETH

    # If not in RTH or ETH, default to ETH (outside normal hours)
    return SessionType.ETH


def split_bars_by_session(
    df: pd.DataFrame, calendar: TradingCalendar, timestamp_column: str = "timestamp"
) -> Dict[SessionType, pd.DataFrame]:
    """
    Split a DataFrame of bars into separate DataFrames by session type.

    Args:
        df: DataFrame containing bar data
        calendar: TradingCalendar instance for the instrument's exchange
        timestamp_column: Name of the timestamp column (default: 'timestamp')

    Returns:
        Dictionary mapping SessionType to DataFrame
        Keys will only include session types that have data

    Raises:
        ValueError: If timestamp_column doesn't exist in DataFrame
    """
    if df.empty:
        return {}

    if timestamp_column not in df.columns:
        raise ValueError(f"Column '{timestamp_column}' not found in DataFrame")

    # Classify each bar
    session_types = df[timestamp_column].apply(
        lambda ts: classify_bar_session(ts, calendar)
    )

    # Split into separate DataFrames
    result = {}
    for session_type in [SessionType.RTH, SessionType.ETH]:
        mask = session_types == session_type
        if mask.any():
            result[session_type] = df[mask].copy()

    return result


def add_session_column(
    df: pd.DataFrame,
    calendar: TradingCalendar,
    timestamp_column: str = "timestamp",
    session_column: str = "session_type",
) -> pd.DataFrame:
    """
    Add a session_type column to a DataFrame.

    Args:
        df: DataFrame containing bar data
        calendar: TradingCalendar instance for the instrument's exchange
        timestamp_column: Name of the timestamp column (default: 'timestamp')
        session_column: Name for the new session column (default: 'session_type')

    Returns:
        DataFrame with added session_type column (in-place modification)

    Raises:
        ValueError: If timestamp_column doesn't exist in DataFrame
    """
    if df.empty:
        return df

    if timestamp_column not in df.columns:
        raise ValueError(f"Column '{timestamp_column}' not found in DataFrame")

    # Classify each bar and add as new column
    df[session_column] = df[timestamp_column].apply(
        lambda ts: classify_bar_session(ts, calendar).value
    )

    _logger.debug("Added %s column to DataFrame with %d rows", session_column, len(df))
    return df
