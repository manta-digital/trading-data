"""Tests for Granularity enum and GRANULARITY_SOURCE mapping."""

from __future__ import annotations

import pytest

from manta_trading.constants import GRANULARITY_SOURCE, Granularity

_EXPECTED_VALUES = {"1m", "5m", "15m", "1h", "4h", "1d", "1w", "1mo", "1q"}


def test_granularity_member_count() -> None:
    assert len(Granularity) == 9


def test_granularity_expected_values() -> None:
    assert {g.value for g in Granularity} == _EXPECTED_VALUES


def test_granularity_source_covers_all_members() -> None:
    for g in Granularity:
        assert g in GRANULARITY_SOURCE, f"Missing GRANULARITY_SOURCE entry for {g!r}"


def test_granularity_no_duplicate_values() -> None:
    vals = [g.value for g in Granularity]
    assert len(vals) == len(set(vals))


@pytest.mark.parametrize("member,expected_value", [
    (Granularity.M1,  "1m"),
    (Granularity.M5,  "5m"),
    (Granularity.M15, "15m"),
    (Granularity.H1,  "1h"),
    (Granularity.H4,  "4h"),
    (Granularity.D1,  "1d"),
    (Granularity.W1,  "1w"),
    (Granularity.MO1, "1mo"),
    (Granularity.Q1,  "1q"),
])
def test_granularity_individual_values(member: Granularity, expected_value: str) -> None:
    assert member.value == expected_value


@pytest.mark.parametrize("member,expected_source", [
    (Granularity.M1,  "minute_ohlcv"),
    (Granularity.M5,  "minute_5min_ohlcv"),
    (Granularity.M15, "minute_15min_ohlcv"),
    (Granularity.H1,  "minute_hourly_ohlcv"),
    (Granularity.H4,  "minute_4hour_ohlcv"),
    (Granularity.D1,  "daily_ohlcv"),
    (Granularity.W1,  "daily_weekly_ohlcv"),
    (Granularity.MO1, "daily_monthly_ohlcv"),
    (Granularity.Q1,  "daily_quarterly_ohlcv"),
])
def test_granularity_source_values(member: Granularity, expected_source: str) -> None:
    assert GRANULARITY_SOURCE[member] == expected_source


def test_granularity_is_str() -> None:
    """StrEnum members must compare equal to their string value."""
    assert Granularity.M1 == "1m"
    assert Granularity.D1 == "1d"
