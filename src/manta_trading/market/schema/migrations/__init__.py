"""Schema migrations package.

Exposes ``TRACKS`` mapping track name → migration list, plus a
backward-compatibility alias ``MIGRATIONS`` pointing at the minute track.
"""

from __future__ import annotations

from manta_trading.market.schema.migrations.daily import DAILY_MIGRATIONS
from manta_trading.market.schema.migrations.kalshi import KALSHI_MIGRATIONS
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS

TRACKS: dict[str, list[dict[str, str]]] = {
    "minute": MINUTE_MIGRATIONS,
    "daily": DAILY_MIGRATIONS,
    "kalshi": KALSHI_MIGRATIONS,
}

#: The track ``mt data migrate`` and the DB wrappers act on when none is named.
DEFAULT_TRACK = "minute"

# Deprecated: use TRACKS["minute"] for new code.
MIGRATIONS = MINUTE_MIGRATIONS

__all__ = [
    "TRACKS",
    "DEFAULT_TRACK",
    "MIGRATIONS",
    "MINUTE_MIGRATIONS",
    "DAILY_MIGRATIONS",
    "KALSHI_MIGRATIONS",
]
