"""Schema migration definitions for the ``tick`` track (slice 923).

The track targets the tick database, never the primary ``trading`` database;
``TRACK_REGISTRY`` routes it there. Its ledger lives in the tick database and
holds only this track's ids.

It starts with the shared ``001_schema_migrations`` bootstrap, reused from the
minute track rather than copied. Every later id is prefixed ``tick_NNN_*``
(slice 222 onward) so it can never collide with a primary-track id.
"""

from __future__ import annotations

from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

TICK_MIGRATIONS: list[dict[str, str]] = [
    next(m for m in MINUTE_MIGRATIONS if m["id"] == BOOTSTRAP_MIGRATION_ID),
]
