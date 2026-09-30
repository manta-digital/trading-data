"""Transition statements through the sync executor (slice 225, TD3).

An ingest worker runs ``mark_ingested`` and ``mark_superseded`` inside its own
transaction on a sync connection: the transition commits or rolls back with
the unit's rows, and a zero-row match raises exactly as the async path does.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit

from manta_trading.data.tick.constants import UnitState
from manta_trading.data.tick.manifest_repo import mark_ingested_statement
from manta_trading.data.tick.manifest_transitions import (
    ManifestTransitionError,
    execute_transition_sync,
    mark_superseded_statement,
)

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
SConn = psycopg.Connection[Any]


@pytest.fixture
def conn(migrated_tick_db: str) -> Any:
    with psycopg.connect(migrated_tick_db) as connection:
        yield connection


def _verified_unit(conn: SConn) -> int:
    request = insert_request(conn)
    unit = insert_unit(conn, request, state=UnitState.VERIFIED.value, **FILE_COLUMNS)
    conn.commit()
    return unit


def _row(conn: SConn, unit_id: int) -> tuple[Any, ...]:
    row = conn.execute(
        "SELECT state, decoded_record_count, superseded_by_unit_id"
        " FROM tick_archive_unit WHERE unit_id = %s",
        (unit_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_mark_ingested_commits_with_the_callers_transaction(conn: SConn) -> None:
    unit = _verified_unit(conn)
    with conn.transaction(), conn.cursor() as cur:
        execute_transition_sync(cur, mark_ingested_statement(unit, 7, NOW))
    assert _row(conn, unit) == (UnitState.INGESTED.value, 7, None)


def test_a_rollback_leaves_the_unit_verified(conn: SConn) -> None:
    unit = _verified_unit(conn)

    class Abort(Exception):
        pass

    with pytest.raises(Abort):
        with conn.transaction(), conn.cursor() as cur:
            execute_transition_sync(cur, mark_ingested_statement(unit, 7, NOW))
            raise Abort
    assert _row(conn, unit) == (UnitState.VERIFIED.value, None, None)


def test_zero_matched_rows_raises(conn: SConn) -> None:
    unit = _verified_unit(conn)
    with conn.transaction(), conn.cursor() as cur:
        execute_transition_sync(cur, mark_ingested_statement(unit, 7, NOW))
    with pytest.raises(ManifestTransitionError, match=f"unit {unit} is not verified"):
        with conn.transaction(), conn.cursor() as cur:
            execute_transition_sync(cur, mark_ingested_statement(unit, 7, NOW))


def test_mark_superseded_sets_the_link_once(conn: SConn) -> None:
    lower, higher = _verified_unit(conn), _verified_unit(conn)
    with conn.transaction(), conn.cursor() as cur:
        execute_transition_sync(cur, mark_superseded_statement(lower, higher))
    assert _row(conn, lower) == (UnitState.VERIFIED.value, None, higher)
    with pytest.raises(ManifestTransitionError, match="not superseded"):
        with conn.transaction(), conn.cursor() as cur:
            execute_transition_sync(cur, mark_superseded_statement(lower, higher))
