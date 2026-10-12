"""The volume rule (229 TD3, FR4, FR6a) on synthetic inputs: no database.

Session S ranks by the volume of S - 2 (``VOLUME_RANK_LAG_SESSIONS``).
"""

from __future__ import annotations

from datetime import date

import pytest
from tick_support.roll_synthetic import (
    contract,
    inputs,
    sessions,
    symbols,
    unit_ids,
    weekdays,
)

from manta_trading.data.tick.roll_resolve import resolve_sessions
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    RollInputError,
    Unresolved,
)
from manta_trading.data.tick.tick_status import TickSessionStatus

FIRST = date(2024, 9, 2)
DAYS = weekdays(FIRST, 15)  # 2024-09-02 → 2024-09-20
RUN = sessions(FIRST, 15)
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))
H = contract(5002, "ESH5", FIRST, date(2025, 3, 21))
CROSS = date(2024, 9, 11)  # Z out-trades U from this session on
V0, V1 = RollRule.parse("v.0"), RollRule.parse("v.1")


def _volumes(cross: date = CROSS) -> dict[tuple[date, int], int]:
    rows: dict[tuple[date, int], int] = {}
    for day in DAYS:
        rows[day, U.instrument_id] = 100 if day < cross else 10
        rows[day, Z.instrument_id] = 50 if day < cross else 200
        rows[day, H.instrument_id] = 1
    return rows


def _resolve(rule: RollRule, volumes: dict[tuple[date, int], int]) -> list[str]:
    run = inputs(RUN, [U, Z, H], volumes)
    return symbols(resolve_sessions(rule, run, FIRST, date(2024, 9, 21)))


def test_the_switch_shows_two_sessions_after_the_cross() -> None:
    rolled = dict(zip(DAYS, _resolve(V0, volumes=_volumes()), strict=True))
    assert rolled[date(2024, 9, 12)] == "ESU4"  # ranked by 09-10
    assert rolled[date(2024, 9, 13)] == "ESZ4"  # ranked by 09-11, the cross


def test_first_two_sessions_have_no_lag_session() -> None:
    answers = _resolve(V0, volumes=_volumes())
    assert answers[:2] == ["-", "-"]
    assert answers[2] == "ESU4"


def test_tie_goes_to_the_earlier_expiration() -> None:
    tied = {key: 5 for key in _volumes()}
    assert set(_resolve(V0, volumes=tied)[2:]) == {"ESU4"}


def test_incomplete_lag_session_is_unresolved_naming_it_and_its_status() -> None:
    statuses = {
        DAYS[0]: TickSessionStatus.MISSING,
        DAYS[1]: TickSessionStatus.MISSING,
        DAYS[5]: TickSessionStatus.AWAITING_INGEST,
    }
    run = inputs(RUN, [U, Z, H], _volumes(), statuses)
    answers = resolve_sessions(V0, run, DAYS[2], DAYS[8])
    unresolved = [a for a in answers if isinstance(a, Unresolved)]
    assert [(a.session_date, a.reason) for a in unresolved] == [
        (DAYS[2], f"volume inputs: session {DAYS[0]} is missing"),
        (DAYS[3], f"volume inputs: session {DAYS[1]} is missing"),
        (DAYS[7], f"volume inputs: session {DAYS[5]} is awaiting_ingest"),
    ]


def test_contract_listed_after_its_lag_session_is_excluded_then_ranked() -> None:
    """N lists on 09-12 (activation = 09-11 22:00 UTC). For S = 09-13 the lag
    session 09-11 closed before that: N is not ranked and, with no ledger row
    there, raises nothing. From S = 09-16 (lag 09-12) it ranks and leads."""
    new = contract(9, "ESN", date(2024, 9, 12), date(2025, 6, 20))
    volumes = _volumes()
    for day in DAYS[DAYS.index(date(2024, 9, 12)) :]:
        volumes[day, new.instrument_id] = 10_000
    run = inputs(RUN, [U, Z, H, new], volumes)
    rolled = dict(
        zip(DAYS, symbols(resolve_sessions(V0, run, FIRST, DAYS[-1])), strict=False)
    )
    assert rolled[date(2024, 9, 13)] == "ESZ4"
    assert rolled[date(2024, 9, 16)] == "ESN"


def test_missing_row_for_a_listed_contract_raises_naming_it() -> None:
    volumes = _volumes()
    del volumes[DAYS[4], Z.instrument_id]
    run = inputs(RUN, [U, Z, H], volumes)
    with pytest.raises(RollInputError) as caught:
        resolve_sessions(V0, run, DAYS[6], DAYS[7])
    assert f"ESZ4 (183748) is listed in session {DAYS[4]}" in str(caught.value)


def test_rank_one_keeps_the_old_front_while_it_is_listed() -> None:
    """Walkthrough step 4's shape: after the switch, v.1 is the old front
    until it expires, then the next by volume."""
    rolled = dict(zip(DAYS, _resolve(V1, volumes=_volumes()), strict=True))
    assert rolled[date(2024, 9, 12)] == "ESZ4"
    assert rolled[date(2024, 9, 13)] == "ESU4"
    assert rolled[date(2024, 9, 20)] == "ESU4"  # its expiration session


def test_answer_names_its_basis_and_units() -> None:
    run = inputs(RUN, [U, Z, H], _volumes())
    (answer,) = resolve_sessions(V0, run, DAYS[4], DAYS[5])
    assert isinstance(answer, ActiveContract)
    assert answer.basis_session == DAYS[2]
    assert answer.basis_unit_ids == unit_ids(DAYS[2])
    assert answer.definition_unit_id == U.unit_id
