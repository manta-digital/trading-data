"""Slice 309: the source of each session's time bars, on the real parity day.

``seed_parity`` holds 2024-12-03 complete in ticks and in provider one-minute
bars, with empty ingested units on 12-02 and 12-04 in both families. The plan
runs to 12-06, so 12-05 is planned with neither family held.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest
from tick_support.parity_seed import SESSION, ParityDb, seed_parity

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.time_bar_size import parse_bar_size
from manta_trading.data.tick.time_bar_source import choose_sources, served_plan

END = date(2024, 12, 6)
NEITHER = date(2024, 12, 5)
BARS = TickSchema.OHLCV_1M.value


@pytest.fixture
async def db(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)


async def _choose(db: ParityDb, token: str) -> tuple[GetPlan, dict[date, str | None]]:
    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        plan = await contract_plan(
            conn, db.calendar, db.symbol, SESSION, END, SchemaFamily.TICK_TIER
        )
        return plan, await choose_sources(
            conn, db.calendar, plan, parse_bar_size(token)
        )


def _set_unit_state(db: ParityDb, schema: TickSchema, state: UnitState) -> None:
    """Move the 12-03 unit of ``schema`` back a step (throwaway database only)."""
    with psycopg.connect(db.url, autocommit=True) as conn:
        updated = conn.execute(
            "UPDATE tick_archive_unit u SET state = %s FROM tick_request r"
            " WHERE u.request_id = r.request_id AND r.schema = %s"
            " AND u.unit_date = %s",
            (state.value, schema.value, SESSION),
        ).rowcount
    assert updated == 1


def _status(plan: GetPlan, day: date) -> TickSessionStatus | None:
    return {i.session_date: i.status for i in plan.incomplete}.get(day)


async def test_a_size_provider_bars_make_reads_them(db: ParityDb) -> None:
    _, sources = await _choose(db, "5m")
    assert sources[SESSION] == BARS


async def test_a_size_in_seconds_reads_ticks(db: ParityDb) -> None:
    _, sources = await _choose(db, "15s")
    assert sources[SESSION] == TICKS_SOURCE


async def test_a_partial_bar_day_falls_through_to_ticks(db: ParityDb) -> None:
    _set_unit_state(db, TickSchema.OHLCV_1M, UnitState.VERIFIED)
    _, sources = await _choose(db, "5m")
    assert sources[SESSION] == TICKS_SOURCE


async def test_a_session_in_neither_family_is_not_read(db: ParityDb) -> None:
    plan, sources = await _choose(db, "5m")
    assert sources[NEITHER] is None
    # 12-05 lies past the fixture's dataset edge, so its tick verdict is
    # ``edge_unknown``, not ``missing``; it has no ledger row, so it is not read.
    served = served_plan(plan, sources)
    assert _status(served, NEITHER) is _status(plan, NEITHER) is not None


async def test_partly_loaded_ticks_with_no_bars_read_ticks(db: ParityDb) -> None:
    _set_unit_state(db, TickSchema.OHLCV_1M, UnitState.VERIFIED)
    _set_unit_state(db, TickSchema.TBBO, UnitState.VERIFIED)
    plan, sources = await _choose(db, "5m")
    tick_status = _status(plan, SESSION)
    assert tick_status not in (None, TickSessionStatus.MISSING)
    assert sources[SESSION] == TICKS_SOURCE
    assert _status(served_plan(plan, sources), SESSION) is tick_status
