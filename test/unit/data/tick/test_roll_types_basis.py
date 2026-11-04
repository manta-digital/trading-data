"""``basis_family`` on roll answers and segments (slice 303, D10)."""

from __future__ import annotations

from datetime import date

from tick_support.roll_synthetic import contract, inputs, sessions, weekdays

from manta_trading.data.tick.roll_resolve import resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import ActiveContract, RollSchedule
from manta_trading.data.tick.schema_families import SchemaFamily

FIRST = date(2024, 9, 2)
DAYS = weekdays(FIRST, 10)
RUN = sessions(FIRST, 10)
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))


def _answer(day: date, instrument: int, family: SchemaFamily | None) -> ActiveContract:
    return ActiveContract(
        session_date=day,
        instrument_id=instrument,
        raw_symbol=f"ES{instrument}",
        expiration=RUN[-1].close_utc,
        basis_session=day,
        definitions_as_of=FIRST,
        definition_unit_id=7,
        basis_unit_ids=(),
        basis_family=family,
    )


def _schedule(*answers: ActiveContract) -> RollSchedule:
    return RollSchedule("ES", RollRule.parse("v.0"), tuple(answers))


def test_a_segment_splits_at_a_basis_change_even_when_the_contract_is_unchanged() -> (
    None
):
    ticks, bars = SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M
    schedule = _schedule(
        _answer(DAYS[0], 118, ticks),
        _answer(DAYS[1], 118, ticks),
        _answer(DAYS[2], 118, bars),
        _answer(DAYS[3], 118, bars),
    )
    segments = schedule.segments()
    assert [(s.first_session, s.last_session) for s in segments] == [
        (DAYS[0], DAYS[1]),
        (DAYS[2], DAYS[3]),
    ]
    assert [s.contract.basis_family for s in segments] == [ticks, bars]


def test_a_run_on_one_basis_and_contract_is_one_segment() -> None:
    ticks = SchemaFamily.TICK_TIER
    schedule = _schedule(*(_answer(day, 118, ticks) for day in DAYS[:4]))
    (segment,) = schedule.segments()
    assert (segment.first_session, segment.last_session) == (DAYS[0], DAYS[3])


def test_a_calendar_answer_has_no_basis_family() -> None:
    run = inputs(RUN, [U, Z])
    answers = resolve_sessions(RollRule.parse("c.0"), run, FIRST, date(2024, 9, 14))
    assert answers
    assert all(
        isinstance(a, ActiveContract) and a.basis_family is None for a in answers
    )
