"""The roll resolver's inputs and results (slice 229, API Contracts). Pure.

``roll_resolve`` computes these from stored state; ``active_contract`` reads
that state; 230 (``get --product``) and 231 (the API) consume the results.
Every answer names the rows it was decided from (``definition_unit_id``,
``basis_unit_ids``) so a client can tell when its inputs changed (State
Management).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.roll_rules import RollRule
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_status import TickSessionStatus

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: The remedy when the calendar does not reach a session the roll path needs.
EXTEND_REMEDY = "run mt data extend"


class RollInputError(Exception):
    """A broken invariant in the stored inputs (TD9): never read as an answer.

    The message names the contract and the session (or the calendar remedy).
    """


def utc_from_ns(ns: int) -> datetime:
    """Epoch nanoseconds as an aware UTC datetime (microsecond grain)."""
    return _EPOCH + timedelta(microseconds=ns // 1_000)


@dataclass(frozen=True)
class ContractDefinition:
    """An outright definition row: one contract's validity window."""

    instrument_id: int
    raw_symbol: str
    activation_ns: int
    expiration_ns: int
    unit_id: int


@dataclass(frozen=True)
class SessionVolume:
    """One ledger row's outright volume: (session, instrument) from one unit."""

    session_date: date
    instrument_id: int
    volume: int
    unit_id: int


@dataclass(frozen=True)
class SessionOpenInterest:
    """One contract's resolved open interest for a session (303 D5): the value
    of the winning record, and the unit that wrote it."""

    session_date: date
    instrument_id: int
    value: int
    unit_id: int


@dataclass(frozen=True)
class RollInputs:
    """Everything one resolution reads, from one snapshot and one calendar read.

    ``sessions`` are ordered and cover the answered sessions plus their lag
    sessions before and the forward sessions a forecast or offset needs.
    ``statuses`` holds 225's verdict for each session the shape was judged on.
    """

    sessions: tuple[Session, ...]
    contracts: tuple[ContractDefinition, ...]
    volumes: tuple[SessionVolume, ...]
    statuses: Mapping[date, TickSessionStatus]
    definitions_as_of: date
    #: The calendar's last populated session (calendar ``known_through``).
    calendar_horizon: date
    #: Read only when the rule needs them (303 D9, D10): the bar family's volume
    #: and statuses when the entry wants bars, open interest and the statistics
    #: statuses for the ``n`` rule. Empty means "not read", never "zero".
    bar_volumes: tuple[SessionVolume, ...] = ()
    bar_statuses: Mapping[date, TickSessionStatus] = field(default_factory=dict)
    open_interest: tuple[SessionOpenInterest, ...] = ()
    statistics_statuses: Mapping[date, TickSessionStatus] = field(default_factory=dict)


@dataclass(frozen=True)
class ActiveContract:
    session_date: date
    instrument_id: int
    raw_symbol: str
    expiration: datetime  # UTC
    basis_session: date  # volume: S − lag; calendar: S
    definitions_as_of: date  # latest ingested definition unit day
    definition_unit_id: int  # unit of the chosen contract's definition row
    basis_unit_ids: tuple[int, ...]  # volume: units read for the lag session
    #: The family the ranking read (303 D10): ``tick_tier`` or ``bar_1m`` for
    #: volume, ``statistics`` for open interest; ``None`` for calendar.
    basis_family: SchemaFamily | None


@dataclass(frozen=True)
class Unresolved:
    session_date: date
    reason: str


Answer = ActiveContract | Unresolved


@dataclass(frozen=True)
class Segment:
    """A run of consecutive sessions answered by one contract."""

    first_session: date
    last_session: date
    contract: ActiveContract


@dataclass(frozen=True)
class RollSchedule:
    product: str
    rule: RollRule
    answers: tuple[Answer, ...]  # one per session, in order

    @property
    def unresolved(self) -> tuple[Unresolved, ...]:
        return tuple(a for a in self.answers if isinstance(a, Unresolved))

    def segments(self) -> tuple[Segment, ...]:
        """Consecutive same-contract runs; an unresolved session breaks a run, and
        so does a change of basis family (303 D10)."""
        runs: list[Segment] = []
        previous: Answer | None = None
        for answer in self.answers:
            if isinstance(answer, ActiveContract):
                if (
                    runs
                    and isinstance(previous, ActiveContract)
                    and previous.instrument_id == answer.instrument_id
                    and previous.basis_family is answer.basis_family
                ):
                    last = runs[-1]
                    runs[-1] = Segment(
                        last.first_session, answer.session_date, last.contract
                    )
                else:
                    runs.append(
                        Segment(answer.session_date, answer.session_date, answer)
                    )
            previous = answer
        return tuple(runs)


@dataclass(frozen=True)
class NextRoll:
    session_date: date
    contract: ActiveContract


@dataclass(frozen=True)
class ActiveLine:
    """One rule's answer for the latest complete session L, and what follows.

    When ``error`` is set, every other field but ``product`` and ``rule`` is
    empty (TD9). ``reason`` says why there is no L at all (nothing held, or no
    complete session held); it is not a failure.
    """

    product: str
    rule: RollRule
    session_date: date | None = None  # L
    answer: Answer | None = None
    next_roll: NextRoll | None = None
    known_through: date | None = None
    roll_no_later_than: date | None = None
    reason: str | None = None
    error: str | None = None
