"""``tick_trade`` COPY rows from one decoded batch (slice 225, TD2).

The seam slice 226 may replace with a NumPy-built binary ``COPY`` buffer: the
worker only needs :data:`COPY_SQL`, :data:`COPY_TYPES` (for ``set_types``)
and :func:`copy_rows`. Rows follow ``TICK_TRADE_COLUMNS`` order, then the
derived ``sequence_ordinal`` and ``unit_id``. A ``trades``-tier batch has no
BBO fields, so those six columns are ``NULL`` (the table's BBO check requires
all six set or all six ``NULL``). Values are stored as delivered (222 TD3).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import numpy as np
import numpy.typing as npt

from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_BBO_COLUMNS,
    TICK_TRADE_COLUMNS,
    TICK_TRADE_DERIVED_COLUMNS,
)

#: ``tick_trade`` columns the COPY writes, in order.
COPY_COLUMNS: tuple[str, ...] = (
    *TICK_TRADE_COLUMNS.values(),
    *TICK_TRADE_DERIVED_COLUMNS,
)

#: PostgreSQL type of each ``COPY_COLUMNS`` entry, for binary ``set_types``;
#: the tick migration's ``tick_trade`` DDL is the source (parity-tested).
_COLUMN_TYPES: dict[str, str] = {
    "ts_event": "int8",
    "ts_recv": "int8",
    "instrument_id": "int8",
    "publisher_id": "int4",
    "sequence": "int8",
    "price": "int8",
    "size": "int8",
    "action": "text",
    "side": "text",
    "flags": "int2",
    "depth": "int2",
    "ts_in_delta": "int4",
    **dict.fromkeys(TICK_TRADE_BBO_COLUMNS, "int8"),
    "sequence_ordinal": "int2",
    "unit_id": "int8",
}
COPY_TYPES: tuple[str, ...] = tuple(_COLUMN_TYPES[column] for column in COPY_COLUMNS)

#: Column names only come from the constants above, never from input.
COPY_SQL = f"COPY tick_trade ({', '.join(COPY_COLUMNS)}) FROM STDIN (FORMAT BINARY)"

_TEXT_FIELDS = frozenset({"action", "side"})


def _column(records: npt.NDArray[Any], field: str) -> list[Any]:
    if field not in (records.dtype.names or ()):
        if field in TICK_TRADE_BBO_COLUMNS:
            return [None] * len(records)
        raise ValueError(f"record batch has no {field!r} field")
    if field in _TEXT_FIELDS:
        return records[field].astype("U1").tolist()
    return records[field].tolist()


def copy_rows(
    records: npt.NDArray[Any], ordinals: npt.NDArray[np.int64], unit_id: int
) -> Iterator[tuple[Any, ...]]:
    """One tuple per record, in ``COPY_COLUMNS`` order."""
    columns = [_column(records, field) for field in TICK_TRADE_COLUMNS]
    columns.append(ordinals.tolist())
    columns.append([unit_id] * len(records))
    return zip(*columns, strict=True)
