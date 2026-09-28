"""Schema migrations package.

``TRACK_REGISTRY`` maps each track name to the database it targets and its
migration list (slice 923 D1); a track's database is stated only there.
``TRACKS`` is derived from it with the long-standing name → migration-list
shape, plus a backward-compatibility alias ``MIGRATIONS`` pointing at the
minute track.
"""

from __future__ import annotations

from dataclasses import dataclass

from manta_trading.market.schema.databases import Database
from manta_trading.market.schema.migrations.daily import DAILY_MIGRATIONS
from manta_trading.market.schema.migrations.kalshi import KALSHI_MIGRATIONS
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.migrations.tick import TICK_MIGRATIONS


@dataclass(frozen=True)
class TrackSpec:
    """A migration track: the database it targets and its ordered migrations."""

    database: Database
    migrations: list[dict[str, str]]


TRACK_REGISTRY: dict[str, TrackSpec] = {
    "minute": TrackSpec(Database.PRIMARY, MINUTE_MIGRATIONS),
    "daily": TrackSpec(Database.PRIMARY, DAILY_MIGRATIONS),
    "kalshi": TrackSpec(Database.PRIMARY, KALSHI_MIGRATIONS),
    "tick": TrackSpec(Database.TICK, TICK_MIGRATIONS),
}

TRACKS: dict[str, list[dict[str, str]]] = {
    name: spec.migrations for name, spec in TRACK_REGISTRY.items()
}

#: The track ``mt data init --database D`` applies for each database.
DEFAULT_TRACK_FOR: dict[Database, str] = {
    Database.PRIMARY: "minute",
    Database.TICK: "tick",
}

#: The track ``mt data migrate`` and the DB wrappers act on when none is named.
DEFAULT_TRACK = DEFAULT_TRACK_FOR[Database.PRIMARY]

# Deprecated: use TRACKS["minute"] for new code.
MIGRATIONS = MINUTE_MIGRATIONS

__all__ = [
    "TRACK_REGISTRY",
    "TrackSpec",
    "TRACKS",
    "DEFAULT_TRACK",
    "DEFAULT_TRACK_FOR",
    "MIGRATIONS",
    "MINUTE_MIGRATIONS",
    "DAILY_MIGRATIONS",
    "KALSHI_MIGRATIONS",
    "TICK_MIGRATIONS",
]
