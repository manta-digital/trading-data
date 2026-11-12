"""Response models for the futures bars, the shared plan envelope and the
status tick block (231).

TD5: every bar price is exact text from ``price_text`` (``None`` when
undefined) and every bar time is text from ``ts_text``; no field carries epoch
nanoseconds, which exceed 2^53. Bar field names are ``BAR_COLUMNS`` in order
(pinned by a test). Rows are validated: measured on 75,000 ticks (2026-10-13),
validating and dumping took 1.19 s against 1.45 s for ``model_construct``,
whose dump path is slower. The columnar ticks live in ``futures_ticks.py``
(308), tick bars in ``futures_tick_bars.py``, catalog models in
``futures_catalog.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from manta_trading.constants import Granularity
from manta_trading.data.tick.bar_constants import BAR_SOURCE
from manta_trading.data.tick.get_plan import ContractTarget, GetPlan
from manta_trading.data.tick.line_state import TickLineState, TickUnavailable
from manta_trading.data.tick.roll_payload import basis_text
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.tick_accounting import (
    TickAccountingLine,
    lines_by_product,
    tier_line,
)
from manta_trading.data.tick.tick_format import cell_value
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, Row
from manta_trading.data.tick.tick_status import TickSessionStatus


class FuturesTarget(BaseModel):
    """What a futures time-series response read: one contract, or a product
    under a roll rule (``rule`` and ``is_default`` null for a contract)."""

    kind: Literal["contract", "product"]
    product: str | None
    contract: str | None
    instrument_id: int | None
    rule: str | None
    is_default: bool | None

    @classmethod
    def from_plan(cls, plan: GetPlan) -> FuturesTarget:
        target = plan.target
        if isinstance(target, ContractTarget):
            return cls(
                kind="contract",
                product=None,
                contract=target.contract,
                instrument_id=target.instrument_id,
                rule=None,
                is_default=None,
            )
        return cls(
            kind="product",
            product=target.product,
            contract=None,
            instrument_id=None,
            rule=str(target.rule),
            is_default=target.rule == DEFAULT_ROLL_RULE,
        )


class SegmentRecord(BaseModel):
    """A run of consecutive served sessions from one contract (time series)."""

    first_session: date
    last_session: date
    raw_symbol: str
    instrument_id: int
    #: The tier the roll rule decided this run from: ``tick_tier``, ``bar_1m`` or
    #: ``statistics``; null for one named contract (no rule chose it).
    basis: str | None


class UnresolvedRecord(BaseModel):
    """A session the roll rule could not answer; it has no rows."""

    session_date: date
    reason: str


class IncompleteRecord(BaseModel):
    """A served session whose data is not complete (its 225 verdict)."""

    session_date: date
    status: TickSessionStatus


class FuturesBarRecord(BaseModel):
    """One session-bounded bar (``/futures/.../bars``): ``BAR_COLUMNS`` in
    order. ``time`` is the bucket start; ``1d`` is the whole session."""

    time: str
    session_date: date
    raw_symbol: str
    instrument_id: int
    open: str | None
    high: str | None
    low: str | None
    close: str | None
    volume: int


def plan_envelope(plan: GetPlan) -> dict[str, Any]:
    """The plan's facts, shared by every futures time-series response."""
    return {
        "target": FuturesTarget.from_plan(plan),
        "start": plan.start,
        "end": plan.end,
        "definitions_as_of": plan.definitions_as_of,
        "segments": [
            SegmentRecord(
                first_session=s.first_session,
                last_session=s.last_session,
                raw_symbol=s.raw_symbol,
                instrument_id=s.instrument_id,
                basis=basis_text(s.basis),
            )
            for s in plan.display
        ],
        "unresolved": [
            UnresolvedRecord(session_date=u.session_date, reason=u.reason)
            for u in plan.unresolved
        ],
        "incomplete": [
            IncompleteRecord(session_date=i.session_date, status=i.status)
            for i in plan.incomplete
        ],
    }


def _records[M: BaseModel](
    model: type[M], columns: Sequence[str], rows: Sequence[Row]
) -> list[M]:
    return [
        model.model_validate(
            {
                name: cell_value(name, value)
                for name, value in zip(columns, row, strict=True)
            }
        )
        for row in rows
    ]


class FuturesBarsResponse(BaseModel):
    """``GET /api/v1/futures/{contracts/{symbol} | products/{product}}/bars``.

    ``end`` is exclusive (session dates); a bar never crosses a session.
    ``source`` names the provider schema the bars came from (306 D2).
    """

    target: FuturesTarget
    granularity: str
    source: str
    start: date | None
    end: date | None
    definitions_as_of: date | None
    segments: list[SegmentRecord]
    unresolved: list[UnresolvedRecord]
    incomplete: list[IncompleteRecord]
    count: int
    bars: list[FuturesBarRecord]

    @classmethod
    def from_plan(
        cls, plan: GetPlan, granularity: str, rows: Sequence[Row]
    ) -> FuturesBarsResponse:
        return cls(
            **plan_envelope(plan),
            granularity=granularity,
            source=BAR_SOURCE[Granularity(granularity)].value,
            count=len(rows),
            bars=_records(FuturesBarRecord, BAR_COLUMNS, rows),
        )


class FamilyAccountingRecord(BaseModel):
    """One wanted family of a product in ``/api/v1/status``'s ``tick`` block
    (305 D8): the figures of ``mt data accounting --json``'s ``families``,
    without the cross-tier counts (those stay in the CLI, health and status)."""

    family: str
    schema_name: str = Field(alias="schema")
    sessions_in_scope: int
    sessions_complete: int
    buckets: dict[str, int]
    records: int
    caught_up: bool | None = Field(
        description=(
            "Every session judged up to ``source_edge`` is complete, or a hole the"
            " provider declared. ``null`` means no dataset edge is known or no"
            " range is wanted: never read it as caught up."
        )
    )
    source_edge: datetime | None = Field(
        description="The last instant the provider can have published this family."
    )
    load_complete_only: bool = Field(
        description=(
            "True when nothing checks this family's traded volume against CME's"
            " cleared volume: it is judged on units loaded only."
        )
    )

    model_config = ConfigDict(populate_by_name=True)


class TickAccountingRecord(BaseModel):
    """One product's tick accounting in ``/api/v1/status``'s ``tick`` block:
    the keys of ``mt data accounting --json``'s ``tick.products``. The top-level
    figures are the tick tier's (231); ``families`` lists every wanted family."""

    product: str
    sessions_in_scope: int
    sessions_complete: int
    buckets: dict[str, int]
    records: int
    families: list[FamilyAccountingRecord] = Field(default_factory=list)


class TickBlock(BaseModel):
    """``/api/v1/status``'s ``tick`` block (231 TD7): always present; when the
    tick tier cannot be read, ``state`` says why and ``products`` is empty."""

    state: TickLineState
    detail: str | None
    products: list[TickAccountingRecord]

    @classmethod
    def from_result(
        cls, result: Sequence[TickAccountingLine] | TickUnavailable
    ) -> TickBlock:
        if isinstance(result, TickUnavailable):
            return cls(state=result.state, detail=result.detail, products=[])
        return cls(
            state=TickLineState.OK,
            detail=None,
            products=[
                _product_record(product, lines)
                for product, lines in lines_by_product(result)
            ],
        )


def _product_record(
    product: str, lines: list[TickAccountingLine]
) -> TickAccountingRecord:
    tier = tier_line(product, lines)
    return TickAccountingRecord(
        product=product,
        sessions_in_scope=tier.sessions_in_scope,
        sessions_complete=tier.sessions_complete,
        buckets=tier.buckets,
        records=tier.records,
        families=[
            FamilyAccountingRecord.model_validate(_family_fields(line))
            for line in lines
            if line.schema is not None  # a tick tier nothing wants is no family
        ],
    )


def _family_fields(line: TickAccountingLine) -> dict[str, Any]:
    return {
        "family": line.family,
        "schema": line.schema,
        "sessions_in_scope": line.sessions_in_scope,
        "sessions_complete": line.sessions_complete,
        "buckets": line.buckets,
        "records": line.records,
        "caught_up": line.caught_up,
        "source_edge": line.source_edge,
        "load_complete_only": line.load_complete_only,
    }
