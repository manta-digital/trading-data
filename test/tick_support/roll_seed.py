"""Seed a migrated tick database with what the roll path reads (slice 229).

One ingested definition unit carrying outright (and one spread) definitions,
one ingested tier unit per UTC day, and ledger rows: a session's rows sit on
the unit of its own date (the day it closes). Every day a session touches has
an ingested unit, so 225's verdict calls it complete. Synchronous psycopg:
fixtures, not the code under test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import psycopg
from tick_support.rows import (
    FILE_COLUMNS,
    insert_definition,
    insert_ledger_row,
    insert_request,
    insert_unit,
)

from manta_trading.data.tick.constants import (
    OUTRIGHT_CLASS,
    SPREAD_CLASS,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.ingest_records import utc_ns

#: A spread's instrument id; it gets ledger rows but is never a candidate.
SPREAD_ID = 999


@dataclass(frozen=True)
class Outright:
    instrument_id: int
    raw_symbol: str
    activation: datetime
    expiration: datetime


def expiring(instrument_id: int, symbol: str, listed: date, expires: date) -> Outright:
    """Listed from ``listed`` 00:00 UTC, expiring ``expires`` 13:30 UTC (ES)."""
    return Outright(
        instrument_id,
        symbol,
        datetime.combine(listed, time(), UTC),
        datetime.combine(expires, time(13, 30), UTC),
    )


@dataclass(frozen=True)
class SeededRoll:
    definition_unit: int
    units: dict[date, int]  # UTC day → tier unit


def _conn(url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(url, autocommit=True)


def seed_definitions(
    url: str, outrights: Iterable[Outright], day: date, *, spread: bool = True
) -> int:
    """An ingested definition unit dated ``day`` holding the outrights and,
    unless ``spread`` is false, a spread."""
    outrights = list(outrights)
    with _conn(url) as conn:
        request = insert_request(conn, schema=TickSchema.DEFINITION.value)
        unit = insert_unit(
            conn, request, unit_date=day, state=UnitState.INGESTED.value, **FILE_COLUMNS
        )
        for o in outrights:
            insert_definition(
                conn,
                unit,
                instrument_id=o.instrument_id,
                raw_symbol=o.raw_symbol,
                activation_ns=utc_ns(o.activation),
                expiration_ns=utc_ns(o.expiration),
                instrument_class=OUTRIGHT_CLASS,
            )
        if not spread:
            return unit
        insert_definition(
            conn,
            unit,
            instrument_id=SPREAD_ID,
            raw_symbol="ESU4-ESZ4",
            activation_ns=utc_ns(outrights[0].activation),
            expiration_ns=utc_ns(outrights[0].expiration),
            instrument_class=SPREAD_CLASS,
        )
    return unit


def seed_tier_days(
    url: str, days: Iterable[date], schema: TickSchema = TickSchema.TRADES
) -> dict[date, int]:
    """An ingested tier unit per UTC day, under one request."""
    days = sorted(days)
    with _conn(url) as conn:
        request = insert_request(
            conn,
            schema=schema.value,
            range_start=days[0],
            range_end=days[-1] + timedelta(days=1),
        )
        return {
            day: insert_unit(
                conn,
                request,
                unit_date=day,
                state=UnitState.INGESTED.value,
                **FILE_COLUMNS,
            )
            for day in days
        }


def seed_ledger(
    url: str, units: Mapping[date, int], volumes: Mapping[tuple[date, int], int]
) -> None:
    """One ledger row per (session, instrument) on the session date's unit."""
    with _conn(url) as conn:
        for (session_date, instrument), volume in volumes.items():
            insert_ledger_row(
                conn,
                units[session_date],
                instrument_id=instrument,
                session_date=session_date,
                record_count=volume,
                volume=volume,
            )


def touched_days(sessions: Iterable[date]) -> set[date]:
    """The UTC days CME sessions dated ``sessions`` touch: D and the day before."""
    return {d for s in sessions for d in (s - timedelta(days=1), s)}
