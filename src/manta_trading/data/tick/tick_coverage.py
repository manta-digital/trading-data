"""``build_coverage``: the raw-count proof for a date range (slice 225, TD10).

For each session dated ``[start, end)``: its status and condition (TD9), the
current units' ledger sum, the raw ``tick_trade`` count (the one scan, bounded
by the range) and the check. Raw rows change after commit only through
supersession (whose ledger is excluded) or by hand, so a mismatch is evidence
of an out-of-band edit or a defect; the CLI exits 3 on one.

Slice 302 (D6): ``family`` selects what is compared. The tick tier is as above.
For the ``BAR_1M`` family the ledger's bar count and volume are compared with
``count(*)`` and ``sum(volume)`` over ``tick_ohlcv_1m``; the result then also
carries the volumes and names its schema. Those keys are absent for the tick
tier, so its output is what it was.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.constants import TickSchema, calendar_for_product
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.raw_reads import raw_bar_totals, raw_counts
from manta_trading.data.tick.schema_families import SCHEMA_FAMILY, SchemaFamily
from manta_trading.data.tick.session_days import utc_midnight
from manta_trading.data.tick.status_reads import (
    calendar_sessions,
    ledger_totals,
    ledger_volumes,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.store_context import TickPreflightError
from manta_trading.data.tick.tick_status import SessionVerdict
from manta_trading.data.tick.tick_status_build import jsonable
from manta_trading.data.tick.universe import TickUniverseEntry

MISMATCH, OK = "mismatch", "ok"


@dataclass(frozen=True)
class Mismatch:
    instrument_id: int
    ledger: int
    raw: int
    ledger_volume: int | None = None  # bars only
    raw_volume: int | None = None  # bars only


@dataclass(frozen=True)
class CoverageSession:
    session_date: date
    condition: str
    status: str
    unit_ids: tuple[int, ...]
    ledger: int
    raw: int
    mismatches: tuple[Mismatch, ...]
    ledger_volume: int | None = None  # bars only
    raw_volume: int | None = None  # bars only

    @property
    def check(self) -> str:
        return MISMATCH if self.mismatches else OK


#: Keys that exist only on a bar family's result: dropped when ``None``.
_BAR_ONLY_KEYS = ("ledger_volume", "raw_volume")


def _without_bar_only(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _without_bar_only(v)
            for k, v in value.items()
            if not (k in _BAR_ONLY_KEYS and v is None)
        }
    if isinstance(value, list):
        return [_without_bar_only(v) for v in value]
    return value


@dataclass(frozen=True)
class TickCoverage:
    product: str
    calendar_id: str
    start: date
    end: date
    sessions: tuple[CoverageSession, ...]
    schema: str | None = None  # a non-tick family's schema; None = the tick tier

    @property
    def mismatched(self) -> bool:
        return any(s.mismatches for s in self.sessions)

    def to_dict(self) -> dict[str, Any]:
        body = jsonable(asdict(self))
        if body.pop("schema") is not None:
            body["schema"] = self.schema
        for session, out in zip(self.sessions, body["sessions"], strict=True):
            out["check"] = session.check
        return _without_bar_only(body) | {"mismatched": self.mismatched}


Totals = Mapping[tuple[int, date], tuple[int, int | None]]
"""Per (instrument, session): count, and volume where the family compares it."""


def _session_row(
    v: SessionVerdict, ledger: Totals, raw: Totals, *, volumes: bool
) -> CoverageSession:
    day = v.session_date
    instruments = sorted({i for i, d in (*ledger, *raw) if d == day})
    empty: tuple[int, int | None] = (0, 0 if volumes else None)
    pairs = [
        (i, ledger.get((i, day), empty), raw.get((i, day), empty)) for i in instruments
    ]
    mismatches = tuple(
        Mismatch(i, a[0], b[0], a[1], b[1]) for i, a, b in pairs if a != b
    )
    return CoverageSession(
        session_date=day,
        condition=v.condition,
        status=v.status.value,
        unit_ids=tuple(sorted({f.unit.unit_id for f in v.days if f.unit})),
        ledger=sum(a[0] for _, a, _ in pairs),
        raw=sum(b[0] for _, _, b in pairs),
        mismatches=mismatches,
        ledger_volume=sum(a[1] or 0 for _, a, _ in pairs) if volumes else None,
        raw_volume=sum(b[1] or 0 for _, _, b in pairs) if volumes else None,
    )


async def _tick_totals(
    conn: Conn,
    entry: TickUniverseEntry,
    sessions: list[Session],
    start: date,
    last: date,
) -> tuple[Totals, Totals]:
    ledger = await ledger_totals(
        conn, shape_of(entry), SchemaFamily.TICK_TIER, start, last
    )
    raw = await raw_counts(conn, entry.product, _windows(sessions))
    return (
        {k: (n, None) for k, n in ledger.items()},
        {k: (n, None) for k, n in raw.items()},
    )


async def _bar_totals(
    conn: Conn,
    entry: TickUniverseEntry,
    sessions: list[Session],
    start: date,
    last: date,
) -> tuple[Totals, Totals]:
    shape, family = shape_of(entry), SchemaFamily.BAR_1M
    counts = await ledger_totals(conn, shape, family, start, last)
    volumes = await ledger_volumes(conn, shape, family, start, last)
    ledger = {key: (n, volumes.get(key, 0)) for key, n in counts.items()}
    raw = await raw_bar_totals(conn, entry.product, _windows(sessions))
    return ledger, raw


def _windows(sessions: list[Session]) -> list[tuple[date, int, int]]:
    return [(s.session_date, utc_ns(s.open_utc), utc_ns(s.close_utc)) for s in sessions]


#: Family -> the reader of its (ledger, raw) totals.
_TOTALS = {SchemaFamily.TICK_TIER: _tick_totals, SchemaFamily.BAR_1M: _bar_totals}


class CoverageSchemaError(TickPreflightError):
    """``--schema`` names a schema the entry does not want (exit 1)."""


def coverage_family(
    entry: TickUniverseEntry, schema: TickSchema | None
) -> tuple[SchemaFamily, TickSchema | None]:
    """The family to compare and the schema to name; ``None`` is today's tier.

    Raises :class:`CoverageSchemaError` for a schema the entry does not want:
    its tier, or a history want of a family with a ledger.
    """
    if schema is None:
        return SchemaFamily.TICK_TIER, None
    family = SCHEMA_FAMILY[schema]
    wanted = schema == entry.tier or any(w.schema == schema for w in entry.history)
    if not wanted or family not in _TOTALS:
        raise CoverageSchemaError(
            f"product {entry.product} does not want {schema.value}"
            f" (tier: {entry.tier}; history: {[w.schema.value for w in entry.history]})"
        )
    return family, None if family is SchemaFamily.TICK_TIER else schema


async def build_coverage(
    tick_conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    start: date,
    end: date,
    schema: TickSchema | None,
) -> TickCoverage:
    """Sessions dated ``[start, end)``: status, ledger sum, raw count, check.

    ``schema`` is ``--schema``: ``None`` compares the tick tier; otherwise the
    family of a schema the entry wants (see :func:`coverage_family`).
    """
    family, named = coverage_family(entry, schema)
    calendar_id = calendar_for_product(entry.product)
    found = await asyncio.to_thread(
        calendar_sessions,
        calendar_url,
        calendar_id,
        utc_midnight(start - timedelta(days=1)),
        utc_midnight(end),
    )
    sessions = [s for s in found if start <= s.session_date < end]
    verdicts = await session_verdicts(tick_conn, entry, sessions, family)
    ledger, raw = await _TOTALS[family](
        tick_conn, entry, sessions, start, end - timedelta(days=1)
    )
    rows = tuple(
        _session_row(v, ledger, raw, volumes=family is not SchemaFamily.TICK_TIER)
        for v in verdicts
    )
    return TickCoverage(
        entry.product,
        calendar_id,
        start,
        end,
        rows,
        None if named is None else named.value,
    )
