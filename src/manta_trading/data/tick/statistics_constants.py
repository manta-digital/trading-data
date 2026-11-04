"""Storage constants of the ``statistics`` schema (slice 303, D2, D4).

Every value that names or shapes the statistics tables lives here and only here.
"""

from __future__ import annotations

from datetime import timedelta
from enum import IntEnum
from typing import Final


class StatType(IntEnum):
    """DBN ``StatType`` values this code names; the rest are stored, never read."""

    SETTLEMENT_PRICE = 3
    CLEARED_VOLUME = 6
    OPEN_INTEREST = 9


class StatUpdateAction(IntEnum):
    """DBN ``StatUpdateAction``: a record adds a value or removes one."""

    NEW = 1
    DELETE = 2


#: One row per delivered statistics record, ``ts_recv``-keyed hypertable.
STATISTICS_TABLE: Final = "tick_statistics"

#: Per-unit, per-session, per-stat-type counts; the ledger of the ``STATISTICS`` family.
STATISTICS_LEDGER_TABLE: Final = "tick_statistics_ledger"

#: ``STATISTICS_TABLE`` columns in table order; ``unit_id`` last, as ``tick_trade``.
STATISTICS_COLUMNS: Final[tuple[str, ...]] = (
    "ts_recv",
    "ts_event",
    "ts_ref",
    "instrument_id",
    "publisher_id",
    "stat_type",
    "update_action",
    "stat_flags",
    "price",
    "quantity",
    "sequence",
    "channel_id",
    "ts_in_delta",
    "record_ordinal",
    "calendar_id",
    "session_date",
    "unit_id",
)

#: Primary key: ``record_ordinal`` keeps a record repeated within a file (D2).
STATISTICS_KEY: Final[tuple[str, ...]] = (
    "instrument_id",
    "stat_type",
    "ts_recv",
    "sequence",
    "record_ordinal",
)

#: Primary key of the ledger.
STATISTICS_LEDGER_KEY: Final[tuple[str, ...]] = (
    "unit_id",
    "instrument_id",
    "session_date",
    "stat_type",
)

#: Same rule as ``tick_trade`` (journal 20260719: span / 1,000 to 2,000 chunks).
STATISTICS_CHUNK_INTERVAL: Final[timedelta] = timedelta(days=7)

#: Two chunk intervals, as ``tick_trade``: history compresses soon after load.
STATISTICS_COMPRESS_AFTER: Final[timedelta] = timedelta(days=14)

#: Columnstore layout: segment by instrument, order by receive time.
STATISTICS_SEGMENT_BY: Final[tuple[str, ...]] = ("instrument_id",)
STATISTICS_ORDER_BY: Final[tuple[str, ...]] = ("ts_recv",)

#: Types whose ``ts_ref`` names a trading date (D4); the projector's only branch
#: on stat type is membership of this set.
REFERENCED_STAT_TYPES: Final[frozenset[StatType]] = frozenset(
    {StatType.SETTLEMENT_PRICE, StatType.CLEARED_VOLUME, StatType.OPEN_INTEREST}
)

#: Types a listed outright must have for a load-complete session (D7).
REQUIRED_STAT_TYPES: Final[frozenset[StatType]] = REFERENCED_STAT_TYPES

#: CME publishes session D's statistics during the following session. This is
#: the same fact the roll lag rests on (``RANK_LAG_SESSIONS`` in ``constants.py``;
#: 220's pinned timing), so the status window for D reaches the close day of the
#: session this many after D.
STATISTICS_PUBLICATION_SESSIONS: Final = 1
