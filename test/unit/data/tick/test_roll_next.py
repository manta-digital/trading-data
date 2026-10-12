"""Segments and the next-roll forecast (229 TD7, API Contracts): no database."""

from __future__ import annotations

from datetime import date

from tick_support.roll_synthetic import contract, inputs, sessions, weekdays

from manta_trading.data.tick.roll_resolve import active_line, resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    RollSchedule,
    Segment,
    Unresolved,
)

FIRST = date(2024, 9, 2)
DAYS = weekdays(FIRST, 30)  # 2024-09-02 → 2024-10-11
RUN = sessions(FIRST, 30)
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))
V0, C0 = RollRule.parse("v.0"), RollRule.parse("c.0")
L = date(2024, 9, 10)


def _volumes(cross: date) -> dict[tuple[date, int], int]:
    """Z out-trades U from ``cross`` on."""
    rows: dict[tuple[date, int], int] = {}
    for day in DAYS:
        rows[day, U.instrument_id] = 100 if day < cross else 10
        rows[day, Z.instrument_id] = 50 if day < cross else 200
    return rows


def test_segments_break_at_an_unresolved_session() -> None:
    run = inputs(RUN, [U, Z], _volumes(date(2024, 9, 11)))
    answers = list(resolve_sessions(V0, run, DAYS[2], DAYS[12]))
    answers[3] = Unresolved(answers[3].session_date, "volume inputs: test gap")
    schedule = RollSchedule("ES", V0, tuple(answers))
    assert [
        (s.first_session, s.last_session, s.contract.raw_symbol)
        for s in schedule.segments()
    ] == [
        (DAYS[2], DAYS[4], "ESU4"),
        (DAYS[6], DAYS[8], "ESU4"),
        (DAYS[9], DAYS[11], "ESZ4"),
    ]
    assert all(isinstance(s, Segment) for s in schedule.segments())
    assert [u.session_date for u in schedule.unresolved] == [DAYS[5]]


def test_volume_roll_on_the_next_session() -> None:
    """The cross at L - 1 decides L + 1."""
    line = active_line("ES", V0, inputs(RUN, [U, Z], _volumes(date(2024, 9, 9))), L)
    assert isinstance(line.answer, ActiveContract)
    assert line.answer.raw_symbol == "ESU4"
    assert line.next_roll is not None
    assert line.next_roll.session_date == date(2024, 9, 11)
    assert line.next_roll.contract.raw_symbol == "ESZ4"
    assert line.known_through == date(2024, 9, 12)


def test_volume_roll_two_sessions_out() -> None:
    """The cross at L decides L + 2."""
    line = active_line("ES", V0, inputs(RUN, [U, Z], _volumes(L)), L)
    assert line.next_roll is not None
    assert line.next_roll.session_date == date(2024, 9, 12)


def test_volume_roll_beyond_two_sessions_gives_only_the_bound() -> None:
    line = active_line("ES", V0, inputs(RUN, [U, Z], _volumes(date(2024, 9, 30))), L)
    assert line.next_roll is None
    assert line.known_through == date(2024, 9, 12)
    # ESU4 expires in the session dated 09-20; the next session is 09-23.
    assert line.roll_no_later_than == date(2024, 9, 23)


def test_calendar_next_roll_is_exact() -> None:
    line = active_line("ES", C0, inputs(RUN, [U, Z]), L)
    assert line.next_roll is not None
    assert line.next_roll.session_date == date(2024, 9, 23)
    assert line.next_roll.contract.raw_symbol == "ESZ4"
    assert line.known_through == DAYS[-1]  # the calendar's last session
    assert line.roll_no_later_than is None


def test_unresolved_latest_session_has_no_forecast() -> None:
    line = active_line("ES", V0, inputs(RUN, [U, Z], _volumes(L)), DAYS[1])
    assert isinstance(line.answer, Unresolved)
    assert (line.next_roll, line.known_through, line.roll_no_later_than) == (
        None,
        None,
        None,
    )
