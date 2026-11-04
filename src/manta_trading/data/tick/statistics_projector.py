"""The ``tick_statistics`` projector: provider statistics records (slice 303, D3).

Follows ``Ohlcv1mProjector``'s shape: per decoded batch it checks the records
lie in the unit's UTC day (by receive time), resolves every instrument to a
definition, assigns each referenced record its session from ``ts_ref`` (D4),
numbers repeats of a key, folds referenced records into the ledger, then
``COPY``s the batch. ``finish`` writes ``tick_statistics_ledger``. A key
collision is the existing ``overlap`` failure; no ``ON CONFLICT`` hides it.
``session_boundary`` does not apply: no statistics record is placed by its own
time. Undefined provider values are stored NULL, never as DBN sentinels.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast

import numpy as np
import numpy.typing as npt

from manta_trading.data.tick.ingest_records import (
    I64,
    OrdinalCarry,
    resolve,
)
from manta_trading.data.tick.ledger_write import write_statistics_ledger
from manta_trading.data.tick.projector_types import BatchProgress, Cursor, Loaded
from manta_trading.data.tick.provider import ITickFileReader
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_COLUMNS,
    STATISTICS_TABLE,
)
from manta_trading.data.tick.statistics_ledger import (
    StatisticsLedgerAccumulator,
    StatisticsLedgerRow,
)
from manta_trading.data.tick.statistics_session import (
    EPOCH_DATE,
    NO_DAY,
    UNDEF_TS_REF,
    assign_batch,
)
from manta_trading.data.tick.unit_day_check import check_unit_day
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

if TYPE_CHECKING:
    from manta_trading.data.tick.ingest_plan import UnitIngestPlan

#: PostgreSQL type of each ``STATISTICS_COLUMNS`` entry, for binary
#: ``set_types``; the ``tick_010`` DDL is the source (parity-tested).
_COLUMN_TYPES: Final[dict[str, str]] = {
    "ts_recv": "int8",
    "ts_event": "int8",
    "ts_ref": "int8",
    "instrument_id": "int8",
    "publisher_id": "int4",
    "stat_type": "int2",
    "update_action": "int2",
    "stat_flags": "int2",
    "price": "int8",
    "quantity": "int8",
    "sequence": "int8",
    "channel_id": "int4",
    "ts_in_delta": "int4",
    "record_ordinal": "int2",
    "calendar_id": "text",
    "session_date": "date",
    "unit_id": "int8",
}
COPY_TYPES: Final[tuple[str, ...]] = tuple(_COLUMN_TYPES[c] for c in STATISTICS_COLUMNS)
#: Column names only come from the constants above, never from input.
COPY_SQL: Final = (
    f"COPY {STATISTICS_TABLE} ({', '.join(STATISTICS_COLUMNS)})"
    " FROM STDIN (FORMAT BINARY)"
)
#: ``UNDEF_PRICE`` (``i64::MAX``). The quantity sentinel is the maximum of its
#: field's width, which is narrower in DBN v1.
_UNDEF_PRICE: Final = int(np.iinfo(np.int64).max)


def _nullable(column: npt.NDArray[Any], sentinel: int) -> list[int | None]:
    """The column as Python ints, the provider's undefined value as ``None``."""
    return [None if value == sentinel else value for value in column.tolist()]


def _ordinals(
    carries: dict[int, OrdinalCarry],
    ids: I64,
    ts_recv: I64,
    sequence: I64,
    types: I64,
) -> I64:
    """``record_ordinal``: ``OrdinalCarry`` per stat type, so the type is part of
    the key and the carry across batches is the tick path's."""
    result = np.empty(len(ids), dtype=np.int64)
    for stat_type in np.unique(types).tolist():
        rows = types == stat_type
        carry = carries.setdefault(stat_type, OrdinalCarry())
        result[rows] = carry.ordinals(ids[rows], ts_recv[rows], sequence[rows])
    return result


class StatisticsProjector:
    def load(
        self,
        cur: Cursor,
        plan: UnitIngestPlan,
        path: Path,
        reader: ITickFileReader,
        progress: BatchProgress | None,
    ) -> Loaded:
        calendar = plan.reference_calendar
        assert calendar is not None, "a statistics unit's plan carries its calendar"
        loaded, ledger = Loaded(), StatisticsLedgerAccumulator(calendar.calendar_id)
        carries: dict[int, OrdinalCarry] = {}
        before_dataset = 0
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
                ids = records["instrument_id"].astype(np.int64)
                ts_recv = records["ts_recv"].astype(np.int64)
                types = records["stat_type"].astype(np.int64)
                check_unit_day(ts_recv, plan.unit.unit_date)
                resolve(plan.definitions, ids, records["ts_event"], plan.product)
                sessions = assign_batch(
                    ids, types, records["ts_ref"], plan.unit.unit_date, calendar
                )
                before_dataset += sessions.before_dataset
                has_session = sessions.days != NO_DAY
                ledger.add(
                    ids[has_session],
                    types[has_session],
                    sessions.days[has_session],
                    ts_recv[has_session],
                )
                ordinals = _ordinals(
                    carries, ids, ts_recv, records["sequence"].astype(np.int64), types
                )
                for row in self._rows(records, ordinals, sessions.days, plan):
                    copy.write_row(row)
                if batch.count:
                    loaded.note_batch(
                        batch.count, int(ts_recv.min()), int(ts_recv.max())
                    )
                    if progress is not None:
                        progress(batch.count)
        if before_dataset:
            loaded.notes.append(
                f"{before_dataset} records reference dates before {CME_DATASET_START}"
            )
        loaded.bookkeeping = ledger.rows()
        return loaded

    @staticmethod
    def _rows(
        records: npt.NDArray[Any], ordinals: I64, days: I64, plan: UnitIngestPlan
    ) -> zip[tuple[Any, ...]]:
        """The COPY rows of a batch, in ``STATISTICS_COLUMNS`` order."""
        calendar = plan.reference_calendar
        assert calendar is not None
        dates = {
            day: EPOCH_DATE + timedelta(days=day)
            for day in np.unique(days[days != NO_DAY]).tolist()
        }
        session_dates: list[date | None] = [dates.get(day) for day in days.tolist()]
        calendar_ids = [
            None if d is None else calendar.calendar_id for d in session_dates
        ]
        quantity_undefined = int(np.iinfo(records["quantity"].dtype).max)
        columns = [
            records["ts_recv"].tolist(),
            records["ts_event"].tolist(),
            _nullable(records["ts_ref"], UNDEF_TS_REF),
            records["instrument_id"].tolist(),
            records["publisher_id"].tolist(),
            records["stat_type"].tolist(),
            records["update_action"].tolist(),
            records["stat_flags"].tolist(),
            _nullable(records["price"], _UNDEF_PRICE),
            _nullable(records["quantity"], quantity_undefined),
            records["sequence"].tolist(),
            records["channel_id"].tolist(),
            records["ts_in_delta"].tolist(),
            ordinals.tolist(),
            calendar_ids,
            session_dates,
            [plan.unit.unit_id] * len(ordinals),
        ]
        return zip(*columns, strict=True)

    def stored_count(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> int:
        """Statistics rows of this unit, bounded by its receive-time range."""
        if first_ns is None:
            return 0
        cur.execute(
            f"SELECT count(*) FROM {STATISTICS_TABLE} WHERE unit_id = %s"
            " AND ts_recv BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )
        return int(cur.fetchone()[0])  # type: ignore[index]

    def finish(self, cur: Cursor, unit_id: int, loaded: Loaded) -> None:
        rows = cast("list[StatisticsLedgerRow]", loaded.bookkeeping)
        write_statistics_ledger(
            cur, FAMILY_LEDGER[SchemaFamily.STATISTICS], unit_id, rows
        )

    def delete_unit(
        self, cur: Cursor, unit_id: int, first_ns: int | None, last_ns: int | None
    ) -> None:
        """Delete the unit's rows; its ledger rows stay (225 TD5).

        Bounded by the unit's own ledger times, so chunk exclusion limits it to
        the chunks the unit touched. A unit with no ledger times holds no rows.
        """
        if first_ns is None:
            return
        cur.execute(
            f"DELETE FROM {STATISTICS_TABLE} WHERE unit_id = %s"
            " AND ts_recv BETWEEN %s AND %s",
            (unit_id, first_ns, last_ns),
        )


STATISTICS_PROJECTOR = StatisticsProjector()
