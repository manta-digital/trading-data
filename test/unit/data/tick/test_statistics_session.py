"""Pure session assignment of a statistics record (slice 303, D4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from manta_trading.data.tick.ingest_checks import StatReferenceProblem
from manta_trading.data.tick.statistics_constants import (
    REFERENCED_STAT_TYPES,
    StatType,
)
from manta_trading.data.tick.statistics_session import (
    NO_SESSION_ASSIGNED,
    NS_PER_DAY,
    ReferenceCalendar,
    SessionAssignment,
    assign_session,
    utc_date_of_ns,
)
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

CALENDAR_ID = "CME_EQUITY"
#: Every weekday of late 2024 except Thanksgiving.
_DAYS = [date(2024, 11, 1) + timedelta(days=n) for n in range(61)]
SESSIONS = frozenset(
    day for day in _DAYS if day.weekday() < 5 and day != date(2024, 11, 28)
)
CALENDAR = ReferenceCalendar(CALENDAR_ID, SESSIONS)
UNIT = date(2024, 12, 4)
#: Intraday statistic with no trading date (LOWEST_OFFER in the DBN samples).
OTHER_TYPE = 7


def _ns(day: date, hour: int = 0, minute: int = 0) -> int:
    instant = datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)
    return int(instant.timestamp()) * 1_000_000_000


@pytest.mark.parametrize("stat_type", sorted(REFERENCED_STAT_TYPES))
def test_a_referenced_type_with_a_session_date_gets_that_session(
    stat_type: StatType,
) -> None:
    assert assign_session(stat_type, _ns(date(2024, 12, 3)), UNIT, CALENDAR) == (
        SessionAssignment(CALENDAR_ID, date(2024, 12, 3))
    )


def test_the_unit_s_own_date_is_allowed() -> None:
    result = assign_session(StatType.OPEN_INTEREST, _ns(UNIT), UNIT, CALENDAR)
    assert result == SessionAssignment(CALENDAR_ID, UNIT)


def test_another_type_stays_sessionless_with_or_without_a_reference_time() -> None:
    assert assign_session(OTHER_TYPE, _ns(UNIT), UNIT, CALENDAR) is NO_SESSION_ASSIGNED
    assert assign_session(OTHER_TYPE, None, UNIT, CALENDAR) is NO_SESSION_ASSIGNED


@pytest.mark.parametrize("stat_type", sorted(REFERENCED_STAT_TYPES))
def test_an_undefined_reference_time_fails_a_referenced_type(
    stat_type: StatType,
) -> None:
    assert assign_session(stat_type, None, UNIT, CALENDAR) is (
        StatReferenceProblem.UNDEFINED
    )


@pytest.mark.parametrize(
    "day", [date(2024, 11, 30), date(2024, 12, 1), date(2024, 11, 28)]
)
def test_a_weekend_or_holiday_is_not_a_session(day: date) -> None:
    result = assign_session(StatType.SETTLEMENT_PRICE, _ns(day), UNIT, CALENDAR)
    assert result is StatReferenceProblem.NOT_A_SESSION


def test_a_date_after_the_unit_fails() -> None:
    result = assign_session(
        StatType.CLEARED_VOLUME, _ns(date(2024, 12, 5)), UNIT, CALENDAR
    )
    assert result is StatReferenceProblem.AFTER_THE_UNIT


def test_a_date_before_the_dataset_is_sessionless_and_flagged() -> None:
    before = CME_DATASET_START - timedelta(days=1)
    result = assign_session(StatType.SETTLEMENT_PRICE, _ns(before), UNIT, CALENDAR)
    assert result == SessionAssignment(None, None, before_dataset=True)
    assert result != NO_SESSION_ASSIGNED


def test_the_dataset_start_date_itself_is_checked_against_the_calendar() -> None:
    # CME_DATASET_START is a Sunday: on the day, not before it.
    assert CME_DATASET_START.weekday() == 6
    result = assign_session(
        StatType.SETTLEMENT_PRICE, _ns(CME_DATASET_START), UNIT, CALENDAR
    )
    assert result is StatReferenceProblem.NOT_A_SESSION


def test_any_instant_of_the_session_date_gives_the_same_session() -> None:
    day = date(2024, 12, 3)
    start = assign_session(StatType.SETTLEMENT_PRICE, _ns(day), UNIT, CALENDAR)
    end = assign_session(StatType.SETTLEMENT_PRICE, _ns(day, 23, 59), UNIT, CALENDAR)
    assert start == end == SessionAssignment(CALENDAR_ID, day)


def test_the_utc_date_of_an_instant_rolls_at_midnight() -> None:
    day = date(2024, 12, 3)
    assert utc_date_of_ns(_ns(day) - 1) == date(2024, 12, 2)
    assert utc_date_of_ns(_ns(day)) == day
    assert utc_date_of_ns(_ns(day) + NS_PER_DAY - 1) == day
