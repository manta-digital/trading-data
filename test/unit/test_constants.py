"""Tests for manta_trading.constants module."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from manta_trading.constants import (
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_REFRESH_MIN_WINDOW_BUCKETS,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    DAILY_HISTORY_MONTHS,
    DAILY_STALENESS_THRESHOLD,
    EODHD_INTRADAY_HORIZON,
    LATE_BAR_GRACE_PERIOD,
    MAX_GAP_STALENESS,
    MAX_RETRY_COUNT,
    MINUTE_CAGG_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
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


# --- Slice 167: coverage continuous aggregates -----------------------------


def test_coverage_view_names_are_non_empty_strings() -> None:
    for view_name in (MINUTE_COVERAGE_VIEW, DAILY_COVERAGE_VIEW):
        assert isinstance(view_name, str)
        assert view_name.strip() == view_name
        assert view_name


def test_coverage_view_names_are_distinct() -> None:
    assert MINUTE_COVERAGE_VIEW != DAILY_COVERAGE_VIEW


def test_coverage_bucket_interval_type_and_value() -> None:
    assert isinstance(COVERAGE_BUCKET_INTERVAL, timedelta)
    assert COVERAGE_BUCKET_INTERVAL == timedelta(days=365)


@pytest.mark.parametrize(
    "interval",
    [
        MINUTE_COVERAGE_REFRESH_START_OFFSET,
        MINUTE_COVERAGE_REFRESH_END_OFFSET,
        MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
        DAILY_COVERAGE_REFRESH_START_OFFSET,
        DAILY_COVERAGE_REFRESH_END_OFFSET,
        DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    ],
)
def test_coverage_refresh_intervals_are_positive_timedeltas(
    interval: timedelta,
) -> None:
    assert isinstance(interval, timedelta)
    assert interval > timedelta(0)


def test_minute_coverage_start_offset_exceeds_parent_refresh_window() -> None:
    """Encodes the slice-167 D4 constraint mechanically.

    ``minute_coverage`` is hierarchical over ``minute_4hour_ohlcv``. A
    ``start_offset`` that does not comfortably exceed the parent's own refresh
    window leaves parent buckets that changed after the coverage cagg last ran
    permanently un-rematerialized — no scheduled run revisits data older than
    ``start_offset``. That is the exact failure that produced the ~79%
    under-materialization slice 163 had to repair.

    Asserted against the measured parent constant, not a literal, so the two
    cannot drift apart.
    """
    assert MINUTE_COVERAGE_REFRESH_START_OFFSET > MINUTE_CAGG_REFRESH_START_OFFSET
    # Strictly greater is not enough on its own -- require real margin, so a
    # future edit cannot shave this down to "parent + one minute" and pass.
    assert MINUTE_COVERAGE_REFRESH_START_OFFSET >= MINUTE_CAGG_REFRESH_START_OFFSET * 7


def test_daily_coverage_start_offset_covers_revision_window() -> None:
    """``daily_coverage`` reads raw ``daily_ohlcv``, which has no refresh policy.

    No parent refresh window to clear, so the binding constraint is the engine's
    two-bucket minimum (asserted below) plus the daily revision window --
    provider restatements and adjustment rebasing.
    """
    assert DAILY_COVERAGE_REFRESH_START_OFFSET >= MINUTE_CAGG_REFRESH_START_OFFSET * 7


@pytest.mark.parametrize(
    ("start_offset", "end_offset"),
    [
        (MINUTE_COVERAGE_REFRESH_START_OFFSET, MINUTE_COVERAGE_REFRESH_END_OFFSET),
        (DAILY_COVERAGE_REFRESH_START_OFFSET, DAILY_COVERAGE_REFRESH_END_OFFSET),
    ],
)
def test_coverage_refresh_window_satisfies_timescale_minimum(
    start_offset: timedelta, end_offset: timedelta
) -> None:
    """TimescaleDB rejects a policy whose window spans under two buckets.

    ``add_continuous_aggregate_policy`` raises ``InvalidParameterValue: policy
    refresh window too small`` unless
    ``start_offset - end_offset >= 2 * bucket``. A refresh only re-materializes
    buckets *fully contained* in its window, so a narrower window can slide into
    a position containing no whole bucket and silently refresh nothing.

    Verified empirically on TimescaleDB 2.21.3 with the 1-year bucket: 730 days
    rejected, 731 accepted. Asserted here so a change to
    ``COVERAGE_BUCKET_INTERVAL`` fails at test time rather than at migration
    time against a live database.
    """
    minimum_window = COVERAGE_REFRESH_MIN_WINDOW_BUCKETS * COVERAGE_BUCKET_INTERVAL
    assert start_offset - end_offset >= minimum_window


def test_coverage_start_offsets_exceed_end_offsets() -> None:
    """A refresh window must look further back than it stops -- otherwise the
    policy describes an empty or inverted range."""
    assert MINUTE_COVERAGE_REFRESH_START_OFFSET > MINUTE_COVERAGE_REFRESH_END_OFFSET
    assert DAILY_COVERAGE_REFRESH_START_OFFSET > DAILY_COVERAGE_REFRESH_END_OFFSET
