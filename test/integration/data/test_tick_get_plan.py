"""``contract_plan`` and ``product_plan`` on a scratch tick database (slice 230).

The September 2024 roll is seeded shaped like production (``roll_seed``):
``v.0`` serves ESU4 through session 09-17 and ESZ4 from 09-18. The calendar
is the scratch production database's CME_EQUITY.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from tick_support.roll_seed import ESU4, ESZ4, SeededRoll, seed_september_roll
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)
from tick_support.universe import UNTIERED

from manta_trading.data.tick import active_contract
from manta_trading.data.tick.active_contract import RollRangeTooLong
from manta_trading.data.tick.constants import OUTRIGHT_CLASS, TickSchema, UnitState
from manta_trading.data.tick.get_plan import (
    ContractAmbiguous,
    ContractNotFound,
    ContractTarget,
    GetPlan,
    Incomplete,
    contract_plan,
    product_plan,
)
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.tick_status import TickSessionStatus

ES = UNTIERED[0]
#: ESZ4's 2014 namesake: the same raw symbol, a decade earlier.
OLD_ESZ4 = 777


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_september_roll(migrated_tick_db)


async def _contract(
    tick_url: str, calendar_url: str, symbol: str, start: Any, end: Any
) -> GetPlan:
    async with await psycopg.AsyncConnection.connect(tick_url) as conn:
        return await contract_plan(conn, calendar_url, symbol, start, end)


async def _product(tick_url: str, calendar_url: str, start: Any, end: Any) -> GetPlan:
    async with await psycopg.AsyncConnection.connect(tick_url) as conn:
        return await product_plan(
            conn, (tick_url, calendar_url), ES, DEFAULT_ROLL_RULE, start, end
        )


def _sessions(plan: GetPlan) -> list[tuple[date, str]]:
    return [(w.session_date, w.raw_symbol) for w in plan.segments]


def _display(plan: GetPlan) -> list[tuple[date, date, str, int]]:
    return [
        (d.first_session, d.last_session, d.raw_symbol, d.instrument_id)
        for d in plan.display
    ]


def _await_ingest(url: str, day: date) -> None:
    """A higher-tier unit for ``day`` that is verified, not ingested: it is
    the day's current unit, so the sessions touching ``day`` await ingest."""
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn, schema=TickSchema.TBBO.value)
        insert_unit(
            conn, request, unit_date=day, state=UnitState.VERIFIED.value, **FILE_COLUMNS
        )


def _old_esz4(url: str, seeded: SeededRoll) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        insert_definition(
            conn,
            seeded.definition_unit,
            instrument_id=OLD_ESZ4,
            raw_symbol="ESZ4",
            activation_ns=utc_ns(datetime(2013, 6, 1, tzinfo=UTC)),
            expiration_ns=utc_ns(datetime(2014, 12, 19, 14, 30, tzinfo=UTC)),
            instrument_class=OUTRIGHT_CLASS,
        )


# -- contract ------------------------------------------------------------------


async def test_contract_with_a_range(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    plan = await _contract(
        migrated_tick_db,
        session_migrated_db,
        "ESZ4",
        date(2024, 9, 16),
        date(2024, 9, 18),
    )

    assert plan.target == ContractTarget("ESZ4", ESZ4.instrument_id)
    assert _sessions(plan) == [(date(2024, 9, 16), "ESZ4"), (date(2024, 9, 17), "ESZ4")]
    assert _display(plan) == [
        (date(2024, 9, 16), date(2024, 9, 17), "ESZ4", ESZ4.instrument_id)
    ]
    assert plan.unresolved == () and plan.incomplete == ()
    window = plan.segments[0]
    assert window.open_ns < window.close_ns  # bounds from the calendar


async def test_contract_without_a_range_covers_its_held_sessions(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    plan = await _contract(migrated_tick_db, session_migrated_db, "ESU4", None, None)

    assert (plan.start, plan.end) == (date(2024, 9, 3), date(2024, 9, 28))
    assert plan.segments[0].session_date == date(2024, 9, 3)
    # The synthetic ledger runs past ESU4's expiry; the plan stops at it.
    assert plan.segments[-1].session_date == date(2024, 9, 20)
    assert {w.instrument_id for w in plan.segments} == {ESU4.instrument_id}


async def test_unknown_contract_names_the_symbol(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    with pytest.raises(ContractNotFound, match="ESZ9"):
        await _contract(migrated_tick_db, session_migrated_db, "ESZ9", None, None)


async def test_ambiguous_contract_lists_every_candidate(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _old_esz4(migrated_tick_db, seeded)

    with pytest.raises(ContractAmbiguous) as raised:
        await _contract(migrated_tick_db, session_migrated_db, "ESZ4", None, None)

    message = str(raised.value)
    assert f"{OLD_ESZ4} (expires 2014-12-19T14:30:00.000000000Z)" in message
    assert f"{ESZ4.instrument_id} (expires 2024-12-20T13:30:00.000000000Z)" in message
    assert "--start/--end" in message


async def test_a_range_separates_the_namesakes(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _old_esz4(migrated_tick_db, seeded)

    plan = await _contract(
        migrated_tick_db,
        session_migrated_db,
        "ESZ4",
        date(2024, 9, 16),
        date(2024, 9, 17),
    )

    assert plan.target == ContractTarget("ESZ4", ESZ4.instrument_id)


async def test_contract_lists_an_incomplete_session_and_still_plans_it(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _await_ingest(migrated_tick_db, date(2024, 9, 19))

    plan = await _contract(
        migrated_tick_db,
        session_migrated_db,
        "ESZ4",
        date(2024, 9, 18),
        date(2024, 9, 20),
    )

    assert _sessions(plan) == [(date(2024, 9, 18), "ESZ4"), (date(2024, 9, 19), "ESZ4")]
    assert plan.incomplete == (
        Incomplete(date(2024, 9, 19), TickSessionStatus.AWAITING_INGEST),
    )


# -- product -------------------------------------------------------------------


async def test_product_across_the_roll_matches_active(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    plan = await _product(
        migrated_tick_db, session_migrated_db, date(2024, 9, 16), date(2024, 9, 20)
    )

    assert _display(plan) == [
        (date(2024, 9, 16), date(2024, 9, 17), "ESU4", ESU4.instrument_id),
        (date(2024, 9, 18), date(2024, 9, 19), "ESZ4", ESZ4.instrument_id),
    ]
    assert _sessions(plan) == [
        (date(2024, 9, 16), "ESU4"),
        (date(2024, 9, 17), "ESU4"),
        (date(2024, 9, 18), "ESZ4"),
        (date(2024, 9, 19), "ESZ4"),
    ]
    assert plan.definitions_as_of == date(2024, 8, 30)
    assert plan.unresolved == () and plan.incomplete == ()


async def test_first_held_sessions_are_unresolved_never_filled(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    plan = await _product(
        migrated_tick_db, session_migrated_db, date(2024, 9, 3), date(2024, 9, 9)
    )

    assert [u.session_date for u in plan.unresolved] == [
        date(2024, 9, 3),
        date(2024, 9, 4),
    ]
    assert all(u.reason for u in plan.unresolved)
    assert [d for d, _ in _sessions(plan)] == [
        date(2024, 9, 5),
        date(2024, 9, 6),
    ]


async def test_product_lists_an_incomplete_session_and_still_plans_it(
    seeded: SeededRoll, migrated_tick_db: str, session_migrated_db: str
) -> None:
    _await_ingest(migrated_tick_db, date(2024, 9, 19))

    plan = await _product(
        migrated_tick_db, session_migrated_db, date(2024, 9, 16), date(2024, 9, 20)
    )

    assert date(2024, 9, 19) in [d for d, _ in _sessions(plan)]
    assert plan.incomplete == (
        Incomplete(date(2024, 9, 19), TickSessionStatus.AWAITING_INGEST),
    )


@pytest.mark.parametrize(
    ("start", "end", "first", "last"),
    [
        (None, None, date(2024, 9, 3), date(2024, 9, 28)),
        (date(2024, 9, 20), None, date(2024, 9, 20), date(2024, 9, 28)),
        (None, date(2024, 9, 6), date(2024, 9, 3), date(2024, 9, 6)),
    ],
)
async def test_missing_ends_default_to_the_held_sessions(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    start: date | None,
    end: date | None,
    first: date,
    last: date,
) -> None:
    plan = await _product(migrated_tick_db, session_migrated_db, start, end)

    assert (plan.start, plan.end) == (first, last)


async def test_a_range_over_the_cap_refuses(
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(active_contract, "TICK_ROLL_MAX_SESSIONS", 3)

    with pytest.raises(RollRangeTooLong):
        await _product(
            migrated_tick_db, session_migrated_db, date(2024, 9, 16), date(2024, 9, 23)
        )
    with pytest.raises(RollRangeTooLong):  # the held span, by default
        await _product(migrated_tick_db, session_migrated_db, None, None)


async def test_nothing_held_and_no_range_is_an_empty_plan(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    plan = await _product(migrated_tick_db, session_migrated_db, None, None)

    assert plan.segments == () and plan.start is None
