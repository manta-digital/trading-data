"""Tests for manta_trading.constants module."""

from __future__ import annotations

from datetime import date, timedelta
import pytest

from manta_trading.constants import (
    DAILY_HISTORY_MONTHS,
    DAILY_STALENESS_THRESHOLD,
    EODHD_INTRADAY_HORIZON,
    LATE_BAR_GRACE_PERIOD,
    MAX_GAP_STALENESS,
    MAX_RETRY_COUNT,
    MINUTE_STALENESS_THRESHOLD,
)


def test_max_retry_count_type_and_value() -> None:
    assert isinstance(MAX_RETRY_COUNT, int)
    assert MAX_RETRY_COUNT == 5


def test_daily_staleness_threshold_type_and_value() -> None:
    assert isinstance(DAILY_STALENESS_THRESHOLD, timedelta)
    assert DAILY_STALENESS_THRESHOLD == timedelta(days=2)


def test_minute_staleness_threshold_type_and_value() -> None:
    assert isinstance(MINUTE_STALENESS_THRESHOLD, timedelta)
    assert MINUTE_STALENESS_THRESHOLD == timedelta(days=1)


def test_daily_history_months_is_none() -> None:
    assert DAILY_HISTORY_MONTHS is None
    assert not isinstance(DAILY_HISTORY_MONTHS, int)
    assert not isinstance(DAILY_HISTORY_MONTHS, timedelta)


def test_eodhd_intraday_horizon_type_and_value() -> None:
    assert isinstance(EODHD_INTRADAY_HORIZON, date)
    assert EODHD_INTRADAY_HORIZON == date(2004, 1, 1)


def test_late_bar_grace_period_type_and_value() -> None:
    assert isinstance(LATE_BAR_GRACE_PERIOD, timedelta)
    assert LATE_BAR_GRACE_PERIOD == timedelta(minutes=30)


def test_max_gap_staleness_type_and_value() -> None:
    assert isinstance(MAX_GAP_STALENESS, timedelta)
    assert MAX_GAP_STALENESS == timedelta(minutes=5)
