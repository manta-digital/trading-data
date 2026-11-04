"""``tick_010_statistics`` (slice 303, D2): the statistics hypertable and ledger."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool
from tick_support.rows import FILE_COLUMNS, insert_request, insert_trade, insert_unit

from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.statistics_constants import (
    STATISTICS_CHUNK_INTERVAL,
    STATISTICS_COMPRESS_AFTER,
    STATISTICS_KEY,
    STATISTICS_LEDGER_KEY,
    STATISTICS_LEDGER_TABLE,
    STATISTICS_ORDER_BY,
    STATISTICS_SEGMENT_BY,
    STATISTICS_TABLE,
)
from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.migrations.tick import interval_to_ns
from manta_trading.market.schema.runner import apply_migrations

MIGRATION_ID = "tick_010_statistics"

#: Positional: ts_recv, sequence, ordinal, ts_ref, calendar_id, session_date, unit_id.
_STAT = (
    f"INSERT INTO {STATISTICS_TABLE} (ts_recv, ts_event, ts_ref, instrument_id,"
    " publisher_id, stat_type, update_action, stat_flags, price, quantity,"
    " sequence, channel_id, ts_in_delta, record_ordinal, calendar_id,"
    " session_date, unit_id)"
    " VALUES (%s, 1, %s, 42, 1, 3, 1, 0, NULL, NULL, %s, 0, 0, %s, %s, %s, %s)"
)
_REF = 1_733_184_000_000_000_000
_SESSION = ("CME_EQUITY", "2024-12-03")


def _migration() -> dict[str, Any]:
    (found,) = [m for m in TRACKS["tick"] if m["id"] == MIGRATION_ID]
    return found


def _apply(url: str, migrations: list[dict[str, Any]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=1) as pool:
        apply_migrations(pool, migrations)


def _row(
    ts_recv: int = 1_000,
    sequence: int = 1,
    ordinal: int = 0,
    ts_ref: int | None = _REF,
    session: tuple[str | None, str | None] = _SESSION,
) -> tuple[Any, ...]:
    return (ts_recv, ts_ref, sequence, ordinal, *session)


def test_it_is_the_last_tick_migration() -> None:
    assert TRACKS["tick"][-1]["id"] == MIGRATION_ID


def test_tick_request_accepts_statistics_and_still_rejects_unknown(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_request(conn, schema=TickSchema.STATISTICS.value)
        with pytest.raises(errors.CheckViolation, match="tick_request_schema_check"):
            insert_request(conn, schema="imbalance")


def test_the_table_layout(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        key = conn.execute(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            " AND a.attnum = ANY (i.indkey)"
            f" WHERE i.indrelid = '{STATISTICS_TABLE}'::regclass AND i.indisprimary"
            " ORDER BY array_position(i.indkey::int2[], a.attnum)"
        ).fetchall()
        chunk = conn.execute(
            "SELECT d.integer_interval FROM timescaledb_information.dimensions d"
            f" WHERE d.hypertable_name = '{STATISTICS_TABLE}'"
        ).fetchone()
        settings = conn.execute(
            "SELECT segmentby, orderby"
            " FROM timescaledb_information.hypertable_columnstore_settings"
            f" WHERE hypertable = '{STATISTICS_TABLE}'::regclass"
        ).fetchone()
        policy = conn.execute(
            "SELECT config FROM timescaledb_information.jobs"
            f" WHERE hypertable_name = '{STATISTICS_TABLE}'"
            " AND proc_name = 'policy_compression'"
        ).fetchall()
        index = conn.execute(
            "SELECT indexdef FROM pg_indexes"
            f" WHERE tablename = '{STATISTICS_TABLE}'"
            f" AND indexname = '{STATISTICS_TABLE}_session_idx'"
        ).fetchone()
    assert [row[0] for row in key] == list(STATISTICS_KEY)
    assert chunk == (interval_to_ns(STATISTICS_CHUNK_INTERVAL),)
    assert settings is not None
    assert settings[0].split(",") == list(STATISTICS_SEGMENT_BY)
    assert [term.split()[0] for term in settings[1].split(",")] == list(
        STATISTICS_ORDER_BY
    )
    assert len(policy) == 1
    assert policy[0][0]["compress_after"] == interval_to_ns(STATISTICS_COMPRESS_AFTER)
    assert index is not None and "(session_date, stat_type)" in index[0]


def test_a_duplicate_key_is_refused_and_an_ordinal_keeps_a_repeat(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="statistics"))
        conn.execute(_STAT, (*_row(), unit))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(_STAT, (*_row(), unit))
        conn.execute(_STAT, (*_row(ordinal=1), unit))


def test_the_session_pair_check_rejects_one_without_the_other(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="statistics"))
        for session in (("CME_EQUITY", None), (None, "2024-12-03")):
            with pytest.raises(errors.CheckViolation, match="session_pair_check"):
                conn.execute(_STAT, (*_row(session=session), unit))
        conn.execute(_STAT, (*_row(ts_ref=None, session=(None, None)), unit))


def test_a_session_without_a_reference_time_is_rejected(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="statistics"))
        with pytest.raises(errors.CheckViolation, match="session_ref_check"):
            conn.execute(_STAT, (*_row(ts_ref=None), unit))


def test_the_ledger_has_its_key_checks_and_unit_foreign_key(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn, schema="statistics"))
        key = conn.execute(
            "SELECT a.attname FROM pg_index i"
            " JOIN pg_attribute a ON a.attrelid = i.indrelid"
            " AND a.attnum = ANY (i.indkey)"
            f" WHERE i.indrelid = '{STATISTICS_LEDGER_TABLE}'::regclass"
            " AND i.indisprimary ORDER BY array_position(i.indkey::int2[], a.attnum)"
        ).fetchall()
        assert [row[0] for row in key] == list(STATISTICS_LEDGER_KEY)
        insert = (
            f"INSERT INTO {STATISTICS_LEDGER_TABLE} (unit_id, instrument_id,"
            " calendar_id, session_date, stat_type, record_count, first_event_ns,"
            " last_event_ns) VALUES (%s, 42, 'CME_EQUITY', '2024-12-03', 3, %s, %s, %s)"
        )
        conn.execute(insert, (unit, 2, 1, 2))
        with pytest.raises(errors.UniqueViolation):
            conn.execute(insert, (unit, 2, 1, 2))
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert.replace("42", "43"), (unit, 0, 1, 2))
        with pytest.raises(errors.CheckViolation):
            conn.execute(insert.replace("42", "44"), (unit, 2, 3, 2))
        with pytest.raises(errors.ForeignKeyViolation):
            conn.execute(insert.replace("42", "45"), (unit + 999, 2, 1, 2))


def test_reapplying_is_a_no_op(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(_migration()["sql"])
        conn.execute(_migration()["sql"])
        count = conn.execute(
            "SELECT count(*) FROM timescaledb_information.jobs"
            f" WHERE hypertable_name = '{STATISTICS_TABLE}'"
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
