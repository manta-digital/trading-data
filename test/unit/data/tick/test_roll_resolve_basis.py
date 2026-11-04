"""The volume rule's basis per session (slice 303, D10): ticks first, else bars.

Session S ranks by the volume of S - 2; the family that volume is read from is
chosen per lag session and never mixed within one.
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
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus

FIRST = date(2024, 9, 2)
DAYS = weekdays(FIRST, 12)
RUN = sessions(FIRST, 12)
U = contract(118, "ESU4", FIRST, date(2024, 9, 20))
Z = contract(183748, "ESZ4", FIRST, date(2024, 12, 20))
V0 = RollRule.parse("v.0")
TICKS, BARS = SchemaFamily.TICK_TIER, SchemaFamily.BAR_1M
INCOMPLETE = TickSessionStatus.AWAITING_INGEST


def _volumes(u: int, z: int) -> dict[tuple[date, int], int]:
    return {
        (day, instrument): volume
        for day in DAYS
        for instrument, volume in ((U.instrument_id, u), (Z.instrument_id, z))
    }


def _answers(**kwargs: object) -> list[ActiveContract | Unresolved]:
    run = inputs(RUN, [U, Z], **kwargs)  # type: ignore[arg-type]
    return list(resolve_sessions(V0, run, DAYS[2], DAYS[8]))


def test_ticks_complete_means_the_tick_basis_even_when_bars_are_complete_too() -> None:
    answers = _answers(volumes=_volumes(100, 10), bar_volumes=_volumes(10, 100))
    assert symbols(answers) == ["ESU4"] * 6  # the tick volumes decide, not bars'
    assert {a.basis_family for a in answers if isinstance(a, ActiveContract)} == {TICKS}


def test_the_basis_units_come_from_the_chosen_family() -> None:
    answers = _answers(volumes=_volumes(100, 10), bar_volumes=_volumes(10, 100))
    first = answers[0]
    assert isinstance(first, ActiveContract)
    assert first.basis_unit_ids == tuple(sorted(unit_ids(DAYS[0])))


def test_ticks_incomplete_and_bars_complete_means_the_bar_basis() -> None:
    answers = _answers(
        volumes=_volumes(100, 10),
        bar_volumes=_volumes(10, 100),
        statuses={DAYS[0]: INCOMPLETE},
        bar_statuses={},
    )
    first, second = answers[0], answers[1]
    assert isinstance(first, ActiveContract) and isinstance(second, ActiveContract)
    # Session DAYS[2] ranks on DAYS[0]: ticks incomplete there, bars decide.
    assert (first.raw_symbol, first.basis_family) == ("ESZ4", BARS)
    # Session DAYS[3] ranks on DAYS[1]: ticks are complete, ticks decide.
    assert (second.raw_symbol, second.basis_family) == ("ESU4", TICKS)


def test_neither_complete_is_unresolved_naming_both_statuses() -> None:
    answers = _answers(
        volumes=_volumes(100, 10),
        bar_volumes=_volumes(10, 100),
        statuses={DAYS[0]: INCOMPLETE},
        bar_statuses={DAYS[0]: TickSessionStatus.MISSING},
    )
    first = answers[0]
    assert isinstance(first, Unresolved)
    assert first.reason == (
        f"volume inputs: session {DAYS[0]} is awaiting_ingest in tick_tier"
        " and missing in bar_1m"
    )


def test_a_family_that_was_not_read_is_left_out_of_the_reason() -> None:
    answers = _answers(volumes=_volumes(100, 10), statuses={DAYS[0]: INCOMPLETE})
    first = answers[0]
    assert isinstance(first, Unresolved)
    assert first.reason == f"volume inputs: session {DAYS[0]} is awaiting_ingest"


def test_a_bar_session_missing_a_listed_contracts_row_raises_i13() -> None:
    bars = {
        k: v for k, v in _volumes(10, 100).items() if k != (DAYS[0], Z.instrument_id)
    }
    with pytest.raises(RollInputError, match="bar_1m ledger row.*I13"):
        _answers(
            volumes=_volumes(100, 10),
            bar_volumes=bars,
            statuses={DAYS[0]: INCOMPLETE},
        )


def test_every_volume_answer_names_a_volume_family() -> None:
    answers = _answers(
        volumes=_volumes(100, 10),
        bar_volumes=_volumes(10, 100),
        statuses={DAYS[0]: INCOMPLETE, DAYS[2]: INCOMPLETE},
    )
    families = {a.basis_family for a in answers if isinstance(a, ActiveContract)}
    assert families == {TICKS, BARS}
