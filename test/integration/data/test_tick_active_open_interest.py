"""``build_schedule`` under ``n.0`` on a scratch tick database (slice 303, D9).

The September 2024 roll is seeded as 229 does; statistics units are ingested for
every calendar day (a session's statistics window includes the weekend after a
Friday) and each session carries a resolved open-interest record per outright.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from tick_support.roll_seed import (
    ESH5,
    ESU4,
    ESZ4,
    SEPTEMBER,
    SEPTEMBER_CROSS,
    SeededRoll,
    seed_september_roll,
    seed_tier_days,
)
from tick_support.rows import insert_statistics
from tick_support.universe import UNTIERED

from manta_trading.data.tick import active_contract
from manta_trading.data.tick.active_contract import build_schedule
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.roll_types import ActiveContract, Unresolved
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType

ES = UNTIERED[0]
N0, N1, V0, C0 = (RollRule.parse(t) for t in ("n.0", "n.1", "v.0", "c.0"))
START, END = date(2024, 9, 5), date(2024, 9, 28)
ANSWERED, ONE_DAY = date(2024, 9, 12), timedelta(days=1)  # ranks on 09-10
OPEN_INTEREST = int(StatType.OPEN_INTEREST)
#: Every calendar day the window of any seeded session touches.
CALENDAR_DAYS = [date(2024, 9, 1) + timedelta(days=n) for n in range(32)]


def _interest(day: date, contract: Any) -> int:
    crossed = day >= SEPTEMBER_CROSS
    if contract is ESU4:
        return 4_000 if crossed else 6_000
    if contract is ESZ4:
        return 7_000 if crossed else 3_000
    return 50


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_september_roll(migrated_tick_db)


@pytest.fixture
def statistics(migrated_tick_db: str, seeded: SeededRoll) -> dict[date, int]:
    units = seed_tier_days(migrated_tick_db, CALENDAR_DAYS, TickSchema.STATISTICS)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        for sequence, day in enumerate(SEPTEMBER):
            for contract in (ESU4, ESZ4, ESH5):
                insert_statistics(
                    conn,
                    units[day],
                    instrument_id=contract.instrument_id,
                    stat_type=OPEN_INTEREST,
                    quantity=_interest(day, contract),
                    price=None,
                    session_date=day,
                    sequence=sequence,
                )
    return units


def _bases(schedule: Any) -> set[SchemaFamily | None]:
    return {a.basis_family for a in schedule.answers if isinstance(a, ActiveContract)}


def _segments(schedule: Any) -> list[tuple[date, date, str]]:
    return [
        (s.first_session, s.last_session, s.contract.raw_symbol)
        for s in schedule.segments()
    ]


async def test_n0_rolls_like_the_cross_in_open_interest(
    statistics: dict[date, int], migrated_tick_db: str, session_migrated_db: str
) -> None:
    schedule = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, N0, START, END
    )
    assert schedule.unresolved == ()
    assert _segments(schedule) == [
        (START, date(2024, 9, 17), "ESU4"),
        (date(2024, 9, 18), date(2024, 9, 27), "ESZ4"),
    ]
    for answer in schedule.answers:
        assert isinstance(answer, ActiveContract)
        assert answer.basis_family is SchemaFamily.STATISTICS
    first = schedule.answers[0]
    assert isinstance(first, ActiveContract)
    assert first.basis_unit_ids == (statistics[first.basis_session],)


async def test_n1_is_the_second_ranked(
    statistics: dict[date, int], migrated_tick_db: str, session_migrated_db: str
) -> None:
    schedule = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, N1, START, END
    )
    assert _segments(schedule)[0][2] == "ESZ4"  # ESU4 leads, so ESZ4 is second


async def test_an_incomplete_statistics_session_is_unresolved_naming_it(
    statistics: dict[date, int], migrated_tick_db: str, session_migrated_db: str
) -> None:
    lag = date(2024, 9, 10)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE tick_archive_unit SET state = 'verified' WHERE unit_id = %s",
            (statistics[lag],),
        )
    schedule = await build_schedule(
        migrated_tick_db,
        session_migrated_db,
        ES,
        N0,
        date(2024, 9, 12),
        date(2024, 9, 13),
    )
    (answer,) = schedule.answers
    assert isinstance(answer, Unresolved)
    assert answer.reason == f"open interest inputs: session {lag} is awaiting_ingest"


async def test_a_contract_with_no_open_interest_is_unresolved(
    statistics: dict[date, int], migrated_tick_db: str, session_migrated_db: str
) -> None:
    lag = date(2024, 9, 10)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM tick_statistics"
            " WHERE instrument_id = %s AND session_date = %s",
            (ESZ4.instrument_id, lag),
        )
    schedule = await build_schedule(
        migrated_tick_db,
        session_migrated_db,
        ES,
        N0,
        date(2024, 9, 12),
        date(2024, 9, 13),
    )
    (answer,) = schedule.answers
    assert isinstance(answer, Unresolved)
    assert f"ESZ4 ({ESZ4.instrument_id}) has no open interest in session {lag}" in (
        answer.reason
    )


async def test_each_rule_reads_exactly_the_inputs_it_needs(
    statistics: dict[date, int],
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    volumes, interest = (
        active_contract.session_volumes,
        active_contract.session_open_interest,
    )

    async def counted_volumes(*args: Any, **kwargs: Any) -> Any:
        calls.append("volumes")
        return await volumes(*args, **kwargs)

    async def counted_interest(*args: Any, **kwargs: Any) -> Any:
        calls.append("open_interest")
        return await interest(*args, **kwargs)

    monkeypatch.setattr(active_contract, "session_volumes", counted_volumes)
    monkeypatch.setattr(active_contract, "session_open_interest", counted_interest)

    async def reads(rule: RollRule) -> list[str]:
        calls.clear()
        await build_schedule(
            migrated_tick_db, session_migrated_db, ES, rule, START, END
        )
        return list(calls)

    assert await reads(C0) == []
    assert await reads(V0) == ["volumes"]
    assert await reads(N0) == ["open_interest"]


async def test_c_and_v_run_without_any_statistics_rows(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    calendar = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, C0, START, END
    )
    volume = await build_schedule(
        migrated_tick_db, session_migrated_db, ES, V0, START, END
    )
    assert {
        a.basis_family for a in calendar.answers if isinstance(a, ActiveContract)
    } == {None}
    assert {
        a.basis_family for a in volume.answers if isinstance(a, ActiveContract)
    } == {SchemaFamily.TICK_TIER}
