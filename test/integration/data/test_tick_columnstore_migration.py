"""``tick_007_trade_columnstore`` (slice 226, TD4; TD5 Migration row).

On an empty tick database and on one populated (uncompressed) before
``tick_007`` arrives: the layout read back from TimescaleDB's information
views matches the constants, the columnstore policy exists with the
constant's age, and ``tick_now_ns()`` is within a second of ``now()``.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg_pool import ConnectionPool
from tick_support.rows import FILE_COLUMNS, insert_request, insert_trade, insert_unit

from manta_trading.data.tick.constants import (
    TICK_TRADE_COMPRESS_AFTER,
    TICK_TRADE_ORDER_BY,
    TICK_TRADE_SEGMENT_BY,
    UnitState,
)
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.migrations.tick import interval_to_ns
from manta_trading.market.schema.runner import apply_migrations

NS_PER_SECOND = 1_000_000_000


def _apply(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=1) as pool:
        apply_migrations(pool, migrations)


def _assert_columnstore(url: str) -> None:
    with psycopg.connect(url) as conn:
        settings = conn.execute(
            "SELECT segmentby, orderby"
            " FROM timescaledb_information.hypertable_columnstore_settings"
            " WHERE hypertable = 'tick_trade'::regclass"
        ).fetchone()
        policy = conn.execute(
            "SELECT config FROM timescaledb_information.jobs"
            " WHERE hypertable_name = 'tick_trade'"
            " AND proc_name = 'policy_compression'"
        ).fetchall()
        drift = conn.execute(
            "SELECT abs(tick_now_ns()"
            " - (extract(epoch FROM now()) * 1000000000)::bigint)"
        ).fetchone()
    assert settings is not None
    segment_by, order_by = settings
    assert segment_by.split(",") == list(TICK_TRADE_SEGMENT_BY)
    assert [term.split()[0] for term in order_by.split(",")] == list(
        TICK_TRADE_ORDER_BY
    )
    assert len(policy) == 1
    assert policy[0][0]["compress_after"] == interval_to_ns(TICK_TRADE_COMPRESS_AFTER)
    assert drift is not None and drift[0] < NS_PER_SECOND


def test_tick_007_on_an_empty_database(migrated_tick_db: str) -> None:
    _assert_columnstore(migrated_tick_db)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(TRACKS["tick"][-1]["sql"])  # its SQL re-applies cleanly
    _assert_columnstore(migrated_tick_db)


def test_tick_007_on_a_populated_uncompressed_database(ephemeral_tick_db: str) -> None:
    url = ephemeral_tick_db
    *before, last = TRACKS["tick"]
    assert last["id"] == "tick_007_trade_columnstore"
    _apply(url, before)
    with psycopg.connect(url, autocommit=True) as conn:
        unit = insert_unit(
            conn,
            insert_request(conn),
            state=UnitState.INGESTED.value,
            **FILE_COLUMNS,
        )
        insert_trade(conn, unit)
    _apply(url, TRACKS["tick"])
    _assert_columnstore(url)
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM tick_trade").fetchone() == (1,)
