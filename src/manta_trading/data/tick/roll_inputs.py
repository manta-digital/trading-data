"""The tick-database reads the roll resolver needs (slice 229, TD2, TD3, TD9).

Every read runs on the roll path's own connection, inside its one
``REPEATABLE READ READ ONLY`` transaction (``active_contract``). "Current" is
``SHAPE_WHERE`` (``COVERAGE_PREDICATE``): superseded and reopened units never
count. Outrights are ``OUTRIGHT_CLASS``; spreads are never a roll candidate.
"""

from __future__ import annotations

from datetime import date

from manta_trading.data.tick.constants import (
    OUTRIGHT_CLASS,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE, Conn
from manta_trading.data.tick.roll_types import (
    ContractDefinition,
    RollInputError,
    SessionOpenInterest,
    SessionVolume,
)
from manta_trading.data.tick.schema_families import VOLUME_FAMILIES, SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType
from manta_trading.data.tick.statistics_reads import resolved
from manta_trading.data.tick.status_reads import SHAPE_WHERE, Shape, ledger_units

_IS_OUTRIGHT = (
    "EXISTS (SELECT 1 FROM tick_definition d WHERE d.instrument_id = l.instrument_id"
    " AND d.instrument_class = %(outright)s)"
)


async def outright_definitions(
    conn: Conn, product: str
) -> tuple[ContractDefinition, ...]:
    """The product's outright definitions. None is an error, never empty."""
    cursor = await conn.execute(
        "SELECT instrument_id, raw_symbol, activation_ns, expiration_ns, unit_id"
        " FROM tick_definition WHERE asset = %s AND instrument_class = %s"
        " ORDER BY expiration_ns, instrument_id",
        (product, OUTRIGHT_CLASS),
    )
    rows = await cursor.fetchall()
    if not rows:
        raise RollInputError(
            f"product {product} has no outright definitions (asset = {product},"
            f" instrument_class = {OUTRIGHT_CLASS})"
        )
    unnamed = [row[0] for row in rows if row[1] is None]
    if unnamed:
        raise RollInputError(
            f"product {product}: outright definitions with no raw_symbol: {unnamed}"
        )
    return tuple(ContractDefinition(*row) for row in rows)


async def session_volumes(
    conn: Conn, shape: Shape, family: SchemaFamily, first: date, last: date
) -> tuple[SessionVolume, ...]:
    """``family``'s outright ledger rows dated ``[first, last]`` over current
    units, one per (session, instrument, unit). The ledger is the family's own
    (``FAMILY_LEDGER``), so a tick-tier and a bar session never mix (303 D10)."""
    if family not in VOLUME_FAMILIES:
        raise ValueError(f"family {family} has no traded volume to rank on")
    cursor = await conn.execute(
        "SELECT l.session_date, l.instrument_id, l.volume, l.unit_id"
        f" FROM {ledger_units(family)} WHERE {SHAPE_WHERE} AND {_IS_OUTRIGHT}"
        " AND l.session_date BETWEEN %(first)s AND %(last)s"
        " ORDER BY l.session_date, l.instrument_id, l.unit_id",
        shape.params(family)
        | {"outright": OUTRIGHT_CLASS, "first": first, "last": last},
    )
    return tuple(SessionVolume(*row) for row in await cursor.fetchall())


async def session_open_interest(
    conn: Conn, shape: Shape, first: date, last: date
) -> tuple[SessionOpenInterest, ...]:
    """Resolved open interest per (instrument, session) dated ``[first, last]``
    (303 D5, D9). A record with an undefined value is left out, so it reads as
    missing; the resolver treats a missing value as ``Unresolved``."""
    rows = await resolved(conn, shape, [StatType.OPEN_INTEREST], first, last)
    return tuple(
        SessionOpenInterest(
            row.session_date, row.instrument_id, row.quantity, row.unit_id
        )
        for row in rows
        if row.quantity is not None
    )


async def held_session_bounds(conn: Conn, shape: Shape) -> tuple[date, date] | None:
    """Earliest and latest ledger session over current units; ``None`` if none."""
    cursor = await conn.execute(
        "SELECT min(l.session_date), max(l.session_date)"
        f" FROM {ledger_units(SchemaFamily.TICK_TIER)} WHERE {SHAPE_WHERE}",
        shape.params(SchemaFamily.TICK_TIER),
    )
    row = await cursor.fetchone()
    if row is None or row[0] is None:
        return None
    return row[0], row[1]


async def definitions_as_of(conn: Conn, shape: Shape) -> date:
    """The latest day of a current, ingested definition unit of the shape."""
    cursor = await conn.execute(
        "SELECT max(u.unit_date) FROM tick_archive_unit u"
        " JOIN tick_request r USING (request_id)"
        " WHERE r.dataset = %(dataset)s AND r.symbols = %(symbols)s"
        " AND r.stype_in = %(stype_in)s AND r.schema = %(schema)s"
        f" AND u.state = %(state)s AND {COVERAGE_PREDICATE}",
        shape.params(SchemaFamily.TICK_TIER)
        | {"schema": TickSchema.DEFINITION.value, "state": UnitState.INGESTED.value},
    )
    row = await cursor.fetchone()
    if row is None or row[0] is None:
        raise RollInputError(f"shape {shape.symbols} has no ingested definition unit")
    day: date = row[0]
    return day
