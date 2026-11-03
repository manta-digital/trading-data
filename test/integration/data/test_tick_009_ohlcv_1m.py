"""``tick_009_ohlcv_1m`` (slice 302, D2): the bar hypertable and its ledger."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool
from tick_support.rows import FILE_COLUMNS, insert_request, insert_trade, insert_unit

from manta_trading.data.tick.bar_constants import (
    BAR_1M_CHUNK_INTERVAL,
    BAR_1M_COMPRESS_AFTER,
    BAR_1M_ORDER_BY,
    BAR_1M_SEGMENT_BY,
    BAR_1M_TABLE,
    BAR_LEDGER_TABLE,
)
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.migrations.tick import interval_to_ns
from manta_trading.market.schema.runner import apply_migrations

MIGRATION_ID = "tick_009_ohlcv_1m"

_BAR = (
    f"INSERT INTO {BAR_1M_TABLE} (ts_event, instrument_id, publisher_id, open,"
    " high, low, close, volume, unit_id) VALUES (%s, 42, 1, 1, 2, 1, 2, 5, %s)"
)


def _migration() -> dict[str, Any]:
    (found,) = [m for m in TRACKS["tick"] if m["id"] == MIGRATION_ID]
    return found


def _apply(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=1) as pool:
        apply_migrations(pool, migrations)


def test_it_is_the_last_tick_migration() -> None:
    assert TRACKS["tick"][-1]["id"] == MIGRATION_ID


def test_tick_request_accepts_ohlcv_1m_and_still_rejects_unknown(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_request(conn, schema=TickSchema.OHLCV_1M.value)
        with pytest.raises(errors.CheckViolation, match="tick_request_schema_check"):
            insert_request(conn, schema="ohlcv-1s")


def test_the_bar_table_layout(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        key = conn.execute(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            " AND a.attnum = ANY (i.indkey)"
            f" WHERE i.indrelid = '{BAR_1M_TABLE}'::regclass AND i.indisprimary"
            " ORDER BY array_position(i.indkey::int2[], a.attnum)"
        ).fetchall()
        chunk = conn.execute(
            "SELECT d.integer_interval FROM timescaledb_information.dimensions d"
            f" WHERE d.hypertable_name = '{BAR_1M_TABLE}'"
        ).fetchone()
        settings = conn.execute(
            "SELECT segmentby, orderby"
            " FROM timescaledb_information.hypertable_columnstore_settings"
            f" WHERE hypertable = '{BAR_1M_TABLE}'::regclass"
        ).fetchone()
        policy = conn.execute(
            "SELECT config FROM timescaledb_information.jobs"
            f" WHERE hypertable_name = '{BAR_1M_TABLE}'"
            " AND proc_name = 'policy_compression'"
        ).fetchall()
    assert [row[0] for row in key] == ["instrument_id", "ts_event"]
    assert chunk == (interval_to_ns(BAR_1M_CHUNK_INTERVAL),)
    assert settings is not None
    assert settings[0].split(",") == list(BAR_1M_SEGMENT_BY)
    assert [term.split()[0] for term in settings[1].split(",")] == list(BAR_1M_ORDER_BY)
    assert len(policy) == 1
    assert policy[0][0]["compress_after"] == interval_to_ns(BAR_1M_COMPRESS_AFTER)


def test_a_duplicate_instrument_and_minute_is_refused(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="ohlcv-1m"))
        conn.execute(_BAR, (1_000, unit))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(_BAR, (1_000, unit))
        conn.execute(_BAR, (2_000, unit))


def test_the_ledger_has_the_tick_ledgers_key_and_checks(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="ohlcv-1m"))
        insert = (
            f"INSERT INTO {BAR_LEDGER_TABLE} (unit_id, instrument_id, calendar_id,"
            " session_date, record_count, volume, first_event_ns, last_event_ns)"
            " VALUES (%s, 42, 'CME_EQUITY', '2024-12-03', %s, 0, %s, %s)"
        )
        conn.execute(insert, (unit, 2, 1, 2))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(insert, (unit, 2, 1, 2))
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert.replace("42", "43"), (unit, 0, 1, 2))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(insert, (unit + 999, 2, 1, 2))


def test_reapplying_is_a_no_op(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(_migration()["sql"])
        conn.execute(_migration()["sql"])
        count = conn.execute(
            "SELECT count(*) FROM timescaledb_information.jobs"
            f" WHERE hypertable_name = '{BAR_1M_TABLE}'"
            " AND proc_name = 'policy_compression'"
        ).fetchone()
    assert count == (1,)


def test_a_populated_database_keeps_its_rows(ephemeral_tick_db: str) -> None:
    url = ephemeral_tick_db
    ids = [m["id"] for m in TRACKS["tick"]]
    _apply(url, TRACKS["tick"][: ids.index(MIGRATION_ID)])
    with psycopg.connect(url, autocommit=True) as conn:
        request = insert_request(conn)
        unit = insert_unit(
            conn, request, state=UnitState.INGESTED.value, **FILE_COLUMNS
        )
        insert_trade(conn, unit)
    _apply(url, TRACKS["tick"])
    with psycopg.connect(url) as conn:
        assert conn.execute("SELECT count(*) FROM tick_trade").fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM tick_request").fetchone() == (1,)
