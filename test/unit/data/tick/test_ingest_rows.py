"""COPY row building on the real slices (slice 225, TD2)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest
from tick_support.tier_units import TBBO_DAY, TRADES_DAY, real_file

from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.ingest_rows import COPY_COLUMNS, COPY_TYPES, copy_rows
from manta_trading.data.tick.storage_columns import TICK_TRADE_BBO_COLUMNS

READER = DbnFileReader()


def _batch(schema: TickSchema, day: date) -> np.ndarray:
    return next(READER.open_file(real_file(day, schema)).iter_batches()).records


@pytest.mark.parametrize(
    ("schema", "day"), [(TickSchema.TRADES, TRADES_DAY), (TickSchema.TBBO, TBBO_DAY)]
)
def test_rows_match_the_column_list(schema: TickSchema, day: date) -> None:
    records = _batch(schema, day)
    ordinals = np.arange(len(records), dtype=np.int64)
    rows = list(copy_rows(records, ordinals, 7))
    assert len(rows) == len(records)
    assert {len(row) for row in rows} == {len(COPY_COLUMNS)} == {len(COPY_TYPES)}
    first = dict(zip(COPY_COLUMNS, rows[0], strict=True))
    assert first["ts_event"] == int(records["ts_event"][0])
    assert first["action"] == records["action"][0].decode()
    assert (first["sequence_ordinal"], first["unit_id"]) == (0, 7)
    bbo = [first[column] for column in TICK_TRADE_BBO_COLUMNS]
    if schema is TickSchema.TRADES:
        assert bbo == [None] * 6
    else:
        assert None not in bbo
