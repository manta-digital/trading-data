"""Shared cadence arithmetic for the acquisition daemon (912 F004).

The runner's gates and the daily cycle's work list must agree on when today's
pass begins: the gate decides whether a pass may run, and the work list decides
which symbols it has already covered. If those two disagree by so much as the
offset, the cycle is handed symbols it then declines to fetch, or drops symbols
it never reached.

The expression was previously copied into five places — three gates in
``runner.py``, the wait message, and ``daily.py``'s pass boundary — each free to
drift from the others. One definition site is what makes the agreement
structural rather than a comment promising it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from manta_trading.constants import DAILY_CYCLE_START_OFFSET

_UTC = UTC


def utc_day_start(now: datetime) -> datetime:
    """Midnight UTC of the day containing ``now``."""
    utc_now = now.astimezone(_UTC)
    return datetime(utc_now.year, utc_now.month, utc_now.day, tzinfo=_UTC)


def daily_pass_boundary(now: datetime) -> datetime:
    """The instant the daily pass covering ``now`` begins.

    Today's UTC midnight plus :data:`DAILY_CYCLE_START_OFFSET`, which gives the
    provider time to publish the completed session's late bars. A symbol whose
    ``last_attempt_ts`` is at or after this instant has been attempted in the
    current pass; one stamped before it (or never) has not.
    """
    return utc_day_start(now) + DAILY_CYCLE_START_OFFSET
