"""``Ohlcv1mProjector`` on a migrated tick database (slice 302, D3).

A unit is seeded from the real 2024-12-03 slices (its definitions land in
``tick_definition``); the bars come from a hand-built batch in the reader's
own record layout.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import pytest
from tick_support.ingest import plan_unit
from tick_support.rows import insert_bar_ledger_row, insert_request, insert_unit
from tick_support.runs import connect
from tick_support.tier_units import READER, TBBO_DAY, real_file, seed_tier_unit

from manta_trading.data.tick import ingest_plan, schema_families
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE, BAR_LEDGER_TABLE
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.ingest_plan import Calendars, superseded_sql
from manta_trading.data.tick.manifest_reads import units_by_id
from manta_trading.data.tick.ohlcv_1m_projector import (
    COPY_TYPES,
    OHLCV_1M_PROJECTOR,
)
from manta_trading.data.tick.provider import RecordBatch
from manta_trading.data.tick.schema_families import SchemaFamily

NS_PER_MINUTE = 60 * 1_000_000_000
SAMPLE = real_file(TBBO_DAY, TickSchema.TRADES)


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


BAR_SAMPLE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "databento"
    / "test_data.ohlcv-1m.v3.dbn.zst"
)


def _bar_dtype() -> np.dtype[Any]:
    """The reader's own ``ohlcv-1m`` record layout, read from the DBN sample."""
    return next(READER.open_file(BAR_SAMPLE).iter_batches()).records.dtype


class _OneBatch:
    """An ``ITickFileReader`` whose file yields one hand-built bar batch."""

    def __init__(self, records: np.ndarray[Any, Any]) -> None:
        self._records = records

    def open_file(self, _path: Path) -> Any:
        batch = RecordBatch(TickSchema.OHLCV_1M, self._records, len(self._records))
        return type("File", (), {"iter_batches": lambda _s: iter([batch])})()


def _bars(instruments_and_minutes: list[tuple[int, int]]) -> np.ndarray[Any, Any]:
    records = np.zeros(len(instruments_and_minutes), dtype=_bar_dtype())
    for i, (instrument, ts) in enumerate(instruments_and_minutes):
        records[i] = (14, 33, 1, instrument, ts, 100, 105, 99, 102, 10 + i)
    return records


def _count(cur: psycopg.Cursor[Any], table: str) -> int:
    cur.execute(f"SELECT count(*) FROM {table}")  # type: ignore[call-overload]
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


def _first_trade() -> tuple[int, int]:
    batch = next(READER.open_file(SAMPLE).iter_batches())
    return int(batch.records["instrument_id"][0]), int(batch.records["ts_event"][0])


async def test_load_stored_count_finish_and_delete(
    migrated_tick_db: str, tmp_path: Path, calendars: Calendars
) -> None:
    url = migrated_tick_db
    seeded = await seed_tier_unit(
        url, tmp_path, TickSchema.TRADES, TBBO_DAY, job_id="BARS"
    )
    plan = await plan_unit(url, calendars, seeded.unit_id)
    instrument, ts = _first_trade()
    minute = ts - ts % NS_PER_MINUTE
    reader = _OneBatch(
        _bars([(instrument, minute), (instrument, minute + NS_PER_MINUTE)])
    )
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        loaded = OHLCV_1M_PROJECTOR.load(cur, plan, tmp_path, reader, None)
        assert loaded.decoded == 2
        assert (loaded.first_ns, loaded.last_ns) == (minute, minute + NS_PER_MINUTE)
        stored = OHLCV_1M_PROJECTOR.stored_count(
            cur, seeded.unit_id, loaded.first_ns, loaded.last_ns
        )
        assert stored == 2
        OHLCV_1M_PROJECTOR.finish(cur, seeded.unit_id, loaded)
        cur.execute(
            f"SELECT instrument_id, record_count, volume FROM {BAR_LEDGER_TABLE}"
            " WHERE record_count > 0 AND unit_id = %s",
            (seeded.unit_id,),
        )
        assert cur.fetchall() == [(instrument, 2, 21)]
        assert _count(cur, BAR_LEDGER_TABLE) > 1  # a row per definition per session
        cur.execute("SELECT count(*) FROM tick_ingest_ledger")
        assert cur.fetchone() == (0,)  # bars never touch the trade ledger
        OHLCV_1M_PROJECTOR.delete_unit(
            cur, seeded.unit_id, loaded.first_ns, loaded.last_ns
        )
        cur.execute(f"SELECT count(*) FROM {BAR_1M_TABLE}")
        assert cur.fetchone() == (0,)
        assert _count(cur, BAR_LEDGER_TABLE) > 1  # ledger rows stay on delete


def test_the_copy_types_follow_the_table_ddl(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        rows = conn.execute(
            "SELECT data_type FROM information_schema.columns"
            f" WHERE table_name = '{BAR_1M_TABLE}' ORDER BY ordinal_position"
        ).fetchall()
    names = {"bigint": "int8", "integer": "int4"}
    assert tuple(names[r[0]] for r in rows) == COPY_TYPES


def test_with_no_times_stored_count_and_delete_touch_nothing(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db) as conn, conn.cursor() as cur:
        assert OHLCV_1M_PROJECTOR.stored_count(cur, 1, None, None) == 0
        OHLCV_1M_PROJECTOR.delete_unit(cur, 1, None, None)


async def test_a_superseded_units_times_come_from_its_familys_ledger(
    migrated_tick_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two schemas of one family: the older's times are in the family's ledger.

    ``ohlcv-1m`` is alone in its family, so a test family is built by moving
    ``tbbo`` into ``BAR_1M`` above it; the read must then use the bar ledger.
    """
    url = migrated_tick_db
    family_schemas = {
        **schema_families.FAMILY_SCHEMAS,
        SchemaFamily.TICK_TIER: (TickSchema.TRADES, TickSchema.MBP_1),
        SchemaFamily.BAR_1M: (TickSchema.OHLCV_1M, TickSchema.TBBO),
    }
    monkeypatch.setattr(schema_families, "FAMILY_SCHEMAS", family_schemas)
    monkeypatch.setattr(
        schema_families,
        "SCHEMA_FAMILY",
        schema_families.schema_family_map(family_schemas),
    )
    monkeypatch.setattr(ingest_plan, "SCHEMA_FAMILY", schema_families.SCHEMA_FAMILY)
    with psycopg.connect(url, autocommit=True) as conn:
        old = insert_unit(
            conn,
            insert_request(conn, schema=TickSchema.OHLCV_1M.value),
            state=UnitState.INGESTED.value,
            file_path="J/f", file_size_bytes=1, file_sha256="0" * 64,
        )  # fmt: skip
        insert_bar_ledger_row(conn, old, first_event_ns=111, last_event_ns=222)
        new = insert_unit(conn, insert_request(conn, schema=TickSchema.TBBO.value))
    assert BAR_LEDGER_TABLE in superseded_sql(TickSchema.TBBO)
    async with connect(url) as aconn:
        (unit,) = await units_by_id(aconn, [new])
        found = await ingest_plan._superseded(aconn, unit)
    assert [(s.unit_id, s.first_event_ns, s.last_event_ns) for s in found] == [
        (old, 111, 222)
    ]


def test_a_tick_tier_units_superseded_read_uses_the_trade_ledger() -> None:
    assert "tick_ingest_ledger" in superseded_sql(TickSchema.TBBO)
    assert BAR_LEDGER_TABLE not in superseded_sql(TickSchema.TBBO)
