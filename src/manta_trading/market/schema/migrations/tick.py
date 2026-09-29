"""Schema migration definitions for the ``tick`` track (slices 923, 222).

The track targets the tick database, never the primary ``trading`` database;
``TRACK_REGISTRY`` routes it there. Its ledger lives in the tick database and
holds only this track's ids.

It starts with the shared ``001_schema_migrations`` bootstrap, reused from the
minute track rather than copied. Every later id is prefixed ``tick_NNN_*``
(slice 222 onward) so it can never collide with a primary-track id.

Slice 222 adds the five storage tables. SQL is idempotent (IF NOT EXISTS,
``if_not_exists => TRUE``, constraints named inside the CREATE). Every enum
CHECK is rendered from its enum, never hand-listed (222 TD9). No migration
grants anything: ``tick_migrate``'s default privileges give ``tick_app`` DML,
and ``scripts/provision_tick_roles.sql`` enumerates the audited write surface.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from enum import StrEnum

from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_BBO_COLUMNS,
)
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import BOOTSTRAP_MIGRATION_ID

_NS_PER_MICROSECOND = 1_000


def _in_list(members: Iterable[StrEnum]) -> str:
    """Render enum members as a quoted SQL list, sorted by value.

    The Kalshi track's idiom, copied: the tick track must not import from
    ``data/kalshi`` (architecture 220).
    """
    return ", ".join(f"'{m.value}'" for m in sorted(members, key=lambda m: m.value))


def _check_in(column: str, members: Iterable[StrEnum]) -> str:
    return f"CHECK ({column} IN ({_in_list(members)}))"


def _interval_ns(span: timedelta) -> int:
    """A timedelta as exact integer nanoseconds (no float arithmetic)."""
    return span // timedelta(microseconds=1) * _NS_PER_MICROSECOND


def _bbo_check() -> str:
    """Every BBO column is NULL exactly when ``bid_px_00`` is (222 TD3)."""
    anchor, *others = TICK_TRADE_BBO_COLUMNS
    pairs = " AND ".join(f"(({c} IS NULL) = ({anchor} IS NULL))" for c in others)
    return f"CHECK ({pairs})"


TICK_MIGRATIONS: list[dict[str, str]] = [
    next(m for m in MINUTE_MIGRATIONS if m["id"] == BOOTSTRAP_MIGRATION_ID),
    {
        "id": "tick_001_extensions",
        "description": "Install TimescaleDB and btree_gist in the tick database",
        # btree_gist backs tick_definition's validity-window exclusion
        # (222 TD5). It is a trusted extension, so the database owner
        # (tick_migrate) creates it with no added attribute.
        "sql": """
            CREATE EXTENSION IF NOT EXISTS timescaledb;
            CREATE EXTENSION IF NOT EXISTS btree_gist;
        """,
    },
]
