"""Which tier units ingest loads, and why the others wait (slice 225, TD4).

A unit is selected when its request schema is a stored tier, it is
*verified* with an open ``fetch_status``, it is current (``COVERAGE_PREDICATE``),
no current unit of a higher tier holds its shape and day (TD5), and its
companion definition unit (same dataset, symbols, ``stype_in`` and day) is
*ingested*. The last two are **skips**, not failures: the next acquisition
pass or the higher tier's own ingest resolves them.

``--unit-id`` narrows the selection and never overrides it: a named unit that
is not selectable is reported with its reason. Order: ``unit_date``, then
``unit_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, LiteralString

from psycopg.rows import dict_row

from manta_trading.data.quality.fetch_status import OPEN_FETCH_STATUSES
from manta_trading.data.tick.constants import (
    STORED_TIERS,
    TickSchema,
    UnitState,
    tier_rank_sql,
)
from manta_trading.data.tick.manifest_reads import (
    OPEN_STATUS_VALUES,
    UNIT_COLUMNS,
    UNIT_FROM,
    Conn,
    UnitRow,
    unit_row,
)


class SkipKind(StrEnum):
    """Why a unit was not loaded this run; the report's ``skipped`` keys."""

    AWAITING_DEFINITIONS = "awaiting_definitions"
    OUTRANKED = "outranked"
    CHANGED_DURING_INGEST = "changed_during_ingest"
    NOT_SELECTABLE = "not_selectable"


@dataclass(frozen=True)
class Skipped:
    unit_id: int
    kind: SkipKind
    detail: str


@dataclass(frozen=True)
class Selection:
    selected: tuple[UnitRow, ...]
    skipped: tuple[Skipped, ...]


_SHAPE_MATCH: LiteralString = (
    "o.request_id = p.request_id AND p.dataset = r.dataset"
    " AND p.symbols = r.symbols AND p.stype_in = r.stype_in"
    " AND o.unit_date = u.unit_date"
)

#: Column names and the rank expression come from code constants only.
_SELECT = (
    f"SELECT {UNIT_COLUMNS}, u.superseded_by_unit_id, ("
    f" SELECT min(o.unit_id) FROM tick_archive_unit o, tick_request p"
    f" WHERE {_SHAPE_MATCH} AND o.superseded_by_unit_id IS NULL"
    " AND o.reopened_at IS NULL AND p.schema = ANY(%(tiers)s)"
    f" AND {tier_rank_sql('p.schema')} > {tier_rank_sql('r.schema')}"
    ") AS outranked_by, EXISTS ("
    f" SELECT 1 FROM tick_archive_unit o, tick_request p WHERE {_SHAPE_MATCH}"
    " AND p.schema = %(definition)s AND o.state = %(ingested)s"
    " AND o.superseded_by_unit_id IS NULL AND o.reopened_at IS NULL"
    f") AS has_definitions FROM {UNIT_FROM}"
)


def _query(named: bool) -> str:
    where = (
        "u.unit_id = ANY(%(ids)s)"
        if named
        else "r.schema = ANY(%(tiers)s) AND u.state = %(verified)s"
        " AND u.fetch_status = ANY(%(open)s)"
        " AND u.superseded_by_unit_id IS NULL AND u.reopened_at IS NULL"
    )
    return f"{_SELECT} WHERE {where} ORDER BY u.unit_date, u.unit_id"


def _not_selectable(unit: UnitRow, row: dict[str, Any]) -> str | None:
    """Why a named unit fails a selection rule; ``None`` when it passes them."""
    if unit.schema not in STORED_TIERS:
        return f"schema {unit.schema} is not a stored tier"
    if unit.state is not UnitState.VERIFIED:
        return f"state is {unit.state}, not {UnitState.VERIFIED}"
    if unit.fetch_status not in OPEN_FETCH_STATUSES:
        return f"fetch_status is {unit.fetch_status}; reset it first"
    if row["superseded_by_unit_id"] is not None:
        return f"superseded by unit {row['superseded_by_unit_id']}"
    if unit.reopened_at is not None:
        return "reopened"
    return None


def _classify(row: dict[str, Any]) -> UnitRow | Skipped:
    unit = unit_row(row)
    reason = _not_selectable(unit, row)
    if reason is not None:
        return Skipped(unit.unit_id, SkipKind.NOT_SELECTABLE, reason)
    if row["outranked_by"] is not None:
        detail = f"outranked by unit {row['outranked_by']}"
        return Skipped(unit.unit_id, SkipKind.OUTRANKED, detail)
    if not row["has_definitions"]:
        detail = "awaiting definitions"
        return Skipped(unit.unit_id, SkipKind.AWAITING_DEFINITIONS, detail)
    return unit


async def select_units(conn: Conn, unit_ids: Sequence[int] = ()) -> Selection:
    """Every selectable unit, or only the named ones, with the rest's reasons."""
    params = {
        "ids": list(unit_ids),
        "tiers": sorted(tier.value for tier in STORED_TIERS),
        "verified": UnitState.VERIFIED.value,
        "open": OPEN_STATUS_VALUES,
        "definition": TickSchema.DEFINITION.value,
        "ingested": UnitState.INGESTED.value,
    }
    async with conn.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(_query(bool(unit_ids)), params)
        rows = await cursor.fetchall()
    found = {row["unit_id"] for row in rows}
    classified = [_classify(row) for row in rows]
    missing = [
        Skipped(unit_id, SkipKind.NOT_SELECTABLE, "no such unit")
        for unit_id in unit_ids
        if unit_id not in found
    ]
    return Selection(
        selected=tuple(c for c in classified if isinstance(c, UnitRow)),
        skipped=tuple(c for c in classified if isinstance(c, Skipped)) + tuple(missing),
    )
