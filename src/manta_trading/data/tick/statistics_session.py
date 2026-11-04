"""Session assignment of a statistics record from its reference time (slice 303, D4).

Pure: no connection, no calendar object. A referenced statistic (settlement,
cleared volume, open interest) belongs to the session whose trading date is the
UTC date of ``ts_ref``; sessions are dated by their close day, which is CME's
trading date. Every other statistic is stored with no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt

from manta_trading.data.tick.ingest_checks import (
    IngestCheck,
    StatReferenceProblem,
    stat_reference_reason,
)
from manta_trading.data.tick.ingest_records import I64, UnitCheckFailed
from manta_trading.data.tick.statistics_constants import REFERENCED_STAT_TYPES
from manta_trading.market.schema.seed_cme_hours import CME_DATASET_START

NS_PER_DAY = 86_400 * 1_000_000_000
EPOCH_DATE = datetime(1970, 1, 1, tzinfo=UTC).date()


@dataclass(frozen=True)
class ReferenceCalendar:
    """The product's calendar as ingest needs it: id and every session date."""

    calendar_id: str
    session_dates: frozenset[date]


@dataclass(frozen=True)
class SessionAssignment:
    """Where a record lands: both ``None`` for a record with no session.

    ``before_dataset`` marks a referenced record dated before the dataset's
    start: stored sessionless and counted in the unit result.
    """

    calendar_id: str | None
    session_date: date | None
    before_dataset: bool = False


NO_SESSION_ASSIGNED = SessionAssignment(None, None)


def utc_date_of_ns(ts_ns: int) -> date:
    """The UTC calendar date of an epoch-nanosecond instant."""
    return EPOCH_DATE + timedelta(days=ts_ns // NS_PER_DAY)


def assign_session(
    stat_type: int,
    ts_ref_ns: int | None,
    unit_date: date,
    calendar: ReferenceCalendar,
) -> SessionAssignment | StatReferenceProblem:
    """The session of one record, or why its reference time cannot give one.

    ``ts_ref_ns`` is ``None`` for an undefined reference time. The date must be
    a session of the calendar and no later than the unit's date.
    """
    if stat_type not in REFERENCED_STAT_TYPES:
        return NO_SESSION_ASSIGNED
    if ts_ref_ns is None:
        return StatReferenceProblem.UNDEFINED
    referenced = utc_date_of_ns(ts_ref_ns)
    if referenced < CME_DATASET_START:
        return SessionAssignment(None, None, before_dataset=True)
    if referenced > unit_date:
        return StatReferenceProblem.AFTER_THE_UNIT
    if referenced not in calendar.session_dates:
        return StatReferenceProblem.NOT_A_SESSION
    return SessionAssignment(calendar.calendar_id, referenced)


#: ``UNDEF_TIMESTAMP`` in a statistics record's ``ts_ref`` (DBN: ``u64::MAX``).
UNDEF_TS_REF = int(np.iinfo(np.uint64).max)
#: ``stat_type`` is ``u16``; one day and one type pack into one int64 key.
_TYPE_RADIX = 1 << 16
#: A batch day for "no usable day": an undefined ``ts_ref``.
NO_DAY = -1


@dataclass(frozen=True)
class BatchSessions:
    """A batch's session per record, as days since the epoch (``NO_DAY``: none)."""

    days: I64
    before_dataset: int


def assign_batch(
    instrument_id: npt.NDArray[np.integer[Any]],
    stat_type: npt.NDArray[np.integer[Any]],
    ts_ref: npt.NDArray[np.uint64],
    unit_date: date,
    calendar: ReferenceCalendar,
) -> BatchSessions:
    """``assign_session`` over a batch; raise ``stat_reference`` on a failure.

    Each distinct (stat type, reference day) is assigned once; a failure names
    the first record in the batch that carries it.
    """
    undefined = ts_ref == np.uint64(UNDEF_TS_REF)
    day = np.where(
        undefined, NO_DAY, (ts_ref // np.uint64(NS_PER_DAY)).astype(np.int64)
    )
    types = stat_type.astype(np.int64)
    packed = (day + 1) * _TYPE_RADIX + types
    keys, first, inverse = np.unique(packed, return_index=True, return_inverse=True)
    session_day = np.full(len(keys), NO_DAY, dtype=np.int64)
    before = np.zeros(len(keys), dtype=bool)
    for slot, key in enumerate(keys.tolist()):
        record_type, record_day = key % _TYPE_RADIX, key // _TYPE_RADIX - 1
        ref_ns = None if record_day == NO_DAY else record_day * NS_PER_DAY
        outcome = assign_session(record_type, ref_ns, unit_date, calendar)
        if isinstance(outcome, StatReferenceProblem):
            at = int(first[slot])
            raise UnitCheckFailed(
                IngestCheck.STAT_REFERENCE,
                stat_reference_reason(
                    int(instrument_id[at]),
                    record_type,
                    None if undefined[at] else int(ts_ref[at]),
                    outcome,
                ),
            )
        before[slot] = outcome.before_dataset
        if outcome.session_date is not None:
            session_day[slot] = record_day
    inverse = inverse.reshape(-1)
    return BatchSessions(
        days=session_day[inverse], before_dataset=int(before[inverse].sum())
    )
