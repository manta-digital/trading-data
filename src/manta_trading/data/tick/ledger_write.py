"""Write a unit's ledger rows (slices 225, 302).

Both ledgers (``tick_ingest_ledger`` for ticks, ``tick_bar_ledger`` for bars)
have the same columns, so one writer serves every projector. The table is a
code constant from ``FAMILY_LEDGER``; every value is bound.
"""

from __future__ import annotations

from collections.abc import Sequence

from psycopg import sql

from manta_trading.data.tick.ingest_records import LedgerRow
from manta_trading.data.tick.projector_types import Cursor


def write_ledger(
    cur: Cursor, table: str, unit_id: int, rows: Sequence[LedgerRow]
) -> None:
    query = sql.SQL(
        "INSERT INTO {} (unit_id, instrument_id, calendar_id, session_date,"
        " record_count, volume, first_event_ns, last_event_ns)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    ).format(sql.Identifier(table))
    cur.executemany(
        query,
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
