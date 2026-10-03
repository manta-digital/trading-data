"""225's write paths on compressed chunks (slice 226, TD5).

The test cluster has no TimescaleDB scheduler, so every compression here is
an explicit ``compress_chunk``. The 2024-12-03 ``trades`` slice is derived
from the ``tbbo`` slice's trade fields, so the two tiers hold the same events
(see ``SOURCES.md``) and land in the same chunk.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.database import ProvisionedTickDb
from tick_support.ingest import (
    plan_unit,
    run_worker,
    trade_count,
    unit_state,
)
from tick_support.rows import FILE_COLUMNS, insert_request, insert_trade, insert_unit
from tick_support.runs import connect
from tick_support.tier_units import TBBO_DAY, SeededUnit, seed_tier_unit

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.ingest_checks import IngestCheck
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_worker import UnitResult
from manta_trading.data.tick.tick_coverage import build_coverage
from manta_trading.data.tick.universe import TICK_UNIVERSE

ES = TICK_UNIVERSE[0]
_STATUS = (
    "SELECT compression_status FROM chunk_columnstore_stats('tick_trade')"
    " ORDER BY chunk_name"
)


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


def _compress_all(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "SELECT compress_chunk(c, if_not_compressed => true)"
            " FROM show_chunks('tick_trade') AS c"
        )


def _statuses(url: str) -> list[str]:
    with psycopg.connect(url) as conn:
        return [str(r[0]) for r in conn.execute(_STATUS).fetchall()]


def _partial(url: str) -> list[bool]:
    """TimescaleDB's chunk status bit for "partially compressed" (8)."""
    with psycopg.connect(url) as conn:
        rows = conn.execute(
            "SELECT (status & 8) <> 0 FROM _timescaledb_catalog.chunk"
            " WHERE hypertable_id = (SELECT id FROM _timescaledb_catalog.hypertable"
            " WHERE table_name = 'tick_trade') ORDER BY id"
        ).fetchall()
    return [bool(r[0]) for r in rows]


async def _ingested(
    url: str, archive: Path, calendars: Calendars, schema: TickSchema, job_id: str
) -> SeededUnit:
    seeded = await seed_tier_unit(url, archive, schema, TBBO_DAY, job_id=job_id)
    plan = await plan_unit(url, calendars, seeded.unit_id)
    assert run_worker(plan, archive, url).result is UnitResult.INGESTED
    return seeded


async def _coverage_ok(url: str, calendar_url: str) -> None:
    async with connect(url) as conn:
        coverage = await build_coverage(
            conn, calendar_url, ES, TBBO_DAY, TBBO_DAY + timedelta(days=1)
        )
    assert [s.check for s in coverage.sessions] == ["ok"]
    assert coverage.sessions[0].ledger == coverage.sessions[0].raw > 0


async def test_tbbo_supersedes_trades_held_in_a_compressed_chunk(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    calendars: Calendars,
) -> None:
    """Supersession (TD5 row 1) and coverage on a partial chunk (row 4)."""
    url = migrated_tick_db
    trades = await _ingested(url, tmp_path, calendars, TickSchema.TRADES, "TRADES")
    _compress_all(url)
    assert set(_statuses(url)) == {"Compressed"}

    tbbo = await _ingested(url, tmp_path, calendars, TickSchema.TBBO, "TBBO")
    assert unit_state(url, trades.unit_id)[3] == tbbo.unit_id
    assert trade_count(url, trades.unit_id) == 0
    assert trade_count(url, tbbo.unit_id) == tbbo.record_count
    assert _partial(url) == [True], "tbbo rows sit uncompressed in the chunk"
    await _coverage_ok(url, session_migrated_db)  # rows not yet recompressed

    _compress_all(url)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(
            "SELECT compress_chunk(c, recompress => true)"
            " FROM show_chunks('tick_trade') AS c"
        )
    assert set(_statuses(url)) == {"Compressed"}
    assert _partial(url) == [False]
    await _coverage_ok(url, session_migrated_db)


async def test_a_duplicate_unit_into_a_compressed_chunk_fails_as_overlap(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    """Overlap (TD5 row 2): the unique key holds on compressed chunks."""
    url = migrated_tick_db
    first = await _ingested(url, tmp_path, calendars, TickSchema.TRADES, "FIRST")
    _compress_all(url)
    second = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TBBO_DAY, job_id="AGAIN"
    )
    plan = await plan_unit(url, calendars, second.unit_id)
    outcome = run_worker(plan, tmp_path, url)
    assert (outcome.result, outcome.check) == (UnitResult.FAILED, IngestCheck.OVERLAP)
    # TimescaleDB raises the violation from a compressed chunk without the
    # "Key (...)=" detail; 225's overlap_reason falls back to naming the table.
    assert (outcome.reason or "").startswith(
        "overlap: a tick_trade key already exists"
    ), outcome.reason
    assert trade_count(url, second.unit_id) == 0
    assert trade_count(url, first.unit_id) == first.record_count


def test_the_application_role_writes_into_a_compressed_chunk(
    provisioned_tick_db: ProvisionedTickDb,
) -> None:
    """Ingest as tick_app (TD5 row 3): the statements ingest runs against a
    compressed chunk (the supersession DELETE, then the COPY), as the
    application role. One transaction, rolled back: the provisioned
    database is shared by the privilege suite."""
    with psycopg.connect(provisioned_tick_db.url) as conn:
        request = insert_request(conn, range_start=date(2024, 9, 3))
        unit = insert_unit(
            conn,
            request,
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
        insert_trade(conn, unit)
        conn.execute("SELECT compress_chunk(c) FROM show_chunks('tick_trade') AS c")
        row = conn.execute("SELECT * FROM tick_trade").fetchone()
        assert row is not None
        conn.execute(f'SET ROLE "{provisioned_tick_db.app_role}"')
        deleted = conn.execute("DELETE FROM tick_trade WHERE unit_id = %s", (unit,))
        assert deleted.rowcount == 1
        _copy_back(conn, row)
        assert conn.execute("SELECT count(*) FROM tick_trade").fetchone() == (1,)
        conn.rollback()


def _copy_back(conn: psycopg.Connection[Any], row: tuple[Any, ...]) -> None:
    """COPY one row in, as ingest does (text COPY of the same columns)."""
    with conn.cursor().copy("COPY tick_trade FROM STDIN") as copy:
        copy.write_row(row)
