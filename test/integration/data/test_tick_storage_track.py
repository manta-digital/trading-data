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

from manta_trading.data.tick.constants import TICK_TRADE_CHUNK_INTERVAL
from manta_trading.data.tick.storage_columns import (
    TICK_TRADE_KEY,
)
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.migrations.tick import _interval_ns
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


def _columns(conn: Conn, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = %s"
        " ORDER BY ordinal_position",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


# --------------------------------------------------------------------------
# Hypertable geometry (Functional Requirement 2)
# --------------------------------------------------------------------------


def test_trade_hypertable_has_one_integer_dimension(tick_conn: Conn) -> None:
    rows = tick_conn.execute(
        "SELECT column_name, column_type::text, integer_interval"
        " FROM timescaledb_information.dimensions"
        " WHERE hypertable_name = 'tick_trade'"
    ).fetchall()
    assert rows == [("ts_event", "bigint", _interval_ns(TICK_TRADE_CHUNK_INTERVAL))]


def test_trade_hypertable_has_no_compression(tick_conn: Conn) -> None:
    row = tick_conn.execute(
        "SELECT compression_enabled FROM timescaledb_information.hypertables"
        " WHERE hypertable_name = 'tick_trade'"
    ).fetchone()
    assert row == (False,)


def test_trade_has_only_the_primary_key_index(tick_conn: Conn) -> None:
    rows = tick_conn.execute(
        "SELECT indexname FROM pg_indexes"
        " WHERE schemaname = 'public' AND tablename = 'tick_trade'"
    ).fetchall()
    assert rows == [("tick_trade_pkey",)]
    key = tick_conn.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'tick_trade_pkey'"
    ).fetchone()
    assert key == (f"PRIMARY KEY ({', '.join(TICK_TRADE_KEY)})",)
