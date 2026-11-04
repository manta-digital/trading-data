"""``build_status``: what ``status`` reports, and the helpers coverage shares.

LLD 225 TD9–TD11. Sessions come from the production calendar (one
``sessions_between`` per product); everything else from the tick database
(``status_reads``). Both return frozen dataclasses whose ``to_dict()`` is the
``--json`` payload, so slice 231's API serializes the same object. A calendar
or tick-database outage raises (→ exit 4); there is no degraded output.

``complete`` is unit-level (TD10, ``complete_basis: "units"``); ``coverage``
adds the raw-count proof for a date range.

Slice 302 (D6): the verdicts are per *family*. The tick tier is judged as
before and fills the product's own fields; each other wanted family (the
``ohlcv-1m`` bars) is judged the same way over its want's range and reported
in ``families`` as load-complete only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from typing import Any

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
from manta_trading.data.tick.schema_families import FAMILY_SCHEMAS, SchemaFamily
from manta_trading.data.tick.statistics_status import (
    ContentFinding,
    content_findings,
    statistics_verdicts,
)
from manta_trading.data.tick.status_reads import (
    ContractLine,
    Edge,
    contract_lines,
    dataset_edge,
    shape_of,
)
from manta_trading.data.tick.tick_status import (
    SessionVerdict,
    TickSessionStatus,
    caught_up,
)
from manta_trading.data.tick.tick_status_scope import (
    FamilyWant,
    ProductScope,
    bucket_counts,
    family_scope,
    family_wants,
    overlaps,
    product_scope,
)
from manta_trading.data.tick.universe import TickUniverseEntry

COMPLETE_BASIS = "units"
DEGRADED = DatasetCondition.DEGRADED.value


@dataclass(frozen=True)
class FamilyStatus:
    """One wanted family's verdicts: the same buckets, over its own range."""

    family: str
    schemas: tuple[str, ...]
    wanted_start: date | None
    wanted_end: datetime | None
    sessions_held: int
    buckets: dict[str, int]
    degraded: int
    caught_up: bool | None
    holed_sessions: tuple[date, ...]


@dataclass(frozen=True)
class StatisticsFamilyStatus(FamilyStatus):
    """The statistics family: its load verdicts plus the content findings of the
    load-complete sessions (303 D7). A subclass, so other families' reports
    carry no ``findings`` key."""

    findings: tuple[ContentFinding, ...]


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
    #: One entry per wanted family, the tick tier first (302 D6).
    families: tuple[FamilyStatus, ...] = ()


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


def _judged(scope: ProductScope) -> list[SessionVerdict]:
    """TD9 (226): caught up is judged over the wanted range only; sessions in
    scope because other units touch them neither help nor hurt it."""
    wanted = scope.wanted
    return [
        v
        for session, v in zip(scope.sessions, scope.verdicts, strict=True)
        if wanted is not None and overlaps(session, wanted)
    ]


def _family_status(family: SchemaFamily, scope: ProductScope) -> FamilyStatus:
    verdicts = scope.verdicts
    return FamilyStatus(
        family=family.value,
        schemas=tuple(schema.value for schema in FAMILY_SCHEMAS[family]),
        wanted_start=scope.wanted_start,
        wanted_end=scope.wanted_end,
        sessions_held=sum(v.held for v in verdicts),
        buckets=bucket_counts(verdicts),
        degraded=sum(v.condition == DEGRADED for v in verdicts),
        caught_up=caught_up(_judged(scope), wanted=scope.wanted_start is not None),
        holed_sessions=tuple(
            v.session_date
            for v in verdicts
            if v.status is TickSessionStatus.PROVIDER_HOLE
        ),
    )


async def _statistics_status(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    edge: Edge | None,
    want: FamilyWant,
) -> StatisticsFamilyStatus:
    """The statistics section: sessions judged over their publication window."""
    scope = await family_scope(
        conn,
        calendar_url,
        entry,
        edge,
        want.family,
        want.start,
        want.end,
        statistics_verdicts(calendar_url),
    )
    base = _family_status(want.family, scope)
    findings = await content_findings(conn, entry, scope.sessions, scope.verdicts)
    return StatisticsFamilyStatus(
        **{f.name: getattr(base, f.name) for f in fields(base)}, findings=findings
    )


async def _families(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    edge: Edge | None,
    tier_scope: ProductScope,
) -> tuple[FamilyStatus, ...]:
    """One status per wanted family; the tier reuses the scope already read."""
    statuses: list[FamilyStatus] = []
    for want in family_wants(entry):
        if want.family is SchemaFamily.STATISTICS:
            statuses.append(
                await _statistics_status(conn, calendar_url, entry, edge, want)
            )
            continue
        if want.family is SchemaFamily.TICK_TIER:
            scope = tier_scope
        else:
            scope = await family_scope(
                conn, calendar_url, entry, edge, want.family, want.start, want.end
            )
        statuses.append(_family_status(want.family, scope))
    return tuple(statuses)


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
    wanted_start, wanted_end = scope.wanted_start, scope.wanted_end
    verdicts = scope.verdicts
    judged = _judged(scope)
    lines = await contract_lines(conn, shape_of(entry), SchemaFamily.TICK_TIER)
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
        families=await _families(conn, calendar_url, entry, edge, scope),
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
