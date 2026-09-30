"""Compare-and-set unit transitions as statements, and their two executors (225).

LLD 225 Technical Decision 3. A transition is built once as a
:class:`TransitionStatement` (``manifest_repo.py`` holds the builders; the
ingest-only ``mark_superseded_statement`` is here) and run by one of two
executors:

- :func:`execute_transition`, on the run's async connection, in its own
  transaction (every 223/224 caller);
- :func:`execute_transition_sync`, on an ingest worker's sync cursor, inside
  the caller's open transaction (``mark_ingested``, ``mark_superseded``).

Either raises :class:`ManifestTransitionError` when the ``UPDATE`` matches
no row, so the state rule is written once and enforced the same way on both
paths (LLD 224 Technical Decision 2).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, LiteralString

import psycopg

from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn


class ManifestTransitionError(Exception):
    """A compare-and-set update matched no row: the unit is not where expected."""

    def __init__(self, unit_id: int, expected: str) -> None:
        self.unit_id = unit_id
        super().__init__(f"unit {unit_id} is not {expected}; manifest unchanged")


@dataclass(frozen=True)
class TransitionStatement:
    """One compare-and-set ``UPDATE`` of one unit, ready for an executor."""

    unit_id: int
    sql: LiteralString
    params: tuple[object, ...]
    expected: str


def transition_statement(
    unit_id: int,
    set_sql: LiteralString,
    where_sql: LiteralString,
    params: tuple[object, ...],
    expected: str,
) -> TransitionStatement:
    """``UPDATE tick_archive_unit SET <set> WHERE <where> AND unit_id = <id>``.

    ``params`` fill the ``SET`` then the ``WHERE`` placeholders; ``expected``
    names the from-state for the error.
    """
    return TransitionStatement(
        unit_id=unit_id,
        sql=(
            f"UPDATE tick_archive_unit SET {set_sql} WHERE {where_sql} AND unit_id = %s"
        ),
        params=(*params, unit_id),
        expected=expected,
    )


def mark_superseded_statement(unit_id: int, by_unit_id: int) -> TransitionStatement:
    """A current unit replaced by ``by_unit_id`` (LLD 225 TD5); state untouched."""
    return transition_statement(
        unit_id,
        "superseded_by_unit_id = %s",
        COVERAGE_PREDICATE,
        (by_unit_id,),
        "current (not superseded, not reopened)",
    )


async def execute_transition(conn: Conn, statement: TransitionStatement) -> None:
    """Run ``statement`` in its own transaction; zero rows raises."""
    async with conn.transaction():
        cursor = await conn.execute(statement.sql, statement.params)
        if cursor.rowcount != 1:
            raise ManifestTransitionError(statement.unit_id, statement.expected)


def execute_transition_sync(
    cur: psycopg.Cursor[Any], statement: TransitionStatement
) -> None:
    """Run ``statement`` in the caller's open transaction; zero rows raises.

    The caller's transaction rolls back on the raise, taking every earlier
    write of the unit with it.
    """
    cur.execute(statement.sql, statement.params)
    if cur.rowcount != 1:
        raise ManifestTransitionError(statement.unit_id, statement.expected)
