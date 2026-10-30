"""``tick_008_plan_inclusion`` (slice 300): the marker column and its backfill."""

from __future__ import annotations

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from manta_trading.market.schema.migrations import TRACKS
from manta_trading.market.schema.runner import apply_migrations

MIGRATION_ID = "tick_008_plan_inclusion"

#: A request written before the column existed: it names no ``is_plan_included``.
_OLD_REQUEST = (
    "INSERT INTO tick_request (dataset, schema, symbols, stype_in, range_start,"
    " range_end, delivery_mode, is_adopted, estimated_cost_usd, requested_at)"
    " VALUES ('GLBX.MDP3', 'trades', ARRAY['ES.FUT'], 'parent', '2024-09-03',"
    " '2024-09-04', 'batch_job', TRUE, 0, now())"
)


def _apply(url: str, migrations: list[dict[str, str]]) -> None:
    with ConnectionPool(url, min_size=1, max_size=1) as pool:
        apply_migrations(pool, migrations)


def test_the_column_is_not_null_with_no_default(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        row = conn.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_name = 'tick_request' AND column_name = 'is_plan_included'"
        ).fetchone()
    assert row == ("NO", None)


def test_existing_requests_become_false(ephemeral_tick_db: str) -> None:
    url = ephemeral_tick_db
    ids = [m["id"] for m in TRACKS["tick"]]
    _apply(url, TRACKS["tick"][: ids.index(MIGRATION_ID)])
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_OLD_REQUEST)
    _apply(url, TRACKS["tick"])
    with psycopg.connect(url) as conn:
        rows = conn.execute("SELECT is_plan_included FROM tick_request").fetchall()
    assert rows == [(False,)]


def test_an_insert_that_omits_the_column_fails(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        with pytest.raises(errors.NotNullViolation, match="is_plan_included"):
            conn.execute(_OLD_REQUEST)
