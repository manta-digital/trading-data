"""Judge how much data a completed trading session actually holds (slice 921).

The 2026-09-07 failure was invisible because ``mt data health`` judged the
newest bar's AGE. Every session still had bars — one per symbol, at the
session open — so freshness passed while the universe collected ~45k bars/day
against ~2.0M/day through 2026-08-27. Age cannot see a session that is present
but nearly empty; mass can.

Split from ``health.py`` to keep both modules inside the ~300-line guideline.
The selection and the rule are pure (values in, verdict out) so the boundary
cases are unit-tested without a database; only ``fetch_*`` touches I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg

from manta_trading.constants import (
    GRANULARITY_BAR_MINUTES,
    GRANULARITY_SOURCE,
    HEALTH_MINUTE_SESSION_CALENDAR,
    HEALTH_MINUTE_SESSION_COLLECTION_LAG,
    HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE,
    HEALTH_MINUTE_SESSION_MIN_SYMBOLS,
    HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT,
    HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS,
    MINUTE_PASS_FIRING_TIMES_UTC,
    Granularity,
)

#: How many recent sessions to consider as judging candidates. Enough to cover
#: a long holiday stretch (Thanksgiving week, a Christmas/New-Year run) without
#: scanning the whole calendar.
CANDIDATE_SESSION_LIMIT = 10


@dataclass(frozen=True)
class TradingSessionBounds:
    """One calendar session's UTC open and close."""

    session_open_utc: datetime
    session_close_utc: datetime

    @property
    def minutes(self) -> float:
        """Session length in minutes — the denominator of the mass rate."""
        return (self.session_close_utc - self.session_open_utc).total_seconds() / 60


@dataclass(frozen=True)
class SessionMass:
    """What the judged session actually holds."""

    total_bars: int
    symbols_meeting_min_bars: int


def collecting_firing_finished_at(
    session: TradingSessionBounds,
    *,
    firing_times: tuple = MINUTE_PASS_FIRING_TIMES_UTC,
    lag: timedelta = HEALTH_MINUTE_SESSION_COLLECTION_LAG,
) -> datetime:
    """When the firing that collects ``session`` has finished.

    The first configured firing at or after the session close, plus the
    collection lag. For a regular 20:00 UTC close and an early 17:00 close
    alike this resolves to 13:05 + 3 h = 16:05 UTC the next day, because both
    closes fall after the day's single 13:05 firing (the one after EODHD
    publishes the day — see the constant).

    Firing times come from ``MINUTE_PASS_FIRING_TIMES_UTC``, the same constant
    the timer drift guard asserts against ``mt-minute-pass.timer`` — the check
    and the timer are not allowed to hold separate copies.
    """
    close = session.session_close_utc
    for firing in sorted(firing_times):
        candidate = close.replace(
            hour=firing.hour, minute=firing.minute, second=0, microsecond=0
        )
        if candidate >= close:
            return candidate + lag
    # Every firing today is before the close — the collecting one is tomorrow's
    # first.
    first = sorted(firing_times)[0]
    next_day = close + timedelta(days=1)
    return (
        next_day.replace(hour=first.hour, minute=first.minute, second=0, microsecond=0)
        + lag
    )


def select_judged_session(
    candidates: list[TradingSessionBounds], *, now: datetime
) -> TradingSessionBounds | None:
    """Return the newest session whose collecting firing has finished.

    Pure: takes ``now`` and the candidates, does no I/O, so the 16:04/16:05
    boundary is directly testable.

    Weekends and holidays need no special case — they are not
    ``trading_sessions`` rows, so they never appear among the candidates.

    Returns None when nothing qualifies (an empty window, a calendar rename, a
    long holiday stretch). The caller MUST treat None as an explicit non-OK
    verdict: a silent pass here is the exact silence this slice exists to end.
    """
    finished = [s for s in candidates if collecting_firing_finished_at(s) <= now]
    if not finished:
        return None
    return max(finished, key=lambda s: s.session_close_utc)


def fetch_candidate_sessions(
    conn: "psycopg.Connection[Any]",
    *,
    now: datetime,
    calendar: str = HEALTH_MINUTE_SESSION_CALENDAR,
    limit: int = CANDIDATE_SESSION_LIMIT,
) -> list[TradingSessionBounds]:
    """Read the most recent CLOSED sessions for the judged calendar.

    The only place ``trading_sessions`` is read for this check.

    ``session_close_utc <= now`` is load-bearing, not a tidy-up.
    ``trading_sessions`` is populated ~2 years ahead
    (``TRADING_SESSIONS_EXTENSION_YEARS``, kept current by
    ``maybe_extend_trading_sessions``), so "the newest rows" are always
    future-dated. Without this bound every candidate is a session that has not
    happened yet, none qualifies for judging, and the check reports "no
    completed session to judge" on every single run — a permanent FAIL that
    says nothing about the data. Measured against the load fixture, whose
    seeded calendar ran to 2028-12-29.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT session_open_utc, session_close_utc
              FROM trading_sessions
             WHERE calendar_id = %s
               AND session_close_utc IS NOT NULL
               AND session_close_utc <= %s
             ORDER BY session_open_utc DESC
             LIMIT %s
            """,
            (calendar, now, limit),
        )
        return [
            TradingSessionBounds(session_open_utc=row[0], session_close_utc=row[1])
            for row in cur.fetchall()
        ]


def fetch_session_mass(
    conn: "psycopg.Connection[Any]",
    session: TradingSessionBounds,
    *,
    symbol_min_bars: int = HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS,
) -> SessionMass:
    """Measure the judged session's mass from the coarse minute cagg.

    One grouped query. Reads ``minute_4hour_ohlcv`` (via
    ``GRANULARITY_SOURCE``, never a literal) and NEVER raw ``minute_ohlcv`` —
    the §166/§167 latency cliff. Runs under
    ``HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT``; a timeout raises so the caller
    exits 2 like every other 919 check rather than reporting a mass it did not
    measure.

    The cagg's own freshness is judged by the existing
    ``cagg minute_4hour_ohlcv`` line and is deliberately not re-checked here.

    **Buckets that OVERLAP the session, not buckets that start inside it.**
    The cagg's 4-hour buckets are aligned to the day (…08:00, 12:00, 16:00…),
    not to the session, so a 13:30-20:00 UTC session opens partway through the
    12:00 bucket. Filtering ``time_bucket >= session_open`` would drop that
    bucket entirely — the first ~2.5 hours of every regular session, roughly
    38% of its bars — and report a mass far below the truth on healthy days.
    Measured against the load fixture: 990,000 bars counted out of 1,980,000
    seeded. The bound is therefore the last bucket that can still contain
    session bars, one bucket width back from the open.
    """
    cagg = GRANULARITY_SOURCE[Granularity.H4]
    bucket_width = GRANULARITY_BAR_MINUTES[Granularity.H4]
    window_start = session.session_open_utc - timedelta(minutes=bucket_width)
    with conn.cursor() as cur:
        cur.execute(
            f"SET LOCAL statement_timeout = '{HEALTH_MINUTE_SESSION_STATEMENT_TIMEOUT}'"
        )
        cur.execute(
            # The cagg name is a module constant, never user input.
            f"""
            SELECT COALESCE(SUM(per_symbol.bars), 0) AS total_bars,
                   COUNT(*) FILTER (WHERE per_symbol.bars >= %s) AS symbols
              FROM (
                    SELECT symbol, SUM(minute_count) AS bars
                      FROM {cagg}
                     WHERE time_bucket > %s
                       AND time_bucket < %s
                     GROUP BY symbol
                   ) AS per_symbol
            """,  # noqa: S608
            (
                symbol_min_bars,
                window_start,
                session.session_close_utc,
            ),
        )
        row = cur.fetchone()
    if row is None:
        return SessionMass(total_bars=0, symbols_meeting_min_bars=0)
    return SessionMass(total_bars=int(row[0]), symbols_meeting_min_bars=int(row[1]))


def check_minute_session_mass(
    session: TradingSessionBounds | None,
    mass: SessionMass | None,
    *,
    min_bars_per_minute: int = HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE,
    min_symbols: int = HEALTH_MINUTE_SESSION_MIN_SYMBOLS,
) -> tuple[bool, str]:
    """Judge one session's mass. Returns ``(ok, detail)``.

    Two floors, both of which the 2026-09-07 collapse breached: bars per
    session-minute (the universe-wide rate) and how many symbols reached a
    usable number of bars. Either alone can miss the failure — a handful of
    fully-collected symbols could clear the rate, and a universe of one-bar
    symbols could clear a naive symbol count.

    ``session is None`` is an explicit FAIL, never a pass: no judgeable session
    means the check could not answer, and answering "ok" would restore the
    silence this exists to end.
    """
    if session is None:
        return (
            False,
            "no completed session to judge — no trading_sessions row for "
            f"calendar {HEALTH_MINUTE_SESSION_CALENDAR} whose collecting "
            "firing has finished",
        )
    if mass is None:
        return False, "session mass could not be measured"

    minutes = session.minutes
    if minutes <= 0:
        return (
            False,
            f"session {session.session_open_utc:%Y-%m-%d} has a non-positive "
            f"length ({minutes:.0f} min) — calendar row is wrong",
        )

    bars_per_minute = mass.total_bars / minutes
    ok = (
        bars_per_minute >= min_bars_per_minute
        and mass.symbols_meeting_min_bars >= min_symbols
    )
    # psycopg hands back timestamptz in the connection's zone (America/Denver
    # in production), so format in UTC or the label lies (#22: "07:30-14:00
    # UTC" for a 13:30-20:00 session).
    open_utc = session.session_open_utc.astimezone(UTC)
    close_utc = session.session_close_utc.astimezone(UTC)
    return ok, (
        f"session {open_utc:%Y-%m-%d} "
        f"({open_utc:%H:%M}-{close_utc:%H:%M} UTC): "
        f"{mass.total_bars:,} bars, {bars_per_minute:,.0f}/min "
        f"(floor {min_bars_per_minute:,}); "
        f"{mass.symbols_meeting_min_bars:,} symbols with "
        f"≥{HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS} bars "
        f"(floor {min_symbols:,})"
    )
