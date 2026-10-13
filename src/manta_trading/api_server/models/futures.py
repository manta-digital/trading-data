"""Response models for the futures time series and the status tick block (231).

TD5: every price is exact text from ``price_text`` (``None`` when undefined)
and every nanosecond time is text from ``ts_text``; no field carries epoch
nanoseconds, which exceed 2^53. Row field names are ``TICK_COLUMNS`` and
``BAR_COLUMNS`` in order (pinned by a test). Rows are validated: measured on
75,000 ticks (2026-10-13), validating and dumping took 1.19 s against 1.45 s
for ``model_construct``, whose dump path is slower.
Catalog models live in ``futures_catalog.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from manta_trading.data.tick.get_plan import ContractTarget, GetPlan
from manta_trading.data.tick.line_state import TickLineState, TickUnavailable
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.tick_accounting import TickAccountingLine
from manta_trading.data.tick.tick_format import cell_value
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, TICK_COLUMNS, Row
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


class UnresolvedRecord(BaseModel):
    """A session the roll rule could not answer; it has no rows."""

    session_date: date
    reason: str


class IncompleteRecord(BaseModel):
    """A served session whose data is not complete (its 225 verdict)."""

    session_date: date
    status: TickSessionStatus


class TickRecord(BaseModel):
    """One tick (``/futures/.../ticks``): ``TICK_COLUMNS`` in order. The BBO
    fields are null on trades-tier rows."""

    ts_event: str
    session_date: date
    raw_symbol: str
    instrument_id: int
    price: str | None
    size: int
    side: str
    bid_px: str | None
    ask_px: str | None
    bid_sz: int | None
    ask_sz: int | None
    bid_ct: int | None
    ask_ct: int | None
    sequence: int


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


def _envelope(plan: GetPlan) -> dict[str, Any]:
    """The plan's facts, shared by both time-series responses."""
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


class FuturesTicksResponse(BaseModel):
    """``GET /api/v1/futures/{contracts/{symbol} | products/{product}}/ticks``.

    ``end`` is exclusive (session dates). ``from``/``until`` echo the instant
    bounds, half-open, null when not given.
    """

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    target: FuturesTarget
    start: date | None
    end: date | None
    # ``from`` is a Python keyword: the field is ``from_``, the wire name its alias.
    from_: datetime | None = Field(alias="from")
    until: datetime | None
    definitions_as_of: date | None
    segments: list[SegmentRecord]
    unresolved: list[UnresolvedRecord]
    incomplete: list[IncompleteRecord]
    count: int
    ticks: list[TickRecord]

    @classmethod
    def from_plan(
        cls,
        plan: GetPlan,
        rows: Sequence[Row],
        bounds: tuple[datetime | None, datetime | None],
    ) -> FuturesTicksResponse:
        # Through the alias, as ``_pass_line`` does: ``from`` is a keyword.
        return cls(
            **_envelope(plan)
            | {
                "from": bounds[0],
                "until": bounds[1],
                "count": len(rows),
                "ticks": _records(TickRecord, TICK_COLUMNS, rows),
            }
        )


class FuturesBarsResponse(BaseModel):
    """``GET /api/v1/futures/{contracts/{symbol} | products/{product}}/bars``.

    ``end`` is exclusive (session dates); a bar never crosses a session.
    """

    target: FuturesTarget
    granularity: str
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
            **_envelope(plan),
            granularity=granularity,
            count=len(rows),
            bars=_records(FuturesBarRecord, BAR_COLUMNS, rows),
        )


class TickAccountingRecord(BaseModel):
    """One product's tick accounting in ``/api/v1/status``'s ``tick`` block:
    the keys of ``mt data accounting --json``'s ``tick.products``."""

    product: str
    sessions_in_scope: int
    sessions_complete: int
    buckets: dict[str, int]
    records: int


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
                TickAccountingRecord(
                    product=line.product,
                    sessions_in_scope=line.sessions_in_scope,
                    sessions_complete=line.sessions_complete,
                    buckets=line.buckets,
                    records=line.records,
                )
                for line in result
            ],
        )
