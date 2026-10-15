"""The ingest heartbeat of ``mt data tick run`` (slice 234, Data Flow step 2).

Ingest workers hand :meth:`IngestHeartbeat.add` the row count of each ``COPY``
batch, from their own threads. The heartbeat sums them and calls ``report``
with ``(rows loaded, rows expected)`` at most once per interval, then once
more from :meth:`flush` when ingest ends. ``report`` runs on the calling
thread outside the lock, so a slow database write never stalls another
worker's count. Two reports cannot overlap: a worker's report is spaced by the
interval, and the flush runs only after every worker has returned.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence

from manta_trading.data.tick.constants import TICK_HEARTBEAT_SECONDS
from manta_trading.data.tick.manifest_reads import UnitRow

#: Receives ``(rows loaded, rows expected or None)``.
Report = Callable[[int, int | None], None]


def expected_rows(units: Sequence[UnitRow]) -> int | None:
    """The provider's record count over ``units``; None if any lacks one,
    because a partial sum would read as a total that is too small."""
    counts = [unit.provider_record_count for unit in units]
    if any(count is None for count in counts):
        return None
    return sum(count for count in counts if count is not None)


class IngestHeartbeat:
    """Thread-safe accumulator that reports at most once per interval."""

    def __init__(
        self,
        report: Report,
        *,
        interval_seconds: float = TICK_HEARTBEAT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._report = report
        self._interval = interval_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._done = 0
        self._total: int | None = None
        self._last = clock()
        self._flushed = False

    def set_units(self, units: Sequence[UnitRow]) -> None:
        """The units this ingest will load; sets the expected total."""
        with self._lock:
            self._total = expected_rows(units)

    def add(self, rows: int) -> None:
        """One batch's rows (an ``IngestInputs.on_batch_rows`` hook)."""
        with self._lock:
            self._done += rows
            now = self._clock()
            if now - self._last < self._interval:
                return
            self._last = now
            done, total = self._done, self._total
        self._report(done, total)

    def flush(self) -> None:
        """Report the final count; only the first call reports."""
        with self._lock:
            if self._flushed:
                return
            self._flushed = True
            done, total = self._done, self._total
        self._report(done, total)
