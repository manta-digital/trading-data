"""Storage constants of the ``ohlcv-1m`` bar tier (slice 302, D2).

Every value that names or shapes the bar tables lives here and only here.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

#: Provider one-minute bars: one row per instrument and minute.
BAR_1M_TABLE: Final = "tick_ohlcv_1m"

#: Per-unit, per-session bar totals; the ledger of the ``BAR_1M`` family.
BAR_LEDGER_TABLE: Final = "tick_bar_ledger"

#: ``BAR_1M_TABLE`` columns in table order; ``unit_id`` last, as in ``tick_trade``.
BAR_1M_COLUMNS: Final[tuple[str, ...]] = (
    "ts_event",
    "instrument_id",
    "publisher_id",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "unit_id",
)

#: Primary key of ``BAR_1M_TABLE``: one provider bar per instrument and minute.
BAR_1M_KEY: Final[tuple[str, ...]] = ("instrument_id", "ts_event")

#: Same rule as ``tick_trade`` (journal 20260719: span / 1,000 to 2,000 chunks);
#: 2010 to 2030 gives about 1,040 chunks.
BAR_1M_CHUNK_INTERVAL: Final[timedelta] = timedelta(days=7)

#: Two chunk intervals, as ``tick_trade``: history compresses soon after load.
BAR_1M_COMPRESS_AFTER: Final[timedelta] = timedelta(days=14)

#: Columnstore layout: segment by instrument, order by event time.
BAR_1M_SEGMENT_BY: Final[tuple[str, ...]] = ("instrument_id",)
BAR_1M_ORDER_BY: Final[tuple[str, ...]] = ("ts_event",)
