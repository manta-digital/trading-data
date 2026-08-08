"""Token-bucket throttling for EODHD API call quotas (slice 146 Decision A).

Two-window bucket: short-window burst (per-minute) + long-window rolling
daily cap. ``consume(call_type)`` blocks the calling thread until both
windows have capacity for the call's credit cost.

The bucket is process-local; restart loss is bounded by
``EODHD_PER_MINUTE_BURST`` credits and is acceptable per the slice
design "Out of Scope" entry on persistent quota accounting.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

from manta_trading.constants import (
    EODHD_BULK_EOD_BASE_COST,
    EODHD_DAILY_QUOTA,
    EODHD_EOD_CALL_COST,
    EODHD_INTRADAY_CALL_COST,
    EODHD_PER_MINUTE_BURST,
)


class CallType(StrEnum):
    """Discriminator for an outbound EODHD HTTP call's credit cost."""

    EOD = "eod"
    INTRADAY = "intraday"
    BULK_EOD = "bulk_eod"


CALL_COSTS: dict[CallType, int] = {
    CallType.EOD: EODHD_EOD_CALL_COST,
    CallType.INTRADAY: EODHD_INTRADAY_CALL_COST,
    CallType.BULK_EOD: EODHD_BULK_EOD_BASE_COST,
}

_MINUTE_WINDOW_SECONDS: float = 60.0
_DAY_WINDOW_SECONDS: float = 86400.0

_STOP_POLL_SECONDS: float = 1.0
"""Upper bound on a single ``consume`` sleep when ``stop_requested`` is set.

A quota wait must re-check the shutdown flag at this cadence: ``time.sleep``
is transparently restarted after a signal handler runs (PEP 475), so a single
full-length sleep — a *daily*-window wait can be hours — would resume after
Ctrl-C and ignore the requested exit (the 20260807 minute-daemon kill)."""


class QuotaWaitAborted(Exception):
    """A blocked ``consume`` observed ``stop_requested`` and gave up waiting.

    Raised instead of deducting credits or making progress; the call it
    would have authorized never happens. Cycle loops catch this to exit
    cleanly between (or within) symbols during shutdown.
    """


@dataclass
class _Window:
    """A single rolling-window credit bucket.

    ``capacity`` is the maximum credits allowed in any ``window_seconds``
    interval. Refill is continuous: ``capacity / window_seconds`` credits
    per second. ``available`` is recomputed from ``last_refill`` lazily on
    each ``request``/``peek``.
    """

    capacity: int
    window_seconds: float
    available: float
    last_refill: float

    def refill(self, now: float) -> None:
        # Clock-jump-backwards guard: never grant credits for negative
        # elapsed time. NTP corrections must not over-fill the bucket.
        elapsed = now - self.last_refill
        if elapsed <= 0:
            self.last_refill = now
            return
        rate = self.capacity / self.window_seconds
        self.available = min(float(self.capacity), self.available + elapsed * rate)
        self.last_refill = now

    def time_until(self, cost: int, now: float) -> float:
        """Seconds the caller must wait before ``cost`` is available.

        Returns 0.0 if the call can proceed immediately. Includes a tiny
        epsilon over the deficit so float-drift can't wedge the loop in
        a sub-microsecond sleep cycle.
        """
        self.refill(now)
        if self.available + 1e-9 >= cost:
            return 0.0
        deficit = cost - self.available
        rate = self.capacity / self.window_seconds
        # Add a small slack so the next refill clears the threshold even
        # under float-rounding noise.
        return (deficit / rate) + 1e-6

    def deduct(self, cost: int) -> None:
        self.available -= cost


@dataclass
class QuotaBucket:
    """Two-window token bucket sized to EODHD's All-In-One plan.

    The minute window enforces the burst ceiling; the day window enforces
    the rolling daily cap. ``consume(call_type)`` blocks (real
    ``time.sleep`` by default) until both windows have capacity; the
    runner is single-threaded so blocking is acceptable.

    A custom ``now`` clock and ``sleep`` callable can be injected for
    deterministic testing.

    ``stop_requested`` (assigned by the daemon Runner, never required for
    CLI one-shot buckets) lets a blocked ``consume`` abort with
    :class:`QuotaWaitAborted` when shutdown is flagged, instead of resuming
    its sleep after the signal handler returns. When set, sleeps are capped
    at ``_STOP_POLL_SECONDS`` so the flag is observed promptly; when None,
    ``consume`` sleeps full-length exactly as before.
    """

    now: Callable[[], float] = field(default=time.monotonic)
    sleep: Callable[[float], None] = field(default=time.sleep)
    stop_requested: Callable[[], bool] | None = None
    minute_window: _Window = field(init=False)
    day_window: _Window = field(init=False)
    _spent_log: list[tuple[float, int]] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        start = self.now()
        self.minute_window = _Window(
            capacity=EODHD_PER_MINUTE_BURST,
            window_seconds=_MINUTE_WINDOW_SECONDS,
            available=float(EODHD_PER_MINUTE_BURST),
            last_refill=start,
        )
        self.day_window = _Window(
            capacity=EODHD_DAILY_QUOTA,
            window_seconds=_DAY_WINDOW_SECONDS,
            available=float(EODHD_DAILY_QUOTA),
            last_refill=start,
        )

    @staticmethod
    def cost_for(call_type: CallType) -> int:
        return CALL_COSTS[call_type]

    def consume(self, call_type: CallType) -> None:
        """Block until both windows have capacity, then deduct.

        Loops because each ``sleep`` may be interrupted (signal) or
        the caller may have provided a fake clock that doesn't actually
        advance time when ``sleep`` is called — re-checking guards both.
        """
        cost = self.cost_for(call_type)
        if cost > self.minute_window.capacity or cost > self.day_window.capacity:
            # Asking for more than any window can ever hold is a config
            # bug; failing fast beats spinning forever.
            raise ValueError(
                f"Call cost {cost} exceeds bucket capacity "
                f"(minute={self.minute_window.capacity}, "
                f"day={self.day_window.capacity})"
            )
        while True:
            now = self.now()
            wait_minute = self.minute_window.time_until(cost, now)
            wait_day = self.day_window.time_until(cost, now)
            wait = max(wait_minute, wait_day)
            if wait <= 0.0:
                self.minute_window.deduct(cost)
                self.day_window.deduct(cost)
                self._spent_log.append((now, cost))
                self._trim_spent_log(now)
                return
            if self.stop_requested is None:
                self.sleep(wait)
            elif self.stop_requested():
                raise QuotaWaitAborted(
                    f"quota wait aborted by shutdown ({wait:.1f}s remaining "
                    f"for {call_type})"
                )
            else:
                self.sleep(min(wait, _STOP_POLL_SECONDS))

    def spent_today(self) -> int:
        """Credits consumed in the rolling 24h window ending now."""
        now = self.now()
        self._trim_spent_log(now)
        return sum(cost for _, cost in self._spent_log)

    def _trim_spent_log(self, now: float) -> None:
        cutoff = now - _DAY_WINDOW_SECONDS
        # Drop entries older than the day window so spent_today reflects
        # the rolling 24h spend rather than process-lifetime spend.
        # Entries are appended in monotonic clock order, so a left-trim
        # is O(k) on the number actually expiring, not O(n) on the log.
        log = self._spent_log
        i = 0
        while i < len(log) and log[i][0] <= cutoff:
            i += 1
        if i > 0:
            del log[:i]
