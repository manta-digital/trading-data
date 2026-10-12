"""``build_schedule`` and ``latest_active`` on a scratch tick database (229).

The September 2024 roll is seeded shaped like production (``roll_seed``);
the calendar is the scratch production database's CME_EQUITY.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
import pytest
from tick_support.roll_seed import (
    ESZ4,
    SeededRoll,
    seed_september_roll,
)
from tick_support.rows import (
    FILE_COLUMNS,
    insert_ledger_row,
    insert_request,
    insert_unit,
)
from tick_support.universe import UNTIERED

from manta_trading.data.tick.active_contract import (
    RollRangeTooLong,
    build_schedule,
    latest_active,
)
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.roll_rules import STATUS_ROLL_RULES, RollRule
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    RollInputError,
    Unresolved,
)

ES = UNTIERED[0]
V0, C0 = RollRule.parse("v.0"), RollRule.parse("c.0")
START, END = date(2024, 9, 5), date(2024, 9, 28)
#: Nothing listens here: a connection to it fails at once.
UNREACHABLE = "postgresql://nobody@127.0.0.1:9/none"


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_september_roll(migrated_tick_db)


def _segments(schedule: Any) -> list[tuple[date, date, str]]:
    return [
        (s.first_session, s.last_session, s.contract.raw_symbol)
        for s in schedule.segments()
    ]


def _delete_row(url: str, day: date, instrument_id: int) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM tick_ingest_ledger WHERE session_date = %s"
            " AND instrument_id = %s",
            (day, instrument_id),
        )


async def test_seeded_roll_resolves_under_volume_and_calendar(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    volume = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, V0, START, END
    )
    assert _segments(volume) == [
        (START, date(2024, 9, 17), "ESU4"),
        (date(2024, 9, 18), date(2024, 9, 27), "ESZ4"),
    ]
    assert volume.unresolved == ()
    calendar = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, C0, START, END
    )
    assert _segments(calendar) == [
        (START, date(2024, 9, 20), "ESU4"),
        (date(2024, 9, 23), date(2024, 9, 27), "ESZ4"),
    ]


async def test_first_sessions_without_a_held_lag_are_unresolved(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    schedule = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, V0, date(2024, 9, 3), START
    )
    assert [(u.session_date, u.reason) for u in schedule.unresolved] == [
        (date(2024, 9, 3), "volume inputs: session 2024-08-30 is edge_unknown"),
        (date(2024, 9, 4), "volume inputs: session 2024-09-02 is edge_unknown"),
    ]


async def test_missing_ledger_row_raises_naming_contract_and_session(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _delete_row(migrated_tick_db, date(2024, 9, 10), ESZ4.instrument_id)
    with pytest.raises(RollInputError, match="ESZ4 .* session 2024-09-10"):
        await build_schedule(migrated_tick_db, session_migrated_db, ES, V0, START, END)


async def test_basis_units_change_after_a_supersession(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    day, answered = date(2024, 9, 10), date(2024, 9, 12)

    async def basis() -> tuple[int, ...]:
        schedule = await build_schedule(
            migrated_tick_db, session_migrated_db, ES, V0, answered, date(2024, 9, 13)
        )
        (answer,) = schedule.answers
        assert isinstance(answer, ActiveContract)
        assert answer.basis_session == day
        return answer.basis_unit_ids

    assert await basis() == (seeded.units[day],)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        tbbo = insert_unit(
            conn, request, unit_date=day, state=UnitState.INGESTED.value, **FILE_COLUMNS
        )
        conn.execute(
            "UPDATE tick_archive_unit SET superseded_by_unit_id = %s"
            " WHERE unit_id = %s",
            (tbbo, seeded.units[day]),
        )
        for instrument, volume in ((118, 600), (183748, 300), (5002, 5)):
            insert_ledger_row(
                conn, tbbo, instrument_id=instrument, session_date=day, volume=volume
            )
    assert await basis() == (tbbo,)


async def test_range_over_the_cap_is_refused_before_any_tick_read(
    session_migrated_db: str,
) -> None:
    """The tick URL is unreachable: reaching it would raise OperationalError."""
    with pytest.raises(RollRangeTooLong, match="TICK_ROLL_MAX_SESSIONS"):
        await build_schedule(
            UNREACHABLE, session_migrated_db, ES, V0, date(2020, 1, 2), date(2026, 1, 2)
        )


async def test_latest_active_answers_the_latest_complete_session(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    lines = await latest_active(
        migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES
    )
    assert [str(line.rule) for line in lines] == ["v.0", "c.0"]
    for line in lines:
        assert line.error is None
        assert line.session_date == date(2024, 9, 27)
        assert isinstance(line.answer, ActiveContract)
        assert line.answer.raw_symbol == "ESZ4"
    volume, calendar = lines
    assert volume.next_roll is None
    assert volume.known_through == date(2024, 10, 1)
    assert volume.roll_no_later_than == date(2024, 12, 23)
    assert calendar.next_roll is not None
    assert calendar.next_roll.session_date == date(2024, 12, 23)
    assert calendar.next_roll.contract.raw_symbol == "ESH5"


async def test_latest_active_with_nothing_complete_gives_a_reason(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    lines = await latest_active(
        migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES
    )
    assert all(line.reason and line.error is None for line in lines)


async def test_broken_invariant_sets_only_that_rules_error(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    """Session 09-27 ranks by 09-25; calendar reads no volume."""
    _delete_row(migrated_tick_db, date(2024, 9, 25), ESZ4.instrument_id)
    volume, calendar = await latest_active(
        migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES
    )
    assert volume.error is not None and "ESZ4" in volume.error
    assert volume.answer is None
    assert calendar.error is None
    assert isinstance(calendar.answer, ActiveContract)


async def test_unresolved_latest_session_keeps_its_reason(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    """With 09-25's units reopened, 09-27's lag session is incomplete."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = %s WHERE unit_date = %s",
            (UnitState.VERIFIED.value, date(2024, 9, 25)),
        )
    volume, _ = await latest_active(
        migrated_tick_db, session_migrated_db, ES, STATUS_ROLL_RULES
    )
    assert isinstance(volume.answer, Unresolved)
    assert "session 2024-09-25 is awaiting_ingest" in volume.answer.reason
