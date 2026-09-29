"""``tick_006_availability`` on a migrated tick database (slice 223).

The availability tables accept every ``DatasetCondition`` and nothing else,
and ``reopened_at`` is refused on a unit that holds a file (LLD 224 TD8).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from psycopg import errors
from tick_support.rows import (
    FILE_COLUMNS,
    insert_dataset_edge,
    insert_day_condition,
    insert_request,
    insert_unit,
)

from manta_trading.data.tick.constants import (
    UNIT_STATES_WITH_FILE,
    DatasetCondition,
    UnitState,
)

Conn = psycopg.Connection[Any]

_AT = datetime(2024, 9, 4, tzinfo=UTC)


def _condition(conn: Conn, day: date, condition: str) -> None:
    insert_day_condition(conn, condition_date=day, condition=condition)


def test_every_condition_member_inserts(tick_conn: Conn) -> None:
    for offset, member in enumerate(DatasetCondition):
        _condition(tick_conn, date(2024, 9, 1 + offset), member.value)
    with pytest.raises(errors.CheckViolation):
        _condition(tick_conn, date(2024, 9, 20), "not-a-member")


def test_duplicate_condition_day_is_rejected(tick_conn: Conn) -> None:
    _condition(tick_conn, date(2024, 9, 3), DatasetCondition.AVAILABLE.value)
    with pytest.raises(errors.UniqueViolation):
        _condition(tick_conn, date(2024, 9, 3), DatasetCondition.DEGRADED.value)


def test_dataset_edge_is_one_row_per_dataset(tick_conn: Conn) -> None:
    insert_dataset_edge(tick_conn)
    with pytest.raises(errors.UniqueViolation):
        insert_dataset_edge(tick_conn)


def test_reopened_at_allowed_on_a_unit_without_a_file(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    insert_unit(tick_conn, request_id, state=UnitState.DELIVERED.value, reopened_at=_AT)


@pytest.mark.parametrize(
    "state", [state for state in UnitState if state in UNIT_STATES_WITH_FILE]
)
def test_reopened_at_rejected_on_a_unit_with_a_file(
    tick_conn: Conn, state: UnitState
) -> None:
    request_id = insert_request(tick_conn)
    with pytest.raises(errors.CheckViolation, match="reopened_check"):
        insert_unit(
            tick_conn,
            request_id,
            state=state.value,
            reopened_at=_AT,
            **FILE_COLUMNS,
        )
