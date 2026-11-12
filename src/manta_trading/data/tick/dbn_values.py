"""DBN values the tick-bar cutter needs, standard library only (slice 308 D1).

``tick_bars`` must import without the database stack, so trading-engine and
the live process (280) can run it. ``constants.py`` cannot be its source: it
imports ``manta_trading.constants`` and the calendar seed modules. The values
live here once; ``constants.py`` and ``tick_format.py`` import them.
"""

from __future__ import annotations

from typing import Final

#: DBN ``UNDEF_PRICE`` (``i64::MAX``): an undefined price or quantity field.
UNDEF_INT64: Final[int] = 2**63 - 1

#: DBN ``UNDEF_ORDER_SIZE`` (``u32::MAX``): an undefined size field. Stored as
#: delivered (``storage_columns``), so a trade row may carry it.
UNDEF_UINT32: Final[int] = 2**32 - 1

#: DBN aggressor sides of a trade. ``N`` (no side) is neither.
TRADE_SIDE_BUY: Final[str] = "B"
TRADE_SIDE_SELL: Final[str] = "A"
