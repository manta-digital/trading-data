"""The tick storage track on a migrated tick database (slice 222).

Track shape and re-apply (Functional Requirement 1), extensions, hypertable
geometry (FR2), column-contract parity with the migrated tables, and the
exactness round trip on real DBN records (FR8). Constraint cases live in
``test_tick_storage_constraints.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from psycopg_pool import ConnectionPool

from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.runner import apply_migrations

Conn = psycopg.Connection[Any]

FIXTURES = Path(__file__).parents[2] / "fixtures" / "databento"

#: DBN's undefined price (``UNDEF_PRICE``) and undefined ``uint32`` size.
INT64_MAX = 2**63 - 1
UINT32_MAX = 2**32 - 1


def test_extensions_are_installed(tick_conn: Conn) -> None:
    rows = tick_conn.execute("SELECT extname FROM pg_extension").fetchall()
    assert {"timescaledb", "btree_gist"} <= {r[0] for r in rows}


def test_second_apply_is_a_no_op(migrated_tick_db: str, tick_conn: Conn) -> None:
    with ConnectionPool(migrated_tick_db, min_size=1, max_size=1) as pool:
        assert apply_migrations(pool, TRACKS["tick"]) == []
    rows = tick_conn.execute(
        "SELECT migration_id FROM schema_migrations ORDER BY 1"
    ).fetchall()
    assert [r[0] for r in rows] == sorted(m["id"] for m in TRACKS["tick"])
