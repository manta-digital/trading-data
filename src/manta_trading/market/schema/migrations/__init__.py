"""Schema migrations package.

Exposes ``TRACKS`` mapping track name → migration list, plus a
backward-compatibility alias ``MIGRATIONS`` pointing at the minute track.
"""

from __future__ import annotations

from manta_trading.market.schema.migrations.daily import DAILY_MIGRATIONS
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

TRACKS: dict[str, list[dict[str, str]]] = {
    "minute": MINUTE_MIGRATIONS,
    "daily": DAILY_MIGRATIONS,
}

# Deprecated: use TRACKS["minute"] for new code.
MIGRATIONS = MINUTE_MIGRATIONS

__all__ = ["TRACKS", "MIGRATIONS", "MINUTE_MIGRATIONS", "DAILY_MIGRATIONS"]
