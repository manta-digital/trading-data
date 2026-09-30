"""The real-slice seeding helper builds a selectable unit (slice 225, task 0.2).

Selection itself is 225's (``ingest_select``); this checks the rows it will
read: a *verified* tier unit with its true record count, and an *ingested*
companion definition unit whose real definitions are projected.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import psycopg
import pytest
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, seed_tier_unit

from manta_trading.data.tick.constants import TickSchema, UnitState


@pytest.mark.parametrize(
    ("schema", "day"),
    [(TickSchema.TRADES, TRADES_DAY), (TickSchema.TBBO, TBBO_DAY)],
)
async def test_seeded_unit_is_selectable(
    migrated_tick_db: str, tmp_path: Path, schema: TickSchema, day: date
) -> None:
    seeded = await seed_tier_unit(
        migrated_tick_db, tmp_path, schema, day, job_id="GLBX-REAL-SLICE"
    )
    assert seeded.file.path.is_file()
    assert seeded.record_count > 3000
    with psycopg.connect(migrated_tick_db) as conn:
        units = {
            row[0]: row[1:]
            for row in conn.execute(
                "SELECT unit_id, state, provider_record_count, unit_date"
                " FROM tick_archive_unit"
            ).fetchall()
        }
        assets = conn.execute(
            "SELECT DISTINCT asset FROM tick_definition WHERE unit_id = %s",
            (seeded.definition_unit_id,),
        ).fetchall()
    assert units[seeded.unit_id] == (
        UnitState.VERIFIED.value,
        seeded.record_count,
        day,
    )
    assert units[seeded.definition_unit_id] == (UnitState.INGESTED.value, None, day)
    assert assets == [("ES",)]
