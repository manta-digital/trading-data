"""Slice 309: the time-bar reader and its admission, on the real parity day.

``seed_parity`` holds 2024-12-03 complete in ticks and in provider one-minute
bars; 12-02 and 12-04 have empty ingested units; 12-05 holds nothing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest
from tick_support.parity_seed import SESSION, ParityDb, seed_parity

from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.time_bar_reads import time_bar_trades
from manta_trading.data.tick.time_bar_size import parse_bar_size
from manta_trading.data.tick.time_bar_source import choose_sources

END = date(2024, 12, 6)
NEITHER = date(2024, 12, 5)
OPEN = (None, None)


@pytest.fixture
async def db(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)


async def _plan(conn: psycopg.AsyncConnection, db: ParityDb) -> GetPlan:  # type: ignore[type-arg]
    return await contract_plan(
        conn, db.calendar, db.symbol, SESSION, END, SchemaFamily.TICK_TIER
    )


def ledger_count(db: ParityDb, first: date = SESSION, last: date = END) -> int:
    """The tick ledger's records for the contract over sessions ``[first, last)``.
    The 12-03 UTC tick day also opens the 12-04 session, so both hold trades."""
    with psycopg.connect(db.url) as conn:
        row = conn.execute(
            f"SELECT sum(record_count) FROM {FAMILY_LEDGER[SchemaFamily.TICK_TIER]}"
            " WHERE instrument_id = %s AND session_date >= %s AND session_date < %s",
            (db.window.instrument_id, first, last),
        ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0])


async def test_admission_counts_only_tick_cut_sessions(db: ParityDb) -> None:
    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        plan = await _plan(conn, db)
        # 5m: every planned session with trades is held complete in provider
        # bars, so nothing is cut from ticks.
        for token, expected in (("5m", 0), ("15s", ledger_count(db))):
            sources = await choose_sources(
                conn, db.calendar, plan, parse_bar_size(token)
            )
            assert await time_bar_trades(conn, plan, sources, OPEN) == expected


async def test_a_session_with_no_ledger_row_adds_nothing(db: ParityDb) -> None:
    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        plan = await _plan(conn, db)
        sources = await choose_sources(conn, db.calendar, plan, parse_bar_size("15s"))
        assert sources[NEITHER] is None
        forced = dict(sources) | {NEITHER: TICKS_SOURCE}
        assert await time_bar_trades(conn, plan, forced, OPEN) == ledger_count(db)
