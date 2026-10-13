"""Response models for the futures catalog routes (slice 231, TD5).

Each is built from 229's dataclasses (or ``catalog_reads``), never by
re-keying ``roll_payload``'s dictionaries. A test pins every model's field set
to the matching dictionary's keys at every level, so ``mt data tick active
--json`` and the API cannot drift. Expirations serialize as
``datetime.isoformat()``, the CLI's spelling, for the same reason.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from pydantic import BaseModel, PlainSerializer

from manta_trading.api_server.models.futures import UnresolvedRecord
from manta_trading.data.tick.catalog_reads import CatalogContract
from manta_trading.data.tick.constants import calendar_for_product
from manta_trading.data.tick.roll_rules import DEFAULT_ROLL_RULE
from manta_trading.data.tick.roll_types import (
    ActiveContract,
    ActiveLine,
    RollSchedule,
    Segment,
    Unresolved,
    utc_from_ns,
)
from manta_trading.data.tick.tick_format import price_text

#: A UTC instant spelled as ``roll_payload`` spells it (``+00:00``).
UtcIso = Annotated[
    datetime, PlainSerializer(lambda d: d.astimezone(UTC).isoformat(), return_type=str)
]


class ContractAnswerRecord(BaseModel):
    """One session's active contract (``schedule.answers``, ``next_roll``): the
    keys of ``roll_payload.contract_dict``."""

    session_date: date
    instrument_id: int
    raw_symbol: str
    expiration: UtcIso
    basis_session: date
    definitions_as_of: date
    definition_unit_id: int
    basis_unit_ids: list[int]

    @classmethod
    def from_answer(cls, answer: ActiveContract) -> ContractAnswerRecord:
        return cls(
            session_date=answer.session_date,
            instrument_id=answer.instrument_id,
            raw_symbol=answer.raw_symbol,
            expiration=answer.expiration,
            basis_session=answer.basis_session,
            definitions_as_of=answer.definitions_as_of,
            definition_unit_id=answer.definition_unit_id,
            basis_unit_ids=list(answer.basis_unit_ids),
        )


def _unresolved(answer: Unresolved) -> UnresolvedRecord:
    return UnresolvedRecord(session_date=answer.session_date, reason=answer.reason)


class ActiveLineRecord(BaseModel):
    """One rule's answer for the latest complete session (``/futures/products``):
    the keys of ``roll_payload.active_line_dict``. Contract fields are null
    without a contract; ``error`` is set when the rule could not be answered."""

    product: str
    rule: str
    is_default: bool
    session_date: date | None
    instrument_id: int | None
    raw_symbol: str | None
    expiration: UtcIso | None
    basis_session: date | None
    definitions_as_of: date | None
    definition_unit_id: int | None
    basis_unit_ids: list[int] | None
    unresolved: list[UnresolvedRecord]
    next_roll: ContractAnswerRecord | None
    known_through: date | None
    roll_no_later_than: date | None
    reason: str | None
    error: str | None

    @classmethod
    def from_line(cls, line: ActiveLine) -> ActiveLineRecord:
        answer = line.answer
        contract = (
            ContractAnswerRecord.from_answer(answer).model_dump()
            if isinstance(answer, ActiveContract)
            else dict.fromkeys(ContractAnswerRecord.model_fields)
            | {"session_date": line.session_date}
        )
        roll = line.next_roll
        return cls(
            product=line.product,
            rule=str(line.rule),
            is_default=line.rule == DEFAULT_ROLL_RULE,
            **contract,
            unresolved=[_unresolved(answer)] if isinstance(answer, Unresolved) else [],
            next_roll=None
            if roll is None
            else ContractAnswerRecord.from_answer(roll.contract),
            known_through=line.known_through,
            roll_no_later_than=line.roll_no_later_than,
            reason=line.reason,
            error=line.error,
        )


class ProductRecord(BaseModel):
    """One configured product, its calendar, and one active line per status
    rule (``/futures/products``)."""

    product: str
    calendar: str
    active: list[ActiveLineRecord]

    @classmethod
    def from_lines(cls, product: str, lines: tuple[ActiveLine, ...]) -> ProductRecord:
        return cls(
            product=product,
            calendar=calendar_for_product(product),
            active=[ActiveLineRecord.from_line(line) for line in lines],
        )


class FuturesProductsResponse(BaseModel):
    """``GET /api/v1/futures/products``: every product in the tick universe."""

    products: list[ProductRecord]


class ScheduleSegmentRecord(BaseModel):
    """A run of sessions under one contract in ``/futures/.../schedule``: the
    keys of ``schedule_dict``'s segments (it carries ``expiration``, unlike the
    time series' ``SegmentRecord``)."""

    first_session: date
    last_session: date
    instrument_id: int
    raw_symbol: str
    expiration: UtcIso

    @classmethod
    def from_segment(cls, segment: Segment) -> ScheduleSegmentRecord:
        contract = segment.contract
        return cls(
            first_session=segment.first_session,
            last_session=segment.last_session,
            instrument_id=contract.instrument_id,
            raw_symbol=contract.raw_symbol,
            expiration=contract.expiration,
        )


class FuturesScheduleResponse(BaseModel):
    """``GET /api/v1/futures/products/{product}/schedule``: the keys of
    ``roll_payload.schedule_dict``."""

    product: str
    rule: str
    is_default: bool
    segments: list[ScheduleSegmentRecord]
    answers: list[ContractAnswerRecord]
    unresolved: list[UnresolvedRecord]

    @classmethod
    def from_schedule(cls, schedule: RollSchedule) -> FuturesScheduleResponse:
        return cls(
            product=schedule.product,
            rule=str(schedule.rule),
            is_default=schedule.rule == DEFAULT_ROLL_RULE,
            segments=[
                ScheduleSegmentRecord.from_segment(s) for s in schedule.segments()
            ],
            answers=[
                ContractAnswerRecord.from_answer(a)
                for a in schedule.answers
                if isinstance(a, ActiveContract)
            ],
            unresolved=[_unresolved(u) for u in schedule.unresolved],
        )


class HeldRange(BaseModel):
    """The first and last session holding a contract's records."""

    first_session: date
    last_session: date


class ContractRecord(BaseModel):
    """One defined instrument (``/futures/products/{product}/contracts``),
    spreads included. ``min_price_increment`` and ``unit_of_measure_qty`` are
    stored fixed-point, so they are exact text like every price."""

    instrument_id: int
    raw_symbol: str | None
    instrument_class: str | None
    activation: UtcIso
    expiration: UtcIso
    exchange: str | None
    currency: str | None
    min_price_increment: str | None
    contract_multiplier: int | None
    unit_of_measure: str | None
    unit_of_measure_qty: str | None
    held: HeldRange | None

    @classmethod
    def from_catalog(cls, row: CatalogContract) -> ContractRecord:
        return cls(
            instrument_id=row.instrument_id,
            raw_symbol=row.raw_symbol,
            instrument_class=row.instrument_class,
            activation=utc_from_ns(row.activation_ns),
            expiration=utc_from_ns(row.expiration_ns),
            exchange=row.exchange,
            currency=row.currency,
            min_price_increment=_fixed(row.min_price_increment),
            contract_multiplier=row.contract_multiplier,
            unit_of_measure=row.unit_of_measure,
            unit_of_measure_qty=_fixed(row.unit_of_measure_qty),
            held=None
            if row.held is None
            else HeldRange(first_session=row.held[0], last_session=row.held[1]),
        )


def _fixed(raw: int | None) -> str | None:
    return None if raw is None else price_text(raw)


class FuturesContractsResponse(BaseModel):
    """``GET /api/v1/futures/products/{product}/contracts``: by expiration,
    then instrument id."""

    product: str
    contracts: list[ContractRecord]
