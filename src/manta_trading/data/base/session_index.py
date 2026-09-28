"""Pure lookup from a timestamp to the trading session that contains it (221 D7).

A :class:`Session` is a closed UTC interval ``[open_utc, close_utc]`` dated by
the day it closes. :class:`SessionIndex` holds a validated, ordered run of
sessions and answers "which session contains this instant" in scalar
(:meth:`SessionIndex.locate`) and vectorized (:meth:`SessionIndex.locate_ns`)
form. Both forms share one position routine, so they cannot disagree.

The index knows nothing about the populated range of a calendar: an instant
outside every session is simply "no session". Distinguishing "outside the
populated range" from "in a break" is the caller's job
(``TradingCalendar.session_containing``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import numpy.typing as npt

NO_SESSION = -1
"""Position ``locate_ns`` returns where no session contains the timestamp."""


@dataclass(frozen=True)
class Session:
    """One trading session: a closed UTC interval dated by its close day."""

    calendar_id: str
    session_date: date
    open_utc: datetime
    close_utc: datetime


def _to_ns(ts: datetime) -> int:
    """Nanoseconds since the epoch for a tz-aware ``ts``.

    Raises:
        ValueError: ``ts`` is naive — its instant is undefined.
    """
    offset = ts.utcoffset()
    if offset is None:
        raise ValueError(f"naive datetime {ts.isoformat()} has no defined instant")
    return int(np.datetime64(ts.replace(tzinfo=None) - offset, "ns").astype(np.int64))


class SessionIndex:
    """Ordered, non-overlapping sessions with scalar and vectorized lookup."""

    def __init__(self, sessions: Sequence[Session]) -> None:
        """Validate and index ``sessions``.

        Raises:
            ValueError: a session has ``open_utc >= close_utc``, the input is
                not sorted by ``open_utc``, or a session overlaps the next one.
                The input is never reordered: an unordered caller has a bug.
        """
        self._sessions = tuple(sessions)
        opens = [_to_ns(s.open_utc) for s in self._sessions]
        closes = [_to_ns(s.close_utc) for s in self._sessions]
        for i, session in enumerate(self._sessions):
            if opens[i] >= closes[i]:
                raise ValueError(
                    f"session {session.session_date.isoformat()} is inverted or "
                    f"empty: open {session.open_utc.isoformat()} >= close "
                    f"{session.close_utc.isoformat()}"
                )
            if i and opens[i] < opens[i - 1]:
                raise ValueError(
                    f"sessions are not sorted by open: "
                    f"{session.session_date.isoformat()} follows "
                    f"{self._sessions[i - 1].session_date.isoformat()}"
                )
            if i and opens[i] <= closes[i - 1]:
                raise ValueError(
                    f"session {session.session_date.isoformat()} overlaps "
                    f"{self._sessions[i - 1].session_date.isoformat()}"
                )
        self._opens: npt.NDArray[np.int64] = np.array(opens, dtype=np.int64)
        self._closes: npt.NDArray[np.int64] = np.array(closes, dtype=np.int64)

    @property
    def sessions(self) -> tuple[Session, ...]:
        """The indexed sessions, in order."""
        return self._sessions

    def locate_ns(self, ts_ns: npt.NDArray[np.int64]) -> npt.NDArray[np.int64]:
        """Positions of the sessions containing each epoch-ns timestamp.

        Returns an int64 array the shape of ``ts_ns``: the index into
        :attr:`sessions`, or :data:`NO_SESSION` where no session contains the
        timestamp. Callers must already have checked that the timestamps lie
        inside the calendar's populated range (221 D7) — outside it, "no
        session" here is indistinguishable from a break or a closed day.
        """
        ts = np.asarray(ts_ns, dtype=np.int64)
        pos = np.searchsorted(self._opens, ts, side="right") - 1
        inside = pos >= 0
        inside[inside] = ts[inside] <= self._closes[pos[inside]]
        return np.where(inside, pos, NO_SESSION).astype(np.int64)

    def locate(self, ts: datetime) -> Session | None:
        """The session whose closed interval contains ``ts``, or ``None``.

        Raises:
            ValueError: ``ts`` is naive.
        """
        pos = int(self.locate_ns(np.array([_to_ns(ts)], dtype=np.int64))[0])
        return None if pos == NO_SESSION else self._sessions[pos]
