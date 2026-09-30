"""COPY rows against the real ``tick_trade`` table (slice 225, TD2).

The declared binary types match the migration's DDL, and one real record of
each tier survives ``COPY`` → ``SELECT`` unchanged.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import psycopg
import pytest
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, real_file

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.ingest_rows import (
    COPY_COLUMNS,
    COPY_SQL,
    COPY_TYPES,
    copy_rows,
)

#: information_schema ``data_type`` → the binary type name COPY declares.
_PG_NAMES = {"bigint": "int8", "integer": "int4", "smallint": "int2", "text": "text"}


def test_copy_types_match_the_table(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        types: dict[str, str] = dict(
            conn.execute(
                "SELECT column_name, data_type FROM information_schema.columns"
                " WHERE table_name = 'tick_trade'"
            ).fetchall()
        )
    assert set(types) == set(COPY_COLUMNS)
    assert [_PG_NAMES[types[c]] for c in COPY_COLUMNS] == list(COPY_TYPES)


@pytest.mark.parametrize(
    ("schema", "day"), [(TickSchema.TRADES, TRADES_DAY), (TickSchema.TBBO, TBBO_DAY)]
)
def test_one_real_record_round_trips(
    migrated_tick_db: str, schema: TickSchema, day: date
) -> None:
    batch = next(DbnFileReader().open_file(real_file(day, schema)).iter_batches())
    row = next(copy_rows(batch.records[:1], np.zeros(1, dtype=np.int64), 3))
    with psycopg.connect(migrated_tick_db) as conn:
        with conn.cursor() as cur, cur.copy(COPY_SQL) as copy:
            copy.set_types(list(COPY_TYPES))
            copy.write_row(row)
        stored: Any = conn.execute(
            f"SELECT {', '.join(COPY_COLUMNS)} FROM tick_trade"
        ).fetchall()
    assert stored == [row]
