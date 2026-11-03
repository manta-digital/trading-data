"""Storage constants of the ``ohlcv-1m`` bar tier (slice 302, D2).

Every value that names or shapes the bar tables lives here and only here.
"""

from __future__ import annotations

from typing import Final

#: Per-unit, per-session bar totals; the ledger of the ``BAR_1M`` family.
BAR_LEDGER_TABLE: Final = "tick_bar_ledger"
