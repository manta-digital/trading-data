"""Tests for manta_trading.constants module."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from manta_trading.constants import (
    API_MAX_BARS_PER_REQUEST,
    API_SERVING_SESSION,
    BARS_PER_TRADING_DAY,
    COVERAGE_BUCKET_INTERVAL,
    COVERAGE_REFRESH_MIN_WINDOW_BUCKETS,
    DAILY_COVERAGE_REFRESH_END_OFFSET,
    DAILY_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    DAILY_COVERAGE_REFRESH_START_OFFSET,
    DAILY_COVERAGE_VIEW,
    DAILY_CYCLE_RETRY_INTERVAL,
    DAILY_CYCLE_START_OFFSET,
    DAILY_HISTORY_MONTHS,
    DAILY_STALENESS_THRESHOLD,
    DB_BULK_SESSION,
    EODHD_INTRADAY_HORIZON,
    GRANULARITY_BAR_MINUTES,
    INTRADAY_MINUTES_PER_TRADING_DAY,
    LATE_BAR_GRACE_PERIOD,
    MAX_GAP_STALENESS,
    MAX_RETRY_COUNT,
    MINUTE_CAGG_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_REFRESH_END_OFFSET,
    MINUTE_COVERAGE_REFRESH_SCHEDULE_INTERVAL,
    MINUTE_COVERAGE_REFRESH_START_OFFSET,
    MINUTE_COVERAGE_VIEW,
    MINUTE_STALENESS_THRESHOLD,
    TRADING_DAYS_PER_CALENDAR_DAY,
    Granularity,
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


# The three assertions below are deliberately independent (slice 912 D3).
# LATE_BAR_GRACE_PERIOD is an offset from session_close_utc; the two
# DAILY_CYCLE_* constants govern the daemon's daily-pass gating and are offsets
# from UTC midnight and from the previous cycle's end respectively. As of the
# 912 code review all three carry the same duration — which is precisely when
# collapsing them starts to look reasonable and is exactly when it is most
# wrong. If you are here because tuning one broke another's test, the fix is to
# update only the constant you meant to change, never to re-copy the value
# across and never to define one in terms of another.


def test_daily_cycle_start_offset_type_and_value() -> None:
    assert isinstance(DAILY_CYCLE_START_OFFSET, timedelta)
    assert DAILY_CYCLE_START_OFFSET == timedelta(minutes=30)


def test_daily_cycle_retry_interval_type_and_value() -> None:
    assert isinstance(DAILY_CYCLE_RETRY_INTERVAL, timedelta)
    assert DAILY_CYCLE_RETRY_INTERVAL == timedelta(minutes=30)


def test_cadence_constants_are_separately_defined() -> None:
    """Equal values must not become a shared definition.

    Asserting distinct identity is not pedantry here: the three are equal today,
    so a future edit that aliases one to another would pass every value
    assertion above while silently coupling a retry cadence to a session-close
    offset. Tuning either would then drag the other along.
    """
    values = [
        LATE_BAR_GRACE_PERIOD,
        DAILY_CYCLE_START_OFFSET,
        DAILY_CYCLE_RETRY_INTERVAL,
    ]
    assert len({id(v) for v in values}) == len(values), (
        "cadence constants share an object — they were aliased, not co-tuned"
    )


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


# --- Slice 186: session settings and range-cap derivation inputs -------------


def test_bulk_session_holds_the_historical_values() -> None:
    """The named bulk defaults must equal what every DB class used before 186.

    If this drifts, the CLI and daemon silently change behavior.
    """
    assert DB_BULK_SESSION.work_mem == "512MB"
    assert DB_BULK_SESSION.statement_timeout == "300s"


def test_api_serving_session_is_tighter_than_bulk() -> None:
    assert API_SERVING_SESSION.work_mem == "64MB"
    assert API_SERVING_SESSION.statement_timeout == "20s"
    assert API_SERVING_SESSION != DB_BULK_SESSION


def test_db_session_settings_is_frozen() -> None:
    """Session settings are shared module-level constants; mutation would leak
    across every pool that holds a reference."""
    with pytest.raises(FrozenInstanceError):
        API_SERVING_SESSION.work_mem = "1GB"  # type: ignore[misc]


@pytest.mark.parametrize("granularity", list(Granularity))
def test_bars_per_trading_day_covers_every_granularity(
    granularity: Granularity,
) -> None:
    """A granularity missing from this table would make the admission cap
    (D4) raise a KeyError on a request FastAPI already validated."""
    assert granularity in BARS_PER_TRADING_DAY
    assert BARS_PER_TRADING_DAY[granularity] > 0


@pytest.mark.parametrize(
    ("granularity", "expected"),
    [
        (Granularity.M1, 960.0),
        (Granularity.M5, 192.0),
        (Granularity.M15, 64.0),
        (Granularity.H1, 16.0),
        (Granularity.H4, 4.0),
    ],
)
def test_intraday_bars_per_day_are_derived_not_literal(
    granularity: Granularity, expected: float
) -> None:
    """Pins the derivation, not the numbers: these follow from the measured
    960-minute trading day divided by each bucket width. Correcting the
    measurement must move all five together."""
    assert BARS_PER_TRADING_DAY[granularity] == expected
    assert (
        BARS_PER_TRADING_DAY[granularity]
        == INTRADAY_MINUTES_PER_TRADING_DAY / GRANULARITY_BAR_MINUTES[granularity]
    )


def test_granularity_bar_minutes_covers_only_intraday() -> None:
    """Daily and coarser grains have no minute bucket width; including them
    would make the intraday derivation produce nonsense."""
    assert set(GRANULARITY_BAR_MINUTES) == {
        Granularity.M1,
        Granularity.M5,
        Granularity.M15,
        Granularity.H1,
        Granularity.H4,
    }


def test_range_cap_inputs_are_sane() -> None:
    assert API_MAX_BARS_PER_REQUEST == 75_000
    assert INTRADAY_MINUTES_PER_TRADING_DAY == 960
    assert 0 < TRADING_DAYS_PER_CALENDAR_DAY < 1
