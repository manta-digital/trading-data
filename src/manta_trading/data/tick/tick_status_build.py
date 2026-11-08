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
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from manta_trading.data.tick.active_contract import latest_active
from manta_trading.data.tick.constants import (
    CME_DATASET,
    SPREAD_CLASS,
    DatasetCondition,
    calendar_for_product,
)
from manta_trading.data.tick.family_edges import family_source_edge
from manta_trading.data.tick.family_report import FamilyStatus, family_reports
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.roll_payload import active_line_dict
from manta_trading.data.tick.roll_rules import STATUS_ROLL_RULES
from manta_trading.data.tick.roll_types import ActiveLine
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.status_reads import (
    ContractLine,
    contract_lines,
    dataset_edge,
    shape_of,
)
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_scope import (
    bucket_counts,
    product_scope,
    scope_caught_up,
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
    tick_edge = await family_source_edge(
        calendar_url, entry, SchemaFamily.TICK_TIER, entry.end, edge
    )
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
        caught_up=scope_caught_up(scope, tick_edge),
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
        families=await family_reports(
            conn, calendar_url, entry, edge, cross_tier=True, tier_scope=scope
        ),
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
