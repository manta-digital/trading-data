"""Valid default rows for the tick storage tables (slice 222 tests).

Each helper inserts one row built from the enums (no hand-typed enum
strings), applies the caller's overrides, and returns the row's id where the
table generates one. A test that expects a constraint to fire overrides the
one column it is testing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CME_DATASET,
    DeliveryMode,
    SType,
    TickSchema,
    UnitState,
    calendar_for_product,
)

#: A fixed instant for timestamptz columns; tests never depend on its value.
_AT = datetime(2024, 9, 4, tzinfo=UTC)

#: The three file columns a unit must carry from ``downloaded`` onward.
FILE_COLUMNS: dict[str, Any] = {
    "file_path": "GLBX-20240930-TEST/glbx-mdp3-20240903.trades.dbn.zst",
    "file_size_bytes": 1024,
    "file_sha256": "0" * 64,
}


def _insert(
    conn: psycopg.Connection[Any],
    table: str,
    row: dict[str, Any],
    returning: str | None,
) -> Any:
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in row),
        sql.SQL(", ").join(sql.Placeholder() for _ in row),
    )
    if returning is None:
        conn.execute(query, list(row.values()))
        return None
    query = query + sql.SQL(" RETURNING {}").format(sql.Identifier(returning))
    fetched = conn.execute(query, list(row.values())).fetchone()
    assert fetched is not None
    return fetched[0]


def insert_request(conn: psycopg.Connection[Any], **overrides: Any) -> int:
    row: dict[str, Any] = {
        "dataset": CME_DATASET,
        "schema": TickSchema.TRADES.value,
        "symbols": ["ES.FUT"],
        "stype_in": SType.PARENT.value,
        "range_start": date(2024, 9, 3),
        "range_end": date(2024, 9, 4),
        "delivery_mode": DeliveryMode.BATCH_JOB.value,
        "is_adopted": False,
        "estimated_cost_usd": Decimal("0.01"),
        "requested_at": _AT,
    }
    return int(_insert(conn, "tick_request", row | overrides, "request_id"))


def insert_unit(
    conn: psycopg.Connection[Any], request_id: int, **overrides: Any
) -> int:
    row: dict[str, Any] = {
        "request_id": request_id,
        "unit_date": date(2024, 9, 3),
        "state": UnitState.REQUESTED.value,
        "state_changed_at": _AT,
        "fetch_status": FetchStatus.UNKNOWN.value,
        "attempt_count": 0,
    }
    return int(_insert(conn, "tick_archive_unit", row | overrides, "unit_id"))


#: A definition window: 2024-09-01T00:00Z for 90 days, in nanoseconds.
ACTIVATION_NS = 1_725_148_800_000_000_000
WINDOW_NS = 90 * 86_400 * 1_000_000_000


def insert_definition(
    conn: psycopg.Connection[Any], unit_id: int, **overrides: Any
) -> None:
    row: dict[str, Any] = {
        "instrument_id": 42_035_063,
        "activation_ns": ACTIVATION_NS,
        "expiration_ns": ACTIVATION_NS + WINDOW_NS,
        "raw_symbol": "ESZ4",
        "asset": "ES",
        "ts_recv_ns": ACTIVATION_NS,
        "unit_id": unit_id,
    }
    _insert(conn, "tick_definition", row | overrides, None)


#: A ``tbbo``-tier trade: every column set, BBO included.
TBBO_TRADE: dict[str, Any] = {
    "ts_event": ACTIVATION_NS,
    "ts_recv": ACTIVATION_NS + 1,
    "instrument_id": 42_035_063,
    "publisher_id": 1,
    "sequence": 1,
    "price": 5_600_250_000_000,
    "size": 1,
    "action": "T",
    "side": "A",
    "flags": 0,
    "depth": 0,
    "ts_in_delta": 0,
    "bid_px_00": 5_600_000_000_000,
    "ask_px_00": 5_600_250_000_000,
    "bid_sz_00": 3,
    "ask_sz_00": 4,
    "bid_ct_00": 1,
    "ask_ct_00": 2,
    "sequence_ordinal": 0,
}


def insert_trade(conn: psycopg.Connection[Any], unit_id: int, **overrides: Any) -> None:
    _insert(conn, "tick_trade", TBBO_TRADE | {"unit_id": unit_id} | overrides, None)


def insert_ledger_row(
    conn: psycopg.Connection[Any], unit_id: int, **overrides: Any
) -> None:
    row: dict[str, Any] = {
        "unit_id": unit_id,
        "instrument_id": 42_035_063,
        "calendar_id": calendar_for_product("ES"),
        "session_date": date(2024, 9, 3),
        "record_count": 2,
        "volume": 3,
        "first_event_ns": ACTIVATION_NS,
        "last_event_ns": ACTIVATION_NS + 1,
    }
    _insert(conn, "tick_ingest_ledger", row | overrides, None)
