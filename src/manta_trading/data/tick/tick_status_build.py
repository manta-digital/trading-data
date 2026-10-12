"""``build_status``: what ``status`` reports, and the helpers coverage shares.

LLD 225 TD9–TD11. Sessions come from the production calendar (one
``sessions_between`` per product); everything else from the tick database
(``status_reads``). Both return frozen dataclasses whose ``to_dict()`` is the
``--json`` payload, so slice 231's API serializes the same object. A calendar
or tick-database outage raises (→ exit 4); there is no degraded output.

``complete`` is unit-level (TD10, ``complete_basis: "units"``); ``coverage``
adds the raw-count proof for a date range.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.active_contract import latest_active
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SPREAD_CLASS,
    DatasetCondition,
    calendar_for_product,
)
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.roll_payload import active_line_dict
from manta_trading.data.tick.roll_rules import STATUS_ROLL_RULES
from manta_trading.data.tick.roll_types import ActiveLine
from manta_trading.data.tick.session_days import days_touched, utc_midnight
from manta_trading.data.tick.status_reads import (
    ContractLine,
    Edge,
    calendar_sessions,
    contract_lines,
    dataset_edge,
    session_verdicts,
    shape_of,
    shape_units,
)
from manta_trading.data.tick.tick_status import (
    SessionVerdict,
    TickSessionStatus,
    caught_up,
)
from manta_trading.data.tick.universe import TickUniverseEntry

COMPLETE_BASIS = "units"
DEGRADED = DatasetCondition.DEGRADED.value


@dataclass(frozen=True)
class ProductStatus:
    product: str
    calendar_id: str
    dataset: str
    symbols: tuple[str, ...]
    stype_in: str
    tier: str | None
    wanted_start: date | None
    wanted_end: datetime | None
    edge_available_end: datetime | None
    edge_observed_at: datetime | None
    edge_age_seconds: float | None
    sessions_held: int
    buckets: dict[str, int]
    degraded: int
    caught_up: bool | None
    holed_sessions: tuple[date, ...]
    contracts: tuple[ContractLine, ...]
    spreads_hidden: int
    #: One line per ``STATUS_ROLL_RULES`` rule (229); its own snapshot.
    rolls: tuple[ActiveLine, ...]


@dataclass(frozen=True)
class TickStatus:
    products: tuple[ProductStatus, ...]
    complete_basis: str = COMPLETE_BASIS

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = jsonable(asdict(self))
        for product, out in zip(self.products, body["products"], strict=True):
            out["rolls"] = [active_line_dict(line) for line in product.rolls]
        return body


def jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _wanted_end(entry: TickUniverseEntry, edge: Edge | None) -> datetime | None:
    """``min(end, the dataset's available end)``; ``None`` when neither is known."""
    ends = [] if entry.end is None else [utc_midnight(entry.end)]
    ends += [] if edge is None else [edge.available_end]
    return min(ends) if ends else None


def overlaps(session: Session, wanted: tuple[datetime, datetime]) -> bool:
    """The session meets the wanted UTC window ``[start, end)``."""
    return session.close_utc > wanted[0] and session.open_utc < wanted[1]


async def _scope(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    wanted: tuple[datetime, datetime] | None,
) -> list[Session]:
    """Sessions in scope (TD9): the wanted range, plus every session a current
    tier unit of the shape touches."""
    held = set(await shape_units(conn, shape_of(entry)))
    bounds = [utc_midnight(d) for d in held]
    bounds += [utc_midnight(d + timedelta(days=1)) for d in held]
    bounds += list(wanted or ())
    if not bounds:
        return []
    found = await asyncio.to_thread(
        calendar_sessions,
        calendar_url,
        calendar_for_product(entry.product),
        min(bounds),
        max(bounds),
    )

    def in_scope(session: Session) -> bool:
        if held.intersection(days_touched(session)):
            return True
        return wanted is not None and overlaps(session, wanted)

    return [session for session in found if in_scope(session)]


@dataclass(frozen=True)
class ProductScope:
    """The sessions ``status`` judges a product on, and their verdicts (TD9).

    ``sessions`` is the wanted range plus every session a current tier unit
    touches; ``wanted`` is the UTC window, ``None`` for an untiered product.
    """

    wanted_start: date | None
    wanted_end: datetime | None
    wanted: tuple[datetime, datetime] | None
    sessions: list[Session]
    verdicts: list[SessionVerdict]


async def product_scope(
    conn: Conn, calendar_url: str, entry: TickUniverseEntry, edge: Edge | None
) -> ProductScope:
    """The one bucket step: ``status`` and 230's accounting line both call it."""
    wanted_start = entry.start if entry.tier is not None else None
    wanted_end = None if wanted_start is None else _wanted_end(entry, edge)
    wanted = (
        None
        if wanted_start is None or wanted_end is None
        else (utc_midnight(wanted_start), wanted_end)
    )
    sessions = await _scope(conn, calendar_url, entry, wanted)
    verdicts = await session_verdicts(conn, entry, sessions)
    return ProductScope(wanted_start, wanted_end, wanted, sessions, verdicts)


def bucket_counts(verdicts: Sequence[SessionVerdict]) -> dict[str, int]:
    """Sessions per status, every status present (zeros included)."""
    counted = Counter(v.status.value for v in verdicts)
    return {s.value: counted.get(s.value, 0) for s in TickSessionStatus}


async def _product_status(
    conn: Conn,
    urls: tuple[str, str],
    entry: TickUniverseEntry,
    now: datetime,
    all_instruments: bool,
) -> ProductStatus:
    tick_url, calendar_url = urls
    edge = await dataset_edge(conn, CME_DATASET)
    scope = await product_scope(conn, calendar_url, entry, edge)
    wanted_start, wanted_end, wanted = (
        scope.wanted_start,
        scope.wanted_end,
        scope.wanted,
    )
    sessions, verdicts = scope.sessions, scope.verdicts
    # TD9 (226): caught up is judged over the wanted range only; sessions in
    # scope because another tier's units touch them neither help nor hurt it.
    judged = [
        v
        for session, v in zip(sessions, verdicts, strict=True)
        if wanted is not None and overlaps(session, wanted)
    ]
    lines = await contract_lines(conn, shape_of(entry))
    spreads = [c for c in lines if c.instrument_class == SPREAD_CLASS]
    return ProductStatus(
        product=entry.product,
        calendar_id=calendar_for_product(entry.product),
        dataset=CME_DATASET,
        symbols=entry.symbols,
        stype_in=entry.stype_in.value,
        tier=None if entry.tier is None else entry.tier.value,
        wanted_start=wanted_start,
        wanted_end=wanted_end,
        edge_available_end=None if edge is None else edge.available_end,
        edge_observed_at=None if edge is None else edge.observed_at,
        edge_age_seconds=(
            None if edge is None else (now - edge.observed_at).total_seconds()
        ),
        sessions_held=sum(v.held for v in verdicts),
        buckets=bucket_counts(verdicts),
        degraded=sum(v.condition == DEGRADED for v in verdicts),
        caught_up=caught_up(judged, wanted=wanted_start is not None),
        holed_sessions=tuple(
            v.session_date
            for v in verdicts
            if v.status is TickSessionStatus.PROVIDER_HOLE
        ),
        contracts=tuple(
            lines if all_instruments else [c for c in lines if c not in spreads]
        ),
        spreads_hidden=0 if all_instruments else len(spreads),
        # After status's own reads, on the roll path's own connections (TD9).
        rolls=await latest_active(tick_url, calendar_url, entry, STATUS_ROLL_RULES),
    )


async def build_status(
    tick_conn: Conn,
    calendar_url: str,
    universe: Sequence[TickUniverseEntry],
    now: datetime,
    *,
    tick_url: str,
    all_instruments: bool = False,
) -> TickStatus:
    """``tick_url`` is for the roll lines only: ``latest_active`` opens its own
    bounded connection and never reuses ``tick_conn`` (229 TD9)."""
    urls = (tick_url, calendar_url)
    return TickStatus(
        tuple(
            [
                await _product_status(tick_conn, urls, e, now, all_instruments)
                for e in universe
            ]
        )
    )
