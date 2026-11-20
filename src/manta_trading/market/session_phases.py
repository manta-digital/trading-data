"""Session phases and bucket placement for session-aligned bars (slice 174 D2).

A session has three half-open phases: pre ``[extended_open, open)``, regular
``[open, close)`` and post ``[close, extended_close)``. Each phase is bucketed
from its own start, so a bar never crosses a phase: a minute at ``ts`` in a
phase starting at ``anchor`` belongs to the bucket
``anchor + floor((ts - anchor) / width) * width``, and the last bucket of a
phase can be short. The instant ``extended_close`` is in no phase (D4).

Pure: the SQL in ``session_bars_sql`` implements the same rule, and its tests
check every minute against :func:`assign`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import ceil

from manta_trading.constants import SessionMode, SessionPhase


@dataclass(frozen=True)
class SessionBounds:
    """One session's UTC bounds (a ``trading_sessions`` row)."""

    session_date: date
    open: datetime
    close: datetime
    extended_open: datetime | None
    extended_close: datetime | None


@dataclass(frozen=True)
class PhaseInterval:
    """A phase of one session, ``[start, end)``."""

    phase: SessionPhase
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start) / timedelta(minutes=1))


def phases_for(bounds: SessionBounds, mode: SessionMode) -> list[PhaseInterval]:
    """The phases ``mode`` reads, in time order.

    Raises:
        ValueError: ``mode`` is ``utc`` (not a session mode), or ``extended``
            on a session without both extended bounds.
    """
    regular = PhaseInterval(SessionPhase.REGULAR, bounds.open, bounds.close)
    if mode is SessionMode.REGULAR:
        return [regular]
    if mode is not SessionMode.EXTENDED:
        raise ValueError(f"not a session mode: {mode!r}")
    if bounds.extended_open is None or bounds.extended_close is None:
        raise ValueError(
            f"session {bounds.session_date}: no extended bounds for an extended read"
        )
    return [
        PhaseInterval(SessionPhase.PRE, bounds.extended_open, bounds.open),
        regular,
        PhaseInterval(SessionPhase.POST, bounds.close, bounds.extended_close),
    ]


def assign(
    ts: datetime, bounds: SessionBounds, mode: SessionMode, width: timedelta
) -> tuple[SessionPhase, datetime] | None:
    """The phase and bucket start of the minute at ``ts``, or ``None`` outside."""
    for interval in phases_for(bounds, mode):
        if interval.start <= ts < interval.end:
            buckets = (ts - interval.start) // width
            return interval.phase, interval.start + buckets * width
    return None


def estimate_bars(
    bounds_list: Iterable[SessionBounds], mode: SessionMode, width: timedelta
) -> int:
    """Bars a dense symbol produces: ``ceil(phase_minutes / width)`` per phase."""
    width_minutes = int(width / timedelta(minutes=1))
    return sum(
        ceil(interval.minutes / width_minutes)
        for bounds in bounds_list
        for interval in phases_for(bounds, mode)
    )


def estimate_minutes(bounds_list: Iterable[SessionBounds], mode: SessionMode) -> int:
    """Minutes of the phases ``mode`` reads, summed over the sessions."""
    return sum(
        interval.minutes
        for bounds in bounds_list
        for interval in phases_for(bounds, mode)
    )
