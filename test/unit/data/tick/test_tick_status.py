"""Session classification for status (slice 225, TD9; FR9 in part)."""

from __future__ import annotations

from datetime import date

import pytest

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import DatasetCondition, TickSchema, UnitState
from manta_trading.data.tick.tick_status import (
    STATUS_PRECEDENCE,
    DayFacts,
    DayUnit,
    TickSessionStatus,
    caught_up,
    classify_day,
    classify_session,
    day_unit,
    session_condition,
    verdict,
)

S = TickSessionStatus
C = DatasetCondition
MON, TUE = date(2024, 9, 2), date(2024, 9, 3)
SUN, SAT = date(2024, 9, 1), date(2024, 8, 31)


def _unit(
    state: UnitState = UnitState.INGESTED,
    status: FetchStatus = FetchStatus.UNKNOWN,
    schema: TickSchema = TickSchema.TRADES,
    unit_id: int = 1,
) -> DayUnit:
    return DayUnit(unit_id, schema, state, status)


def _day(
    unit: DayUnit | None, condition: C | None = C.AVAILABLE, day: date = TUE
) -> DayFacts:
    return DayFacts(day, unit, condition)


@pytest.mark.parametrize(
    ("facts", "expected"),
    [
        (_day(_unit(status=FetchStatus.RETRY_EXHAUSTED)), S.RETRY_EXHAUSTED),
        (_day(_unit(status=FetchStatus.FAILED_RETRYABLE)), S.FAILED),
        (_day(_unit(UnitState.DELIVERED, FetchStatus.PROVIDER_HOLE)), S.PROVIDER_HOLE),
        (_day(None, C.MISSING), S.PROVIDER_HOLE),
        (_day(_unit(UnitState.REQUESTED)), S.IN_FLIGHT),
        (_day(_unit(UnitState.SUBMITTED)), S.IN_FLIGHT),
        (_day(_unit(UnitState.DELIVERED)), S.IN_FLIGHT),
        (_day(_unit(UnitState.DOWNLOADED)), S.AWAITING_INGEST),
        (_day(_unit(UnitState.VERIFIED)), S.AWAITING_INGEST),
        (_day(None, C.AVAILABLE), S.MISSING),
        (_day(None, C.DEGRADED), S.MISSING),
        (_day(None, C.PENDING), S.PENDING),
        (_day(None, None), S.EDGE_UNKNOWN),
        (_day(_unit()), S.COMPLETE),
    ],
)
def test_each_bucket(facts: DayFacts, expected: S) -> None:
    assert classify_day(facts) is expected


def test_every_bucket_is_reachable_and_ordered_worst_first() -> None:
    assert STATUS_PRECEDENCE[0] is S.RETRY_EXHAUSTED
    assert STATUS_PRECEDENCE[-1] is S.COMPLETE
    assert set(STATUS_PRECEDENCE) == set(S)


def test_exhaustion_outranks_the_units_state() -> None:
    """Two rules match (verified and exhausted): the worse wins."""
    exhausted = _unit(UnitState.VERIFIED, FetchStatus.RETRY_EXHAUSTED)
    assert classify_day(_day(exhausted)) is S.RETRY_EXHAUSTED


def test_a_session_takes_the_worst_of_its_days() -> None:
    days = [_day(_unit(), day=MON), _day(_unit(UnitState.VERIFIED), day=TUE)]
    assert classify_session(days) is S.AWAITING_INGEST
    days = [_day(None, C.PENDING, day=MON), _day(None, None, day=TUE)]
    assert classify_session(days) is S.PENDING


def test_the_day_unit_is_the_highest_ranked_tier() -> None:
    trades = _unit(schema=TickSchema.TRADES, unit_id=9)
    tbbo = _unit(UnitState.REQUESTED, schema=TickSchema.TBBO, unit_id=4)
    assert day_unit([trades, tbbo]) is tbbo
    assert day_unit([]) is None


def test_condition_is_the_worst_day_and_unknown_when_a_day_lacks_a_row() -> None:
    assert session_condition(
        [_day(None, C.AVAILABLE, MON), _day(None, C.DEGRADED)]
    ) == ("degraded")
    assert session_condition([_day(None, C.MISSING, MON), _day(None, C.PENDING)]) == (
        "missing"
    )
    assert session_condition([_day(None, C.AVAILABLE, MON), _day(None, None)]) == (
        "unknown"
    )


def test_saturdays_are_exempt_from_the_condition() -> None:
    days = [_day(None, None, SAT), _day(None, C.AVAILABLE, SUN)]
    assert session_condition(days) == "available"


def test_a_complete_session_with_a_degraded_day_is_complete_and_degraded() -> None:
    v = verdict(TUE, [_day(_unit(), C.AVAILABLE, MON), _day(_unit(), C.DEGRADED)])
    assert (v.status, v.condition) == (S.COMPLETE, "degraded")


def test_caught_up_needs_a_wanted_range() -> None:
    complete = verdict(TUE, [_day(_unit())])
    hole_on_missing = verdict(MON, [_day(None, C.MISSING, MON)])
    held_hole = verdict(
        MON, [_day(_unit(UnitState.DELIVERED, FetchStatus.PROVIDER_HOLE), C.AVAILABLE)]
    )
    assert caught_up([complete], wanted=False) is None
    assert caught_up([complete, hole_on_missing], wanted=True) is True
    assert caught_up([complete, held_hole], wanted=True) is False
    assert caught_up([verdict(TUE, [_day(None, C.AVAILABLE)])], wanted=True) is False
