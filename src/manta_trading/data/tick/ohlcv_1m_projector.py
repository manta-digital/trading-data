"""The ``tick_ohlcv_1m`` projector: provider one-minute bars (slice 302, D3).

Follows ``TickTradeProjector``'s shape for one interval only: per decoded
batch it checks the bars lie in the unit's UTC day, resolves every instrument
to a definition, places every bar in a session and folds it into the ledger,
then ``COPY``s the batch. ``finish`` writes ``tick_bar_ledger``. A key collision
is the existing ``overlap`` failure; no ``ON CONFLICT`` ever hides it. The
``ohlcv-1s`` slice extracts what the two share when a second interval exists.
"""

from __future__ import annotations

import time
from itertools import repeat
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from manta_trading.data.tick.bar_constants import BAR_1M_COLUMNS, BAR_1M_TABLE
from manta_trading.data.tick.ingest_records import (
    LedgerAccumulator,
    LedgerRow,
    locate_sessions,
    resolve,
)
from manta_trading.data.tick.ledger_write import write_ledger
from manta_trading.data.tick.projector_types import BatchProgress, Cursor, Loaded
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.unit_day_check import check_unit_day

if TYPE_CHECKING:
    from manta_trading.data.tick.ingest_plan import UnitIngestPlan

#: PostgreSQL type of each ``BAR_1M_COLUMNS`` entry, for binary ``set_types``;
#: the ``tick_009`` DDL is the source (parity-tested).
_COLUMN_TYPES: Final[dict[str, str]] = {
    "ts_event": "int8",
    "instrument_id": "int8",
    "publisher_id": "int4",
    "open": "int8",
    "high": "int8",
    "low": "int8",
    "close": "int8",
    "volume": "int8",
    "unit_id": "int8",
}
COPY_TYPES: Final[tuple[str, ...]] = tuple(_COLUMN_TYPES[c] for c in BAR_1M_COLUMNS)
#: Column names only come from the constants above, never from input.
COPY_SQL: Final = (
    f"COPY {BAR_1M_TABLE} ({', '.join(BAR_1M_COLUMNS)}) FROM STDIN (FORMAT BINARY)"
)
#: The DBN record fields, which are the columns but for the unit.
_RECORD_FIELDS: Final = tuple(c for c in BAR_1M_COLUMNS if c != "unit_id")


class Ohlcv1mProjector:
    def load(
        self,
        cur: Cursor,
        plan: UnitIngestPlan,
        path: Path,
        reader: ITickFileReader,
        progress: BatchProgress | None,
    ) -> Loaded:
        loaded, ledger = Loaded(), LedgerAccumulator()
        batches = reader.open_file(path).iter_batches()
        with cur.copy(COPY_SQL) as copy:
            copy.set_types(list(COPY_TYPES))
            while True:
                started = time.monotonic()
                batch = next(batches, None)
                loaded.decode_seconds += time.monotonic() - started
                if batch is None:
                    break
                records = batch.records
                ids, ts = records["instrument_id"], records["ts_event"]
                check_unit_day(ts, plan.unit.unit_date)
                resolve(plan.definitions, ids, ts, plan.product)
                positions = locate_sessions(plan.frame, ts)
                ledger.add(ids, positions, records["volume"], ts)
                columns = [records[field].tolist() for field in _RECORD_FIELDS]
                for row in zip(
                    *columns, repeat(plan.unit.unit_id, batch.count), strict=True
                ):
                    copy.write_row(row)
                if batch.count:
                    loaded.note_batch(batch.count, int(ts.min()), int(ts.max()))
                    if progress is not None:
                        progress(batch.count)
        loaded.bookkeeping = ledger.rows(plan.definitions, plan.frame, plan.calendar_id)
        return loaded

    def stored_count(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> int:
        """Bar rows of this unit, bounded by its time range."""
        if first_ns is None:
            return 0
        cur.execute(
            f"SELECT count(*) FROM {BAR_1M_TABLE} WHERE unit_id = %s"
            " AND ts_event BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )
        return int(cur.fetchone()[0])  # type: ignore[index]

    def finish(self, cur: Cursor, unit_id: int, loaded: Loaded) -> None:
        rows = cast("list[LedgerRow]", loaded.bookkeeping)
        write_ledger(cur, FAMILY_LEDGER[SchemaFamily.BAR_1M], unit_id, rows)

    def delete_unit(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> None:
        """Delete the unit's bars; its ledger rows stay (225 TD5).

        Bounded by the unit's own ledger times, so chunk exclusion limits it to
        the chunks the unit touched. A unit with no ledger times holds no rows.
        """
        if first_ns is None:
            return
        cur.execute(
            f"DELETE FROM {BAR_1M_TABLE} WHERE unit_id = %s"
            " AND ts_event BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )


OHLCV_1M_PROJECTOR = Ohlcv1mProjector()
