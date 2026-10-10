"""Pin the drill's bookkeeping constants to the migrated tick schema (228 TD4).

A table or column added to the tick migration without updating
``drill_228_bookkeeping`` fails here, so the drill can't skip it silently.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import psycopg

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_bookkeeping as bk  # noqa: E402
import drill_228_fingerprint as fp  # noqa: E402

#: The migration runner's ledger table, created in every tracked database.
MIGRATION_LEDGER = "schema_migrations"


def _public(conn: psycopg.Connection[Any], query: str) -> set[tuple[str, ...]]:
    return {tuple(r) for r in conn.execute(query).fetchall()}


def test_bookkeeping_list_plus_trades_is_every_tick_table(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        tables = _public(
            conn,
            "SELECT table_name FROM information_schema.tables"
            " WHERE table_schema = 'public' AND table_type = 'BASE TABLE'",
        )
    found = {t for (t,) in tables} - {MIGRATION_LEDGER}
    assert found == {*bk.BOOKKEEPING_TABLES, fp.TABLE}


def test_allowed_set_names_real_columns_and_covers_reobserved_values(
    migrated_tick_db: str,
) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        columns = _public(
            conn,
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public'",
        )
    assert bk.ALLOWED_DIFFERENCES <= columns
    for table in bk.REOBSERVED_TABLES:
        values = {(t, c) for t, c in columns if t == table} - {
            (table, c) for c in bk.NATURAL_KEYS[table]
        }
        assert values <= bk.ALLOWED_DIFFERENCES, table


def test_every_id_valued_column_is_mapped(migrated_tick_db: str) -> None:
    with psycopg.connect(migrated_tick_db) as conn:
        columns = _public(
            conn,
            "SELECT table_name, column_name FROM information_schema.columns"
            " WHERE table_schema = 'public' AND column_name LIKE '%unit_id'"
            " OR (table_schema = 'public' AND column_name = 'request_id')",
        )
    ids = {(t, c) for t, c in columns if t in bk.BOOKKEEPING_TABLES}
    identities = {(t, c) for t, c in bk.IDENTITY.items()}
    assert ids == set(bk.ID_REFERENCES) | identities
