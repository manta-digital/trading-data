"""The calendar rule (229 TD2, FR5) on synthetic sessions: no database."""

from __future__ import annotations

from datetime import date

import pytest
from tick_support.roll_synthetic import contract, inputs, sessions, symbols

from manta_trading.data.tick.constants import RollMethod
from manta_trading.data.tick.roll_resolve import resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    RollInputError,
    Unresolved,
)

FIRST = date(2024, 9, 2)
RUN = sessions(FIRST, 30)  # 2024-09-02 → 2024-10-11
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))
H = contract(5002, "ESH5", FIRST, date(2025, 3, 21))
M = contract(4916, "ESM5", FIRST, date(2025, 6, 20))
INPUTS = inputs(RUN, [H, U, M, Z])  # any order


def _roll(rule: str, start: date, end: date) -> list[tuple[date, str]]:
    answers = resolve_sessions(RollRule.parse(rule), INPUTS, start, end)
    return [(a.session_date, s) for a, s in zip(answers, symbols(answers), strict=True)]


def _first_of(rule: str, symbol: str) -> date:
    return next(d for d, s in _roll(rule, FIRST, date(2024, 10, 1)) if s == symbol)


def test_front_holds_through_its_expiration_session() -> None:
    """ESU4 expires 09-20 13:30 UTC, inside the session dated 09-20 (TD2)."""
    rolled = dict(_roll("c.0", date(2024, 9, 18), date(2024, 9, 25)))
    assert rolled == {
        date(2024, 9, 18): "ESU4",
        date(2024, 9, 19): "ESU4",
        date(2024, 9, 20): "ESU4",
        date(2024, 9, 23): "ESZ4",
        date(2024, 9, 24): "ESZ4",
    }


@pytest.mark.parametrize(
    ("rule", "first_z"),
    [
        ("c.0", date(2024, 9, 23)),
        ("c.0+1s", date(2024, 9, 20)),
        ("c.0+5s", date(2024, 9, 16)),
    ],
)
def test_offset_rolls_that_many_sessions_earlier(rule: str, first_z: date) -> None:
    assert _first_of(rule, "ESZ4") == first_z


def test_offset_five_is_exactly_five_sessions_before_plain() -> None:
    days = [s.session_date for s in RUN]
    plain = days.index(_first_of("c.0", "ESZ4"))
    assert days.index(_first_of("c.0+5s", "ESZ4")) == plain - 5


def test_expired_contract_drops_out_of_rank_one() -> None:
    rolled = dict(_roll("c.1", date(2024, 9, 19), date(2024, 9, 24)))
    assert rolled == {
        date(2024, 9, 19): "ESZ4",
        date(2024, 9, 20): "ESZ4",
        date(2024, 9, 23): "ESH5",
    }


def test_contract_not_listed_yet_is_not_a_candidate() -> None:
    late = contract(1, "ESX", date(2024, 9, 10), date(2024, 9, 13))
    run = inputs(RUN, [U, Z, late])
    answers = resolve_sessions(
        RollRule(RollMethod.CALENDAR), run, FIRST, FIRST.replace(day=14)
    )
    assert symbols(answers) == ["ESU4"] * 6 + ["ESX"] * 4


def test_rank_deeper_than_candidates_is_unresolved_naming_both() -> None:
    (answer,) = resolve_sessions(
        RollRule.parse("c.5"), INPUTS, FIRST, FIRST.replace(day=3)
    )
    assert isinstance(answer, Unresolved)
    assert "rank 5" in answer.reason
    assert "only 4 contracts" in answer.reason


def test_expiration_past_the_calendar_read_is_not_an_error() -> None:
    """ESH5 and ESM5 expire long after the last session passed in."""
    answers = resolve_sessions(
        RollRule.parse("c.3"), INPUTS, FIRST, FIRST.replace(day=3)
    )
    assert symbols(answers) == ["ESM5"]
    assert isinstance(answers[0], ActiveContract)
    assert answers[0].basis_unit_ids == ()
    assert answers[0].basis_session == FIRST


def test_a_needed_session_past_the_calendar_read_raises_naming_extend() -> None:
    last = RUN[-1].session_date
    with pytest.raises(RollInputError, match="mt data extend"):
        resolve_sessions(RollRule.parse("c.0+5s"), INPUTS, last, date(2024, 10, 12))
