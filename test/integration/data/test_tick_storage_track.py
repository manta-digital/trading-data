"""The tick storage track on a migrated tick database (slice 222).

Track shape and re-apply (Functional Requirement 1), extensions, hypertable
geometry (FR2), column-contract parity with the migrated tables, and the
exactness round trip on real DBN records (FR8). Constraint cases live in
``test_tick_storage_constraints.py``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit

from manta_trading.data.tick.constants import TICK_TRADE_CHUNK_INTERVAL, UnitState
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.storage_columns import (
    TICK_DEFINITION_COLUMNS,
    TICK_DEFINITION_DERIVED_COLUMNS,
    TICK_TRADE_COLUMNS,
    TICK_TRADE_DERIVED_COLUMNS,
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


# --------------------------------------------------------------------------
# Column contract parity (task 3.14)
# --------------------------------------------------------------------------


def test_trade_columns_match_the_contract(tick_conn: Conn) -> None:
    expected = [*TICK_TRADE_COLUMNS.values(), *TICK_TRADE_DERIVED_COLUMNS]
    assert _columns(tick_conn, "tick_trade") == expected


def test_definition_columns_match_the_contract(tick_conn: Conn) -> None:
    expected = [*TICK_DEFINITION_COLUMNS.values(), *TICK_DEFINITION_DERIVED_COLUMNS]
    assert _columns(tick_conn, "tick_definition") == expected


# --------------------------------------------------------------------------
# Exactness round trip on real records (Functional Requirement 8)
# --------------------------------------------------------------------------


def _python(value: Any) -> Any:
    """A numpy scalar as the Python value a writer would send."""
    if isinstance(value, bytes | np.bytes_):
        return bytes(value).decode("ascii")
    return value.item()


def _fixture_rows(tier: str) -> list[dict[str, Any]]:
    """Every fixture record through ``TICK_TRADE_COLUMNS``; absent BBO → None."""
    file = DbnFileReader().open_file(FIXTURES / f"test_data.{tier}.v3.dbn.zst")
    rows: list[dict[str, Any]] = []
    for batch in file.iter_batches():
        fields = set(batch.records.dtype.names or ())
        for record in batch.records:
            rows.append(
                {
                    column: _python(record[field]) if field in fields else None
                    for field, column in TICK_TRADE_COLUMNS.items()
                }
            )
    return rows


def _with_derived_edge_row(rows: list[dict[str, Any]], tier: str) -> None:
    """Append a copy of the first record carrying the widest provider values."""
    edge = dict(rows[0])
    edge["sequence"] = max(r["sequence"] for r in rows) + 1
    edge["size"] = UINT32_MAX
    if tier == "tbbo":
        edge["bid_px_00"] = INT64_MAX  # stored as delivered (TD3)
    rows.append(edge)


def _assign_ordinals(rows: list[dict[str, Any]], unit_id: int) -> None:
    """TD1's ordinal: earlier records with the same triple, keyed by a counter."""
    seen: Counter[tuple[int, int, int]] = Counter()
    for row in rows:
        triple = (row["instrument_id"], row["ts_event"], row["sequence"])
        row["sequence_ordinal"] = seen[triple]
        seen[triple] += 1
        row["unit_id"] = unit_id


@pytest.mark.parametrize("tier", ["trades", "tbbo"])
def test_real_records_round_trip_exactly(tick_conn: Conn, tier: str) -> None:
    unit_id = insert_unit(
        tick_conn,
        insert_request(tick_conn, schema=tier),
        state=UnitState.VERIFIED.value,
        **FILE_COLUMNS,
    )
    rows = _fixture_rows(tier)
    _with_derived_edge_row(rows, tier)
    _assign_ordinals(rows, unit_id)
    columns = list(rows[0])
    insert = sql.SQL("INSERT INTO tick_trade ({}) VALUES ({})").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    with tick_conn.cursor() as cur:
        cur.executemany(insert, [[row[c] for c in columns] for row in rows])

    select = sql.SQL("SELECT {} FROM tick_trade ORDER BY {}").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns),
        sql.SQL(", ").join(sql.Identifier(c) for c in TICK_TRADE_KEY),
    )
    stored = [dict(zip(columns, r, strict=True)) for r in tick_conn.execute(select)]

    def key(row: dict[str, Any]) -> tuple[int, ...]:
        return tuple(row[c] for c in TICK_TRADE_KEY)

    assert stored == sorted(rows, key=key)
