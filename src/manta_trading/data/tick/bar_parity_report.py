"""The parity report, built in memory (306 D7).

Per compared session: minute counts by class, how many close-stamped minutes
there are and how many of them agree, and both sides' volume totals. Sessions
that were planned but not compared (a tier not ``complete``) are listed with
both statuses. Nothing prints until the last session is in, so a failed run
never leaves a half report.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from manta_trading.data.tick.bar_parity import MinuteClass, MinuteRecord
from manta_trading.data.tick.tick_status import TickSessionStatus

#: Index of the volume in a minute's open, high, low, close, volume.
_VOLUME = -1


@dataclass(frozen=True)
class SkippedSession:
    """A planned session not compared, with both tiers' verdicts."""

    session_date: date
    tick_status: TickSessionStatus
    bar_status: TickSessionStatus


@dataclass(frozen=True)
class SessionReport:
    session_date: date
    counts: dict[MinuteClass, int]  # every class present, zeros included
    close_stamped: int
    close_stamped_agree: int
    tick_volume: int
    provider_volume: int
    #: Every non-agreeing minute plus every close-stamped one, whatever its class.
    minutes_listed: tuple[MinuteRecord, ...]


@dataclass(frozen=True)
class ParityReport:
    sessions: tuple[SessionReport, ...]
    skipped: tuple[SkippedSession, ...]

    @property
    def all_agree(self) -> bool:
        """Every compared minute agrees (also true when nothing was compared)."""
        return all(
            s.counts[cls] == 0
            for s in self.sessions
            for cls in MinuteClass
            if cls is not MinuteClass.AGREE
        )


def session_report(
    session_date: date, minutes: Sequence[MinuteRecord]
) -> SessionReport:
    counts = {cls: 0 for cls in MinuteClass}
    for minute in minutes:
        counts[minute.cls] += 1
    close_stamped = [m for m in minutes if m.at_close]
    return SessionReport(
        session_date,
        counts,
        len(close_stamped),
        sum(1 for m in close_stamped if m.cls is MinuteClass.AGREE),
        sum(m.tick[_VOLUME] for m in minutes if m.tick is not None),
        sum(m.provider[_VOLUME] for m in minutes if m.provider is not None),
        tuple(m for m in minutes if m.cls is not MinuteClass.AGREE or m.at_close),
    )


def build_report(
    sessions: Iterable[SessionReport], skipped: Iterable[SkippedSession]
) -> ParityReport:
    return ParityReport(tuple(sessions), tuple(skipped))
