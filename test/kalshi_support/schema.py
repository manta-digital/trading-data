"""Kalshi schema helpers shared by the integration and load tiers (188 D11).

Only the *schema* helpers live here. The fixture row builders stay in
``test/integration/kalshi_helpers.py``: both tiers need a migrated database,
but only the integration tier writes recorded catalog shapes into one.

Both functions only ever run against a database a fixture in this repository
minted — never the production URL.
"""

from __future__ import annotations

from typing import Any

import psycopg


def ensure_timescaledb(url: str) -> str:
    """Give a throwaway database the TimescaleDB extension, as production has.

    The kalshi track targets the shared ``trading`` database, where the
    minute track's ``001a`` created the extension long ago; ``kalshi_005``
    (slice 264) creates a hypertable and assumes it. ``CREATE EXTENSION``
    cannot run inside a transaction block. Only ever called on a database a
    fixture in this tier minted.
    """
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")
    return url


def apply_kalshi_track(url: str) -> str:
    """Extension, then the whole kalshi track — spelled once, for the
    ``kalshi_db`` fixture, ``test_kalshi_pass.second_kalshi_db``, and the load
    tier's ``kalshi_dense_db``."""
    from psycopg_pool import ConnectionPool

    from manta_trading.market.schema.migrations import TRACKS
    from manta_trading.market.schema.runner import apply_migrations

    ensure_timescaledb(url)
    with ConnectionPool[Any](url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, TRACKS["kalshi"])
    return url
