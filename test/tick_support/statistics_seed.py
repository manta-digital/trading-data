"""Seed manifest units, definitions and ledger rows for the statistics verdict
and volume-check tests (slice 303): the reads under test touch only these."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import psycopg
from tick_support.rows import (
    ACTIVATION_NS,
    FILE_COLUMNS,
    insert_definition,
    insert_request,
    insert_unit,
)

from manta_trading.data.tick.constants import TickSchema, UnitState

#: A definition window long enough to hold every seeded session.
LISTED_FOR_NS = 200 * 86_400 * 1_000_000_000


def seed_unit(
    conn: psycopg.Connection[Any],
    schema: TickSchema,
    day: date,
    *,
    symbols: list[str] | None = None,
) -> int:
    """One ingested unit of ``schema`` for ``day``; returns its id."""
    extra = {} if symbols is None else {"symbols": symbols}
    request = insert_request(
        conn,
        schema=schema.value,
        range_start=day,
        range_end=day + timedelta(days=1),
        **extra,
    )
    return insert_unit(
        conn,
        request,
        unit_date=day,
        state=UnitState.INGESTED.value,
        **FILE_COLUMNS,
    )


def seed_units(
    conn: psycopg.Connection[Any], schema: TickSchema, days: list[date]
) -> list[int]:
    return [seed_unit(conn, schema, day) for day in days]


def seed_outright(
    conn: psycopg.Connection[Any],
    unit: int,
    instrument: int,
    *,
    expiration_ns: int | None = None,
    instrument_class: str = "F",
) -> None:
    """A definition of ``instrument`` listed from before the seeded sessions."""
    insert_definition(
        conn,
        unit,
        instrument_id=instrument,
        raw_symbol=f"ES{instrument}",
        instrument_class=instrument_class,
        activation_ns=ACTIVATION_NS,
        expiration_ns=expiration_ns or ACTIVATION_NS + LISTED_FOR_NS,
    )
