"""The ledger of the ``STATISTICS`` family: referenced records only (slice 303, D2).

One row per (contract, session, stat type) with the record count and the first
and last receive time. A record with no session has nothing to ledger against;
the unit-level counts check already covers every record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from manta_trading.data.tick.ingest_records import I64
from manta_trading.data.tick.statistics_session import EPOCH_DATE


@dataclass(frozen=True)
class StatisticsLedgerRow:
    instrument_id: int
    calendar_id: str
    session_date: date
    stat_type: int
    record_count: int
    first_event_ns: int
    last_event_ns: int


class StatisticsLedgerAccumulator:
    """Count and first/last receive time per (instrument, stat type, session)."""

    def __init__(self, calendar_id: str) -> None:
        self._calendar_id = calendar_id
        self._totals: dict[tuple[int, int, int], list[int]] = {}

    def add(self, ids: I64, types: I64, day: I64, ts: I64) -> None:
        """Fold in records that all have a session: instrument, stat type, the
        session's day since the epoch, receive time."""
        if not len(ids):
            return
        order = np.lexsort((day, types, ids))
        ids, types, day, ts = ids[order], types[order], day[order], ts[order]
        starts = np.ones(len(ids), dtype=bool)
        starts[1:] = (
            (ids[1:] != ids[:-1]) | (types[1:] != types[:-1]) | (day[1:] != day[:-1])
        )
        at = np.flatnonzero(starts)
        counts = np.diff(np.append(at, len(ids)))
        firsts = np.minimum.reduceat(ts, at)
        lasts = np.maximum.reduceat(ts, at)
        for i, position in enumerate(at.tolist()):
            key = (int(ids[position]), int(types[position]), int(day[position]))
            total = self._totals.setdefault(key, [0, int(firsts[i]), int(lasts[i])])
            total[0] += int(counts[i])
            total[1] = min(total[1], int(firsts[i]))
            total[2] = max(total[2], int(lasts[i]))

    def rows(self) -> list[StatisticsLedgerRow]:
        return [
            StatisticsLedgerRow(
                instrument_id=instrument,
                calendar_id=self._calendar_id,
                session_date=EPOCH_DATE + timedelta(days=day),
                stat_type=stat_type,
                record_count=count,
                first_event_ns=first,
                last_event_ns=last,
            )
            for (instrument, stat_type, day), (count, first, last) in sorted(
                self._totals.items()
            )
        ]
