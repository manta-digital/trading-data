"""Tick storage constraints on a migrated tick database (slice 222).

Functional Requirements 3, 4, 5 and 7; the manifest's FR6 cases are in
``test_tick_manifest_constraints.py``. Every rule the server enforces is
exercised by one row that passes and one that is rejected. Rows come from
``tick_support.rows``; each test overrides only the column it tests.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from psycopg import errors
from tick_support.rows import (
    ACTIVATION_NS,
    FILE_COLUMNS,
    WINDOW_NS,
    insert_definition,
    insert_ledger_row,
    insert_request,
    insert_trade,
    insert_unit,
)

from manta_trading.data.tick.constants import (
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.storage_columns import TICK_TRADE_BBO_COLUMNS

Conn = psycopg.Connection[Any]


# --------------------------------------------------------------------------
# Definition windows (Functional Requirement 5)
# --------------------------------------------------------------------------


@pytest.fixture
def definition_unit(tick_conn: Conn) -> int:
    request_id = insert_request(tick_conn, schema=TickSchema.DEFINITION.value)
    return insert_unit(tick_conn, request_id)


@pytest.mark.parametrize(
    "activation_ns",
    [ACTIVATION_NS + WINDOW_NS // 2, ACTIVATION_NS + WINDOW_NS],
    ids=["overlapping", "touching-endpoint"],
)
def test_overlapping_windows_are_rejected(
    tick_conn: Conn, definition_unit: int, activation_ns: int
) -> None:
    insert_definition(tick_conn, definition_unit)
    with pytest.raises(errors.ExclusionViolation):
        insert_definition(
            tick_conn,
            definition_unit,
            activation_ns=activation_ns,
            expiration_ns=activation_ns + WINDOW_NS,
        )


def test_disjoint_windows_for_a_reused_id_insert(
    tick_conn: Conn, definition_unit: int
) -> None:
    insert_definition(tick_conn, definition_unit)
    later = ACTIVATION_NS + WINDOW_NS + 1
    insert_definition(
        tick_conn, definition_unit, activation_ns=later, expiration_ns=later + 1
    )


def test_same_window_for_two_ids_inserts(tick_conn: Conn, definition_unit: int) -> None:
    insert_definition(tick_conn, definition_unit)
    insert_definition(tick_conn, definition_unit, instrument_id=1)


def test_expiration_before_activation_is_rejected(
    tick_conn: Conn, definition_unit: int
) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_definition(tick_conn, definition_unit, expiration_ns=ACTIVATION_NS - 1)


@pytest.mark.parametrize("column", ["activation_ns", "expiration_ns"])
def test_window_bounds_are_required(
    tick_conn: Conn, definition_unit: int, column: str
) -> None:
    with pytest.raises(errors.NotNullViolation):
        insert_definition(tick_conn, definition_unit, **{column: None})


def test_definition_names_a_real_unit(tick_conn: Conn) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        insert_definition(tick_conn, 999_999)


# --------------------------------------------------------------------------
# Trade key and BBO rule (Functional Requirements 3, 4; TD6)
# --------------------------------------------------------------------------


@pytest.fixture
def trade_unit(tick_conn: Conn) -> int:
    return insert_unit(tick_conn, insert_request(tick_conn))


def test_identical_fills_differ_only_by_ordinal(
    tick_conn: Conn, trade_unit: int
) -> None:
    insert_trade(tick_conn, trade_unit, sequence_ordinal=0)
    insert_trade(tick_conn, trade_unit, sequence_ordinal=1)
    with pytest.raises(errors.UniqueViolation):
        insert_trade(tick_conn, trade_unit, sequence_ordinal=1)


def test_negative_ordinal_is_rejected(tick_conn: Conn, trade_unit: int) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_trade(tick_conn, trade_unit, sequence_ordinal=-1)


def test_trade_has_no_foreign_key(tick_conn: Conn) -> None:
    rows = tick_conn.execute(
        "SELECT conname FROM pg_constraint"
        " WHERE conrelid = 'tick_trade'::regclass AND contype = 'f'"
    ).fetchall()
    assert rows == []
    insert_trade(tick_conn, 999_999)  # names no unit, and inserts


@pytest.mark.parametrize("column", TICK_TRADE_BBO_COLUMNS)
def test_one_null_bbo_column_is_rejected(
    tick_conn: Conn, trade_unit: int, column: str
) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_trade(tick_conn, trade_unit, **{column: None})


def test_bbo_all_null_or_all_set(tick_conn: Conn, trade_unit: int) -> None:
    insert_trade(tick_conn, trade_unit)
    no_bbo = dict.fromkeys(TICK_TRADE_BBO_COLUMNS)
    insert_trade(tick_conn, trade_unit, sequence=2, **no_bbo)


# --------------------------------------------------------------------------
# Ingest ledger (Functional Requirement 7)
# --------------------------------------------------------------------------


@pytest.fixture
def ingested_unit(tick_conn: Conn) -> int:
    return insert_unit(
        tick_conn,
        insert_request(tick_conn),
        state=UnitState.INGESTED.value,
        **FILE_COLUMNS,
    )


def test_quiet_session_is_a_zero_row_with_no_times(
    tick_conn: Conn, ingested_unit: int
) -> None:
    insert_ledger_row(
        tick_conn,
        ingested_unit,
        record_count=0,
        volume=0,
        first_event_ns=None,
        last_event_ns=None,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"record_count": 0, "volume": 0},
        {"record_count": 0, "volume": 0, "first_event_ns": None},
        {"first_event_ns": None},
        {"last_event_ns": None},
        {"first_event_ns": ACTIVATION_NS + 2},
        {"record_count": -1},
        {"volume": -1},
    ],
    ids=[
        "zero-with-times",
        "zero-with-last-time",
        "records-without-first",
        "records-without-last",
        "first-after-last",
        "negative-count",
        "negative-volume",
    ],
)
def test_ledger_value_checks(
    tick_conn: Conn, ingested_unit: int, overrides: dict[str, Any]
) -> None:
    with pytest.raises(errors.CheckViolation):
        insert_ledger_row(tick_conn, ingested_unit, **overrides)


def test_ledger_names_a_real_unit(tick_conn: Conn) -> None:
    with pytest.raises(errors.ForeignKeyViolation):
        insert_ledger_row(tick_conn, 999_999)


def test_one_ledger_row_per_unit_instrument_session(
    tick_conn: Conn, ingested_unit: int
) -> None:
    insert_ledger_row(tick_conn, ingested_unit)
    with pytest.raises(errors.UniqueViolation):
        insert_ledger_row(tick_conn, ingested_unit)
