"""Per-batch record processing for ingest: pure NumPy, no I/O (slice 225, TD6).

A worker feeds each decoded ``RecordBatch`` through, in order:

1. :func:`resolve` — every record to a definition of the product whose
   validity window ``[activation_ns, expiration_ns]`` holds its ``ts_event``;
2. :func:`locate_sessions` — every record to a session position, the
   populated span tested first (221 D7);
3. :class:`OrdinalCarry` — ``sequence_ordinal``: the count of earlier records
   in the file with the same ``(instrument_id, ts_event, sequence)`` (222 TD1),
   carried across batches, with no contiguity assumption;
4. :class:`LedgerAccumulator` — count, volume and first/last event per
   (instrument, session), then one ledger row per valid instrument and session.

A failed check raises :class:`UnitCheckFailed` carrying the reason from
``ingest_checks``. Every class here is owned by one worker thread.
"""

from __future__ import annotations

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import numpy.typing as npt

from manta_trading.data.base.session_index import NO_SESSION, SessionIndex
from manta_trading.data.tick.ingest_checks import (
    IngestCheck,
    no_session_reason,
    outside_span_reason,
    resolution_reason,
)

I64 = npt.NDArray[np.int64]
U64 = npt.NDArray[np.uint64]
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ID_SHIFT = np.uint64(32)


class UnitCheckFailed(Exception):
    """A unit fails an ingest check; ``reason`` starts with ``<check>:``."""

    def __init__(self, check: IngestCheck, reason: str) -> None:
        self.check = check
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Definitions:
    """Definition windows sorted by ``(instrument_id, activation_ns)`` (TD6)."""

    instrument_id: I64
    activation_ns: I64
    expiration_ns: I64

    @classmethod
    def of(cls, rows: Sequence[tuple[int, int, int]]) -> Definitions:
        """From ``(instrument_id, activation_ns, expiration_ns)`` rows, any order."""
        table = np.array(rows, dtype=np.int64).reshape(-1, 3)
        order = np.lexsort((table[:, 1], table[:, 0]))
        table = table[order]
        return cls(table[:, 0].copy(), table[:, 1].copy(), table[:, 2].copy())


def resolve(
    definitions: Definitions, instrument_id: I64, ts_event: I64, product: str
) -> None:
    """Raise ``resolution`` unless every record has a definition window holding it.

    ``searchsorted`` finds each record's id; a single window is tested
    vectorized, and only ids with several windows that day are looped over.
    """
    ids, ts = instrument_id.astype(np.int64), ts_event.astype(np.int64)
    left = np.searchsorted(definitions.instrument_id, ids, side="left")
    right = np.searchsorted(definitions.instrument_id, ids, side="right")
    windows = right - left
    resolved = np.zeros(len(ids), dtype=bool)
    single = windows == 1
    first = left[single]
    resolved[single] = (definitions.activation_ns[first] <= ts[single]) & (
        ts[single] <= definitions.expiration_ns[first]
    )
    for multi in np.unique(ids[windows > 1]):
        rows = ids == multi
        lo, hi = np.searchsorted(definitions.instrument_id, [multi, multi + 1])
        act, exp = definitions.activation_ns[lo:hi], definitions.expiration_ns[lo:hi]
        held = (act[None, :] <= ts[rows, None]) & (ts[rows, None] <= exp[None, :])
        resolved[rows] = held.any(axis=1)
    if resolved.all():
        return
    miss = int(np.argmin(resolved))
    reason = resolution_reason(
        product, int(ids[miss]), int(ts[miss]), int((~resolved).sum())
    )
    raise UnitCheckFailed(IngestCheck.RESOLUTION, reason)


def utc_ns(instant: datetime) -> int:
    """An aware datetime as epoch nanoseconds, exact to the microsecond."""
    return (instant - _EPOCH) // timedelta(microseconds=1) * 1_000


@dataclass(frozen=True)
class SessionFrame:
    """The sessions a unit's day touches, the calendar's populated span and zone."""

    index: SessionIndex
    first_open: datetime
    last_close: datetime
    zone: ZoneInfo

    def neighbours(self, ts_ns: int) -> tuple[int, int]:
        """Positions of the sessions before and after an instant (``-1``: none)."""
        opens = [utc_ns(session.open_utc) for session in self.index.sessions]
        after = bisect.bisect_right(opens, ts_ns)
        return after - 1, after if after < len(opens) else NO_SESSION


def locate_sessions(frame: SessionFrame, ts_event: I64) -> I64:
    """Each record's session position; raise ``session_boundary`` on any miss.

    The populated span is tested first: outside it, ``locate_ns``'s "no
    session" cannot be told apart from a break (221 D7).
    """
    ts = ts_event.astype(np.int64)
    outside = (ts < utc_ns(frame.first_open)) | (ts > utc_ns(frame.last_close))
    if outside.any():
        reason = outside_span_reason(
            int(ts[np.argmax(outside)]),
            frame.first_open,
            frame.last_close,
            int(outside.sum()),
        )
        raise UnitCheckFailed(IngestCheck.SESSION_BOUNDARY, reason)
    positions = frame.index.locate_ns(ts)
    missing = positions == NO_SESSION
    if missing.any():
        first = int(ts[np.argmax(missing)])
        before, after = frame.neighbours(first)
        sessions = frame.index.sessions
        reason = no_session_reason(
            first,
            frame.zone,
            None if before == NO_SESSION else sessions[before],
            None if after == NO_SESSION else sessions[after],
            int(missing.sum()),
        )
        raise UnitCheckFailed(IngestCheck.SESSION_BOUNDARY, reason)
    return positions


class OrdinalCarry:
    """``sequence_ordinal`` across a file's batches (TD6, 222 TD1).

    A record's ordinal is the number of earlier records in the file with the
    same ``(instrument_id, ts_event, sequence)``. Within a batch: a stable
    lexsort and run lengths. Across batches: each triple's count from earlier
    batches, held as sorted arrays of distinct triples (the id and sequence
    packed in one ``uint64``) with counts, merged per batch.
    """

    def __init__(self) -> None:
        self._hi: U64 = np.empty(0, dtype=np.uint64)  # instrument_id << 32 | sequence
        self._lo: U64 = np.empty(0, dtype=np.uint64)  # ts_event
        self._count: I64 = np.empty(0, dtype=np.int64)

    def ordinals(self, instrument_id: I64, ts_event: I64, sequence: I64) -> I64:
        hi = (instrument_id.astype(np.uint64) << _ID_SHIFT) | sequence.astype(np.uint64)
        lo = ts_event.astype(np.uint64)
        order = np.lexsort((lo, hi))  # stable: file order within a triple
        hi_s, lo_s = hi[order], lo[order]
        starts = _run_starts(hi_s, lo_s)
        run_of = np.cumsum(starts) - 1
        start_at = np.flatnonzero(starts)
        rank = np.arange(len(order)) - start_at[run_of]
        distinct_hi, distinct_lo = hi_s[start_at], lo_s[start_at]
        earlier = self._earlier(distinct_hi, distinct_lo)
        result = np.empty(len(order), dtype=np.int64)
        result[order] = rank + earlier[run_of]
        self._merge(distinct_hi, distinct_lo, np.diff(np.append(start_at, len(order))))
        return result

    def _earlier(self, hi: U64, lo: U64) -> I64:
        """The carried count of each distinct batch triple (0 when unseen)."""
        merged_hi = np.concatenate((self._hi, hi))
        merged_lo = np.concatenate((self._lo, lo))
        is_batch = np.concatenate(
            (np.zeros(len(self._hi), bool), np.ones(len(hi), bool))
        )
        # Carry before batch for an equal triple; both sides hold distinct keys.
        order = np.lexsort((is_batch, merged_lo, merged_hi))
        carried = np.concatenate((self._count, np.zeros(len(hi), np.int64)))[order]
        same = ~_run_starts(merged_hi[order], merged_lo[order])
        earlier_sorted = np.where(same, np.roll(carried, 1), 0)
        earlier = np.empty(len(order), dtype=np.int64)
        earlier[order] = earlier_sorted
        return earlier[len(self._hi) :]

    def _merge(
        self,
        hi: U64,
        lo: U64,
        counts: I64,
    ) -> None:
        merged_hi = np.concatenate((self._hi, hi))
        merged_lo = np.concatenate((self._lo, lo))
        merged_count = np.concatenate((self._count, counts))
        order = np.lexsort((merged_lo, merged_hi))
        starts = _run_starts(merged_hi[order], merged_lo[order])
        start_at = np.flatnonzero(starts)
        self._hi = merged_hi[order][start_at]
        self._lo = merged_lo[order][start_at]
        self._count = np.add.reduceat(merged_count[order], start_at)


def _run_starts(hi: U64, lo: U64) -> npt.NDArray[np.bool_]:
    """True where a sorted key differs from the one before it (and at 0)."""
    starts = np.ones(len(hi), dtype=bool)
    starts[1:] = (hi[1:] != hi[:-1]) | (lo[1:] != lo[:-1])
    return starts


@dataclass(frozen=True)
class LedgerRow:
    instrument_id: int
    calendar_id: str
    session_date: date
    record_count: int
    volume: int
    first_event_ns: int | None
    last_event_ns: int | None


class LedgerAccumulator:
    """Count, volume and first/last ``ts_event`` per (instrument, session) (TD6)."""

    def __init__(self) -> None:
        self._totals: dict[tuple[int, int], list[int]] = {}

    def add(self, instrument_id: I64, positions: I64, size: I64, ts_event: I64) -> None:
        keys = np.stack((instrument_id.astype(np.int64), positions), axis=1)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        inverse = inverse.reshape(-1)
        count = np.bincount(inverse, minlength=len(unique))
        volume = np.zeros(len(unique), dtype=np.int64)
        np.add.at(volume, inverse, size.astype(np.int64))
        first = np.full(len(unique), np.iinfo(np.int64).max, dtype=np.int64)
        np.minimum.at(first, inverse, ts_event.astype(np.int64))
        last = np.full(len(unique), np.iinfo(np.int64).min, dtype=np.int64)
        np.maximum.at(last, inverse, ts_event.astype(np.int64))
        for i, (instrument, position) in enumerate(unique.tolist()):
            total = self._totals.setdefault(
                (instrument, position), [0, 0, int(first[i]), int(last[i])]
            )
            total[0] += int(count[i])
            total[1] += int(volume[i])
            total[2] = min(total[2], int(first[i]))
            total[3] = max(total[3], int(last[i]))

    def rows(
        self, definitions: Definitions, frame: SessionFrame, calendar_id: str
    ) -> list[LedgerRow]:
        """One row per definition valid in each session; zero rows for no records.

        Every accumulated (instrument, session) must be in that set: resolution
        and session location already placed each record in a window and a
        session, so a miss here is a defect, not a unit failure.
        """
        rows: list[LedgerRow] = []
        placed: set[tuple[int, int]] = set()
        for position, session in enumerate(frame.index.sessions):
            opened, closed = utc_ns(session.open_utc), utc_ns(session.close_utc)
            meets = (definitions.activation_ns <= closed) & (
                definitions.expiration_ns >= opened
            )
            for instrument in np.unique(definitions.instrument_id[meets]).tolist():
                placed.add((instrument, position))
                total = self._totals.get((instrument, position))
                rows.append(
                    LedgerRow(
                        instrument_id=instrument,
                        calendar_id=calendar_id,
                        session_date=session.session_date,
                        record_count=0 if total is None else total[0],
                        volume=0 if total is None else total[1],
                        first_event_ns=None if total is None else total[2],
                        last_event_ns=None if total is None else total[3],
                    )
                )
        stray = set(self._totals) - placed
        assert not stray, (
            f"records outside the ledger's instrument set: {sorted(stray)}"
        )
        return rows
