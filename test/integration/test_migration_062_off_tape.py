"""Migration 062 adds ``minute_ohlcv.off_tape`` over compressed chunks (slice 171).

The chain stops at 061 so the column is absent while rows land and a chunk is
compressed: production's state when the release applies 062.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_ID = "062_minute_ohlcv_off_tape"
_INDEX = next(i for i, m in enumerate(MINUTE_MIGRATIONS) if m["id"] == _ID)
_START = datetime(2026, 9, 8, 13, 30, tzinfo=UTC)
_CHECKSUM = (
    "SELECT md5(string_agg(concat_ws(',', time, symbol, open, high, low, close,"
    " volume), '|' ORDER BY symbol, time)) FROM minute_ohlcv"
)


@pytest.fixture()
def pre_062_db(ephemeral_db: str) -> Iterator[str]:
    """Chain through 061, 30 bars in one chunk, that chunk compressed."""
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS[:_INDEX])
    with psycopg.connect(ephemeral_db, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close,"
                " volume) VALUES (%s, 'SPY', %s, %s, %s, %s, 100)",
                [
                    (_START + timedelta(minutes=i), 100 + i, 101 + i, 99 + i, 100.5 + i)
                    for i in range(30)
                ],
            )
        conn.execute("SELECT compress_chunk(c) FROM show_chunks('minute_ohlcv') c")
    yield ephemeral_db


def _apply_062(url: str) -> None:
    with ConnectionPool(url, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)


def _column(conn: psycopg.Connection) -> tuple[str, str] | None:
    row = conn.execute(
        "SELECT data_type, is_nullable FROM information_schema.columns"
        " WHERE table_name = 'minute_ohlcv' AND column_name = 'off_tape'"
    ).fetchone()
    return None if row is None else (row[0], row[1])


def test_adds_nullable_smallint_over_compressed_chunk(pre_062_db: str) -> None:
    with psycopg.connect(pre_062_db) as conn:
        assert _column(conn) is None
        compressed = conn.execute(
            "SELECT bool_and(is_compressed) FROM timescaledb_information.chunks"
            " WHERE hypertable_name = 'minute_ohlcv'"
        ).fetchone()
        assert compressed == (True,)
    _apply_062(pre_062_db)
    with psycopg.connect(pre_062_db) as conn:
        assert _column(conn) == ("smallint", "YES")
        nulls = conn.execute(
            "SELECT count(*) FILTER (WHERE off_tape IS NULL), count(*)"
            " FROM minute_ohlcv"
        ).fetchone()
        assert nulls == (30, 30)


def test_idempotent_on_rerun(pre_062_db: str) -> None:
    _apply_062(pre_062_db)
    with psycopg.connect(pre_062_db, autocommit=True) as conn:
        conn.execute(MINUTE_MIGRATIONS[_INDEX]["sql"])
        assert _column(conn) == ("smallint", "YES")


def test_update_on_compressed_chunk_keeps_raw(pre_062_db: str) -> None:
    _apply_062(pre_062_db)
    with psycopg.connect(pre_062_db) as conn:
        before = conn.execute(_CHECKSUM).fetchone()
        conn.execute("UPDATE minute_ohlcv SET off_tape = 2 WHERE symbol = 'SPY'")
        conn.commit()
        assert conn.execute(_CHECKSUM).fetchone() == before
        marks = conn.execute("SELECT DISTINCT off_tape FROM minute_ohlcv").fetchall()
        assert marks == [(2,)]
