"""Session classification for ``status`` and ``coverage``: pure, no I/O (225).

LLD 225 TD9. A session's days are the UTC days it touches; a day's unit is
the highest-ranked current tier unit of the shape that day (TD5's rule). A
session takes the worst status of its days, by :data:`STATUS_PRECEDENCE`, the
one statement of the order. Its condition is the worst condition of its days
(``missing`` > ``degraded`` > ``pending`` > ``available``), or ``unknown`` when
a day has no condition row; Saturdays are exempt (the provider never lists
them). ``complete`` is unit-level (TD10): every day's unit is *ingested*.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CONDITION_ABSENT_WEEKDAY,
    TICK_TIER_RANK,
    DatasetCondition,
    TickSchema,
    UnitState,
)


class TickSessionStatus(StrEnum):
    """A session's status, declared worst first (TD9's table order)."""

    RETRY_EXHAUSTED = "retry_exhausted"
    FAILED = "failed"
    PROVIDER_HOLE = "provider_hole"
    IN_FLIGHT = "in_flight"
    AWAITING_INGEST = "awaiting_ingest"
    MISSING = "missing"
    PENDING = "pending"
    EDGE_UNKNOWN = "edge_unknown"
    COMPLETE = "complete"


#: Worst first; the first rule that matches wins.
STATUS_PRECEDENCE: tuple[TickSessionStatus, ...] = tuple(TickSessionStatus)

#: A session's condition when some non-Saturday day has no condition row.
UNKNOWN_CONDITION = "unknown"
#: Conditions worst first (the architecture's order).
CONDITION_PRECEDENCE: tuple[DatasetCondition, ...] = (
    DatasetCondition.MISSING,
    DatasetCondition.DEGRADED,
    DatasetCondition.PENDING,
    DatasetCondition.AVAILABLE,
)

_IN_FLIGHT_STATES = frozenset(
    {UnitState.REQUESTED, UnitState.SUBMITTED, UnitState.DELIVERED}
)
_AWAITING_STATES = frozenset({UnitState.DOWNLOADED, UnitState.VERIFIED})
_NO_UNIT = {
    DatasetCondition.MISSING: TickSessionStatus.PROVIDER_HOLE,
    DatasetCondition.AVAILABLE: TickSessionStatus.MISSING,
    DatasetCondition.DEGRADED: TickSessionStatus.MISSING,
    DatasetCondition.PENDING: TickSessionStatus.PENDING,
}


@dataclass(frozen=True)
class DayUnit:
    """A current tier unit of the shape on one day."""

    unit_id: int
    schema: TickSchema
    state: UnitState
    fetch_status: FetchStatus


@dataclass(frozen=True)
class DayFacts:
    """What status knows about one UTC day of a session."""

    day: date
    unit: DayUnit | None
    condition: DatasetCondition | None


def day_unit(units: Iterable[DayUnit]) -> DayUnit | None:
    """The highest-ranked current tier unit of a day (TD5); newest on a tie."""
    return max(units, key=lambda u: (TICK_TIER_RANK[u.schema], u.unit_id), default=None)


def classify_day(facts: DayFacts) -> TickSessionStatus:
    unit = facts.unit
    if unit is None:
        if facts.condition is None:
            return TickSessionStatus.EDGE_UNKNOWN
        return _NO_UNIT[facts.condition]
    if unit.fetch_status is FetchStatus.RETRY_EXHAUSTED:
        return TickSessionStatus.RETRY_EXHAUSTED
    if unit.fetch_status is FetchStatus.FAILED_RETRYABLE:
        return TickSessionStatus.FAILED
    if unit.fetch_status is FetchStatus.PROVIDER_HOLE:
        return TickSessionStatus.PROVIDER_HOLE  # current, so not reopened
    if unit.state in _IN_FLIGHT_STATES:
        return TickSessionStatus.IN_FLIGHT
    if unit.state in _AWAITING_STATES:
        return TickSessionStatus.AWAITING_INGEST
    return TickSessionStatus.COMPLETE


def worst(statuses: Iterable[TickSessionStatus]) -> TickSessionStatus:
    return min(statuses, key=STATUS_PRECEDENCE.index)


def classify_session(days: Sequence[DayFacts]) -> TickSessionStatus:
    return worst(classify_day(facts) for facts in days)


def session_condition(days: Sequence[DayFacts]) -> str:
    """The worst condition of the session's days, or ``unknown``."""
    rated = [facts for facts in days if facts.day.weekday() != CONDITION_ABSENT_WEEKDAY]
    if any(facts.condition is None for facts in rated):
        return UNKNOWN_CONDITION
    conditions = {facts.condition for facts in rated}
    return next(
        (str(c) for c in CONDITION_PRECEDENCE if c in conditions), UNKNOWN_CONDITION
    )


@dataclass(frozen=True)
class SessionVerdict:
    session_date: date
    status: TickSessionStatus
    condition: str
    days: tuple[DayFacts, ...]

    @property
    def held(self) -> bool:
        return any(facts.unit is not None for facts in self.days)

    @property
    def provider_missing(self) -> bool:
        return any(f.condition is DatasetCondition.MISSING for f in self.days)


def verdict(session_date: date, days: Sequence[DayFacts]) -> SessionVerdict:
    return SessionVerdict(
        session_date, classify_session(days), session_condition(days), tuple(days)
    )


def caught_up(verdicts: Sequence[SessionVerdict], wanted: bool) -> bool | None:
    """Every session complete, or a hole on a provider-``missing`` day (TD9).

    ``None`` ("n/a") when the product has no wanted range.
    """
    if not wanted:
        return None
    return all(
        v.status is TickSessionStatus.COMPLETE
        or (v.status is TickSessionStatus.PROVIDER_HOLE and v.provider_missing)
        for v in verdicts
    )
