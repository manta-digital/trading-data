"""Reads of the stored statistics (slice 303, D5).

``resolved`` is the only place a statistic value is chosen. Records of one
(contract, statistic, session) arrive in many units and may revise each other;
the value read is the latest by receive time, then ``sequence``, then
``record_ordinal``, over the current units of the shape. Resolution is at read
time, not in a stored table: corrections arrive in later units, and a stored
winner would need rewriting on every ingest. SQL is written here from code
constants; every value is bound.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_TABLE,
    StatType,
    StatUpdateAction,
)
from manta_trading.data.tick.status_reads import SHAPE_WHERE, Shape


@dataclass(frozen=True)
class ResolvedStatistic:
    """The winning record of one (contract, statistic, session)."""

    instrument_id: int
    stat_type: int
    session_date: date
    price: int | None
    quantity: int | None
    unit_id: int


#: ``DISTINCT ON`` keeps the first row of each key under this order: the latest
#: record. A winning ``DELETE`` is dropped by the outer query, so the key has no
#: value, whatever earlier records said.
_RESOLVED = (
    "SELECT instrument_id, stat_type, session_date, price, quantity, unit_id"
    " FROM ("
    "SELECT DISTINCT ON (s.instrument_id, s.stat_type, s.session_date)"
    " s.instrument_id, s.stat_type, s.session_date, s.update_action,"
    " s.price, s.quantity, s.unit_id"
    f" FROM {STATISTICS_TABLE} s JOIN tick_archive_unit u ON u.unit_id = s.unit_id"
    " JOIN tick_request r ON r.request_id = u.request_id"
    f" WHERE {SHAPE_WHERE}"
    " AND s.stat_type = ANY(%(stat_types)s)"
    " AND s.session_date BETWEEN %(first)s AND %(last)s"
    " ORDER BY s.instrument_id, s.stat_type, s.session_date,"
    " s.ts_recv DESC, s.sequence DESC, s.record_ordinal DESC"
    ") latest WHERE update_action <> %(delete)s"
    " ORDER BY session_date, instrument_id, stat_type"
)


async def resolved(
    conn: Conn,
    shape: Shape,
    stat_types: Collection[StatType],
    first: date,
    last: date,
) -> tuple[ResolvedStatistic, ...]:
    """One row per (instrument, stat type, session) in ``[first, last]``.

    Over the current units of ``shape`` (superseded and reopened units never
    count). Records with no session never appear.
    """
    cursor = await conn.execute(
        _RESOLVED,
        {
            **shape.params(SchemaFamily.STATISTICS),
            "stat_types": sorted(int(stat_type) for stat_type in stat_types),
            "first": first,
            "last": last,
            "delete": int(StatUpdateAction.DELETE),
        },
    )
    return tuple(ResolvedStatistic(*row) for row in await cursor.fetchall())
