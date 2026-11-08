"""One per-family report, read once and shown by every surface (slice 305, D3).

``status``, accounting, health, overview and the API all take their futures
completeness from :func:`futures_completeness`, which is the only function that
reads the database for them. It returns one :class:`FamilyStatus` per wanted
family: the family's buckets over its own range, ``caught_up`` judged against
its source edge (:mod:`family_edges`), its record total and, for a volume
family, the cross-tier results.

:func:`family_reports` is ``status``'s per-product body moved here (302 D6, 303
D7). Contract lines and roll lines stay in ``status``.

A failure is never caught here: :class:`ReadProgress` records the product,
family and step in flight, so the caller that does catch it (D5 "Failure and
concurrency") can name where it stopped.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from enum import StrEnum
from typing import Final

from manta_trading.data.tick.constants import CME_DATASET, DatasetCondition
from manta_trading.data.tick.cross_tier_result import CrossTierResult
from manta_trading.data.tick.family_edges import family_source_edge
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import (
    FAMILY_SCHEMAS,
    VOLUME_FAMILIES,
    SchemaFamily,
)
from manta_trading.data.tick.statistics_status import (
    ContentFinding,
    content_findings,
    statistics_verdicts,
)
from manta_trading.data.tick.status_reads import (
    Edge,
    dataset_edge,
    ledger_totals,
    session_verdicts,
    shape_of,
)
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.data.tick.tick_status_scope import (
    FamilyWant,
    ProductScope,
    bucket_counts,
    family_scope,
    family_wants,
    scope_caught_up,
)
from manta_trading.data.tick.universe import TickUniverseEntry

#: Heads a family that is judged for load only: it reports units loaded, with no
#: content check against another tier. Every surface prints it from
#: ``FamilyStatus.load_complete_only``, never from its own condition.
LOAD_COMPLETE_ONLY: Final = "load-complete only"
_DEGRADED: Final = DatasetCondition.DEGRADED.value


class ReadStep(StrEnum):
    """The steps :func:`family_reports` takes, in order, for one family."""

    DATASET_EDGE = "dataset edge"
    SCOPE = "scope"
    CONTENT = "content"
    TRADED_VS_CLEARED = "traded_vs_cleared"
    RECORDS = "records"


@dataclass
class ReadProgress:
    """Where a read is: written before each step, read by whoever catches."""

    product: str | None = None
    family: SchemaFamily | None = None
    step: ReadStep | None = None

    def at(
        self, step: ReadStep, product: str | None, family: SchemaFamily | None
    ) -> None:
        self.product, self.family, self.step = product, family, step


def unavailable_with_progress(progress: ReadProgress, unavailable_text: str) -> str:
    """``<product> <family>: <step>: <text>``; accounting, health and the API
    all word a partial failure through this."""
    where = " ".join(p for p in (progress.product, progress.family) if p)
    parts = [where, progress.step.value if progress.step else "", unavailable_text]
    return ": ".join(p for p in parts if p)


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
    #: The last instant the provider can have published this family (D2).
    source_edge: datetime | None
    #: Ledger ``record_count`` over the family's current units.
    records: int
    #: Cross-tier results; empty unless the read asked for them (D4).
    cross_tier: tuple[CrossTierResult, ...]
    #: True when the family has a traded volume that no statistics want checks.
    load_complete_only: bool


@dataclass(frozen=True)
class StatisticsFamilyStatus(FamilyStatus):
    """The statistics family: its load verdicts plus the content findings of the
    load-complete sessions (303 D7). A subclass, so other families' reports
    carry no ``findings`` key."""

    findings: tuple[ContentFinding, ...]


def _mark(
    progress: ReadProgress | None,
    step: ReadStep,
    product: str | None,
    family: SchemaFamily | None,
) -> None:
    if progress is not None:
        progress.at(step, product, family)


async def _family_report(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    edge: Edge | None,
    want: FamilyWant,
    scope: ProductScope | None,
    progress: ReadProgress | None,
    wants_statistics: bool,
) -> FamilyStatus:
    family, product = want.family, entry.product
    statistics = family is SchemaFamily.STATISTICS
    _mark(progress, ReadStep.SCOPE, product, family)
    source = await family_source_edge(calendar_url, entry, family, want.end, edge)
    if scope is None:
        scope = await family_scope(
            conn,
            calendar_url,
            entry,
            edge,
            family,
            want.start,
            want.end,
            statistics_verdicts(calendar_url) if statistics else session_verdicts,
        )
    verdicts = scope.verdicts
    _mark(progress, ReadStep.RECORDS, product, family)
    totals = await ledger_totals(conn, shape_of(entry), family, date.min, date.max)
    base = FamilyStatus(
        family=family.value,
        schemas=tuple(schema.value for schema in FAMILY_SCHEMAS[family]),
        wanted_start=scope.wanted_start,
        wanted_end=scope.wanted_end,
        sessions_held=sum(v.held for v in verdicts),
        buckets=bucket_counts(verdicts),
        degraded=sum(v.condition == _DEGRADED for v in verdicts),
        caught_up=scope_caught_up(scope, source),
        holed_sessions=tuple(
            v.session_date
            for v in verdicts
            if v.status is TickSessionStatus.PROVIDER_HOLE
        ),
        source_edge=source,
        records=sum(totals.values()),
        cross_tier=(),
        load_complete_only=family in VOLUME_FAMILIES and not wants_statistics,
    )
    if not statistics:
        return base
    _mark(progress, ReadStep.CONTENT, product, family)
    findings = await content_findings(conn, entry, scope.sessions, verdicts)
    return StatisticsFamilyStatus(
        **{f.name: getattr(base, f.name) for f in fields(base)}, findings=findings
    )


async def family_reports(
    conn: Conn,
    calendar_url: str,
    entry: TickUniverseEntry,
    edge: Edge | None,
    *,
    cross_tier: bool,
    progress: ReadProgress | None = None,
    tier_scope: ProductScope | None = None,
) -> tuple[FamilyStatus, ...]:
    """One report per wanted family, in ``family_wants`` order.

    ``tier_scope`` is a tick-tier scope the caller already read (``status``
    does), reused rather than read again. Writes the product, family and step
    to ``progress`` before each step and catches nothing.
    """
    wants = family_wants(entry)
    wants_statistics = any(w.family is SchemaFamily.STATISTICS for w in wants)
    return tuple(
        [
            await _family_report(
                conn,
                calendar_url,
                entry,
                edge,
                want,
                tier_scope if want.family is SchemaFamily.TICK_TIER else None,
                progress,
                wants_statistics,
            )
            for want in wants
        ]
    )


async def futures_completeness(
    conn: Conn,
    calendar_url: str,
    universe: Sequence[TickUniverseEntry],
    progress: ReadProgress,
    *,
    cross_tier: bool,
) -> dict[str, tuple[FamilyStatus, ...]]:
    """The only read behind the futures surfaces: the dataset edge once, then
    :func:`family_reports` per product, keyed by product. Catches nothing."""
    _mark(progress, ReadStep.DATASET_EDGE, None, None)
    edge = await dataset_edge(conn, CME_DATASET)
    return {
        entry.product: await family_reports(
            conn,
            calendar_url,
            entry,
            edge,
            cross_tier=cross_tier,
            progress=progress,
        )
        for entry in universe
    }
