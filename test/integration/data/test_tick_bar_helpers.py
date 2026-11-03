"""The bar-ingest test helpers record units the way ingest selection needs (302)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
from tick_support.ingest import scalar, unit_state
from tick_support.tick_bar_helpers import seed_bar_units, write_bar_file

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader


async def test_the_helper_records_a_definition_and_a_bar_unit(
    migrated_tick_db: str, tmp_path: Path
) -> None:
    seeded = await seed_bar_units(migrated_tick_db, tmp_path, job_id="BARS")
    bar = unit_state(migrated_tick_db, seeded.unit_id)
    definition = unit_state(migrated_tick_db, seeded.definition_unit_id)
    assert bar[0] == UnitState.VERIFIED.value
    assert definition[0] == UnitState.INGESTED.value
    assert scalar(migrated_tick_db, "SELECT count(*) FROM tick_definition") > 0
    schema = scalar(
        migrated_tick_db,
        "SELECT r.schema FROM tick_archive_unit u JOIN tick_request r"
        " USING (request_id) WHERE u.unit_id = %s",
        seeded.unit_id,
    )
    assert schema == TickSchema.OHLCV_1M.value
    assert seeded.record_count == 2281


def test_the_primary_database_reaches_back_to_2010(session_migrated_db: str) -> None:
    with psycopg.connect(session_migrated_db) as conn:
        first = conn.execute(
            "SELECT min(session_date) FROM trading_sessions"
            " WHERE calendar_id = 'CME_EQUITY'"
        ).fetchone()
    assert first == (date(2010, 6, 7),)


def test_hand_built_bars_read_back_through_the_reader(tmp_path: Path) -> None:
    written = write_bar_file(
        tmp_path / "bars.dbn.zst",
        date(2012, 3, 6),
        [
            {"instrument_id": 7, "ts_event": 1_000_000_000, "volume": 3},
            {"instrument_id": 8, "ts_event": 2_000_000_000, "volume": 4},
        ],
    )
    tick_file = DbnFileReader().open_file(written)
    assert tick_file.schema is TickSchema.OHLCV_1M
    (batch,) = list(tick_file.iter_batches())
    assert batch.count == 2
    assert batch.records["instrument_id"].tolist() == [7, 8]
    assert batch.records["volume"].tolist() == [3, 4]
