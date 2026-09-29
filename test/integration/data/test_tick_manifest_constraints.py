"""Tick manifest constraints on a migrated tick database (slice 222).

Functional Requirement 6: every rule the server enforces is exercised by
one row that passes and one that is rejected. Rows come from
``tick_support.rows``; each test overrides only the column it tests.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

import psycopg
import pytest
from psycopg import errors
from tick_support.rows import (
    FILE_COLUMNS,
    insert_request,
    insert_unit,
)

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    ARCHIVED_SCHEMAS,
    UNIT_STATES_WITH_FILE,
    DeliveryMode,
    SType,
    TickSchema,
    UnitState,
)

Conn = psycopg.Connection[Any]

NOT_A_MEMBER = "not-a-member"
_DAY = date(2024, 9, 3)


# --------------------------------------------------------------------------
# Manifest (Functional Requirement 6)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "members"),
    [
        ("schema", ARCHIVED_SCHEMAS),
        ("stype_in", SType),
        ("delivery_mode", DeliveryMode),
    ],
)
def test_request_enum_checks(
    tick_conn: Conn, column: str, members: Iterable[StrEnum]
) -> None:
    for member in members:
        insert_request(tick_conn, **{column: member.value})
    with pytest.raises(errors.CheckViolation):
        insert_request(tick_conn, **{column: NOT_A_MEMBER})


def test_request_rejects_the_estimate_only_tier(tick_conn: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_request(tick_conn, schema=TickSchema.MBP_1.value)


def test_unit_state_check(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    for offset, state in enumerate(UnitState):
        insert_unit(
            tick_conn,
            request_id,
            unit_date=_DAY + timedelta(days=offset),
            state=state.value,
            **FILE_COLUMNS,
        )
    with pytest.raises(errors.CheckViolation):
        insert_unit(
            tick_conn,
            request_id,
            unit_date=_DAY - timedelta(days=1),
            state=NOT_A_MEMBER,
        )


def test_unit_fetch_status_check(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    for offset, status in enumerate(FetchStatus):
        reason = None if status is FetchStatus.UNKNOWN else "provider said no"
        insert_unit(
            tick_conn,
            request_id,
            unit_date=_DAY + timedelta(days=offset),
            fetch_status=status.value,
            failure_reason=reason,
        )
    with pytest.raises(errors.CheckViolation):
        insert_unit(
            tick_conn,
            request_id,
            unit_date=_DAY - timedelta(days=1),
            fetch_status=NOT_A_MEMBER,
            failure_reason="x",
        )


@pytest.mark.parametrize("state", sorted(UNIT_STATES_WITH_FILE), ids=lambda s: s.value)
def test_file_states_require_the_file(tick_conn: Conn, state: UnitState) -> None:
    request_id = insert_request(tick_conn)
    for missing in FILE_COLUMNS:
        partial = {c: v for c, v in FILE_COLUMNS.items() if c != missing}
        with pytest.raises(errors.CheckViolation):
            insert_unit(tick_conn, request_id, state=state.value, **partial)
    insert_unit(tick_conn, request_id, state=state.value, **FILE_COLUMNS)


def test_delivered_needs_no_file(tick_conn: Conn) -> None:
    insert_unit(tick_conn, insert_request(tick_conn), state=UnitState.DELIVERED.value)


def test_unknown_status_rejects_a_failure_reason(tick_conn: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_unit(
            tick_conn,
            insert_request(tick_conn),
            fetch_status=FetchStatus.UNKNOWN.value,
            failure_reason="left over",
        )


def test_failed_status_requires_a_failure_reason(tick_conn: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_unit(
            tick_conn,
            insert_request(tick_conn),
            fetch_status=FetchStatus.FAILED_RETRYABLE.value,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"symbols": []},
        {"range_end": date(2024, 9, 3)},
        {"range_end": date(2024, 9, 2)},
        {"estimated_cost_usd": Decimal("-0.01")},
        {"actual_cost_usd": Decimal("-0.01")},
    ],
    ids=["empty-symbols", "empty-range", "reversed-range", "estimate", "actual"],
)
def test_request_value_checks(tick_conn: Conn, overrides: dict[str, Any]) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_request(tick_conn, **overrides)


def test_is_adopted_has_no_default(tick_conn: Conn) -> None:
    with pytest.raises(errors.NotNullViolation):
        tick_conn.execute(
            "INSERT INTO tick_request (dataset, schema, symbols, stype_in,"
            " range_start, range_end, delivery_mode, estimated_cost_usd,"
            " requested_at) VALUES ('d', %s, ARRAY['ES.FUT'], %s,"
            " '2024-09-03', '2024-09-04', %s, 0, now())",
            (
                TickSchema.TRADES.value,
                SType.PARENT.value,
                DeliveryMode.BATCH_JOB.value,
            ),
        )


def test_negative_attempt_count_is_rejected(tick_conn: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_unit(tick_conn, insert_request(tick_conn), attempt_count=-1)


def test_one_unit_per_request_day(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    insert_unit(tick_conn, request_id)
    with pytest.raises(errors.UniqueViolation):
        insert_unit(tick_conn, request_id)
    insert_unit(tick_conn, insert_request(tick_conn))  # same day, other request


def test_unit_cannot_supersede_itself(tick_conn: Conn) -> None:
    unit_id = insert_unit(tick_conn, insert_request(tick_conn))
    with pytest.raises(errors.CheckViolation):
        tick_conn.execute(
            "UPDATE tick_archive_unit SET superseded_by_unit_id = unit_id"
            " WHERE unit_id = %s",
            (unit_id,),
        )


def test_a_unit_is_repurchased_once(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    expired = insert_unit(tick_conn, request_id)
    insert_unit(tick_conn, insert_request(tick_conn), repurchase_of_unit_id=expired)
    with pytest.raises(errors.UniqueViolation):
        insert_unit(tick_conn, insert_request(tick_conn), repurchase_of_unit_id=expired)


def test_unit_links_must_name_real_units(tick_conn: Conn) -> None:
    request_id = insert_request(tick_conn)
    with pytest.raises(errors.ForeignKeyViolation):
        insert_unit(tick_conn, request_id, repurchase_of_unit_id=999_999)
    with pytest.raises(errors.ForeignKeyViolation):
        insert_unit(tick_conn, 999_999)
