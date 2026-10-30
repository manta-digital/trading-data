"""The ``tick_trade`` projector: COPY, stored count, ledger, supersession delete.

Moved out of ``ingest_worker`` by slice 300 (D2) with no change to the SQL or the
behaviour. It serves the tick tiers (``trades``, ``tbbo``): their records land in
``tick_trade`` and their per-instrument, per-session totals in
``tick_ingest_ledger``, which the catalog, roll and status readers mean by
"trades".
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, cast

from manta_trading.data.tick.ingest_records import (
    LedgerAccumulator,
    LedgerRow,
    OrdinalCarry,
    locate_sessions,
    resolve,
)
from manta_trading.data.tick.ingest_rows import COPY_SQL, COPY_TYPES, copy_rows
from manta_trading.data.tick.projector_types import (
    BatchProgress,
    Cursor,
    Loaded,
)
from manta_trading.data.tick.provider import ITickFileReader

if TYPE_CHECKING:
    from manta_trading.data.tick.ingest_plan import UnitIngestPlan


class TickTradeProjector:
    def load(
        self,
        cur: Cursor,
        plan: UnitIngestPlan,
        path: Path,
        reader: ITickFileReader,
        progress: BatchProgress | None,
    ) -> Loaded:
        loaded, carry, ledger = Loaded(), OrdinalCarry(), LedgerAccumulator()
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
                resolve(plan.definitions, ids, ts, plan.product)
                positions = locate_sessions(plan.frame, ts)
                ordinals = carry.ordinals(ids, ts, records["sequence"])
                ledger.add(ids, positions, records["size"], ts)
                for row in copy_rows(records, ordinals, plan.unit.unit_id):
                    copy.write_row(row)
                if batch.count:
                    low, high = int(ts.min()), int(ts.max())
                    loaded.first_ns = (
                        low if loaded.first_ns is None else min(loaded.first_ns, low)
                    )
                    loaded.last_ns = (
                        high if loaded.last_ns is None else max(loaded.last_ns, high)
                    )
                loaded.decoded += batch.count
                if progress is not None and batch.count:
                    progress(batch.count)
        loaded.bookkeeping = ledger.rows(plan.definitions, plan.frame, plan.calendar_id)
        return loaded

    def stored_count(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> int:
        """Trade rows of this unit, bounded by its time range."""
        if first_ns is None:
            return 0
        cur.execute(
            "SELECT count(*) FROM tick_trade WHERE unit_id = %s"
            " AND ts_event BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )
        return int(cur.fetchone()[0])  # type: ignore[index]

    def finish(self, cur: Cursor, unit_id: int, loaded: Loaded) -> None:
        rows = cast("list[LedgerRow]", loaded.bookkeeping)
        cur.executemany(
            "INSERT INTO tick_ingest_ledger (unit_id, instrument_id, calendar_id,"
            " session_date, record_count, volume, first_event_ns, last_event_ns)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    unit_id,
                    row.instrument_id,
                    row.calendar_id,
                    row.session_date,
                    row.record_count,
                    row.volume,
                    row.first_event_ns,
                    row.last_event_ns,
                )
                for row in rows
            ],
        )

    def delete_unit(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> None:
        """Delete the unit's trade rows; its ledger rows stay (TD5).

        Bounded by the unit's own ledger times, so chunk exclusion limits it to
        the chunks the unit touched. A unit with no ledger times holds no rows.
        """
        if first_ns is None:
            return
        cur.execute(
            "DELETE FROM tick_trade WHERE unit_id = %s AND ts_event BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )


TICK_TRADE = TickTradeProjector()
