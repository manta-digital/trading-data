"""Reconcile gap rows over dates whose session status changed (slice 174 D8, #26).

After migration 068 a date can stop being a NYSE/NASDAQ session (2025-01-09, the
2027-2028 closures) or become one (2021-12-31). Gap rows are only rewritten when
a window is recomputed, so the cutover runs :func:`reconcile_calendar_gaps` over
those dates, through the existing writer (``update_data_gaps``) and nothing else.

Each date is reconciled over its own America/New_York day, never a wider window
(PM decision 2026-11-20). The writer rewrites every row contained in its window
with one status, and on production the multi-day rows touching these dates are
mostly terminal (``RETRY_EXHAUSTED``, ``PROVIDER_HOLE``) and span up to seven
years: widening the window would reset them to ``UNKNOWN`` and re-fetch years of
history. So:

- a closed date loses the gap rows contained in its day, terminal ones too
  (``force_reset_terminal``, scoped to the day: a provider-hole judgement on a
  day that is not a session means nothing); a multi-day row that spans it keeps
  its status (the day has no data to fetch);
- an opened date gains a gap row for a symbol that holds no bar on it and has no
  gap row over its session. Minute passes the session (open to close, as the
  minute seed writes since 921) as ``precomputed_ranges``: the bar check above is
  exact, where the coverage cagg is day-coarse. Daily lets the writer compute it.
  Both are clamped to the instrument's lifecycle.

A symbol whose ``acquisition_state`` row has no outcome yet is skipped and
counted: it has never been attempted, and its first full seed covers the date.
The outcome and ``last_attempt_ts`` of every symbol written are left as found.
A second call changes nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Final
from zoneinfo import ZoneInfo

from manta_trading.constants import DAEMON_LOCK_TIMEOUT
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    clamp_to_lifecycle,
    fetch_session_bounds,
)
from manta_trading.data.gaps.update_data_gaps import update_data_gaps
from manta_trading.data.locking import advisory_lock
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.market.schema.nyse_calendar_history import US_EQUITY_CALENDAR_IDS
from manta_trading.providers.types import ProviderType

if TYPE_CHECKING:
    import psycopg

GRANULARITIES: Final = ("minute", "daily")
_PROVIDER: Final = str(ProviderType.EODHD)
_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class GapCount:
    """Gap rows over one changed date for one symbol and granularity."""

    day: date
    granularity: str
    symbol: str
    before: int
    after: int


@dataclass
class ReconcileReport:
    """What :func:`reconcile_calendar_gaps` changed."""

    changed: list[GapCount] = field(default_factory=list)
    skipped_never_attempted: Counter[tuple[date, str]] = field(default_factory=Counter)
    totals_before: Counter[tuple[date, str]] = field(default_factory=Counter)
    totals_after: Counter[tuple[date, str]] = field(default_factory=Counter)

    def is_noop(self) -> bool:
        return not self.changed


def day_window(day: date) -> tuple[datetime, datetime]:
    """``day``'s America/New_York day as an inclusive UTC window."""
    start = datetime.combine(day, time(0), tzinfo=_ET)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=_ET)
    return start, end - timedelta(microseconds=1)


def reconcile_calendar_gaps(
    conn: psycopg.Connection[Any], dates: frozenset[date] | set[date]
) -> ReconcileReport:
    """Recompute gap rows over each date's day, minute and daily.

    Each symbol is written in its own transaction under the daemons' advisory
    lock, so it can run beside them. ``conn`` must not be inside a transaction.
    """
    report = ReconcileReport()
    for day in sorted(dates):
        is_session = _is_session(conn, day)
        for granularity in GRANULARITIES:
            window = day_window(day)
            before = gap_rows_over(conn, granularity, window)
            if is_session:
                candidates = _opened_candidates(conn, granularity, day)
            else:
                candidates = _closed_candidates(conn, granularity, window)
            for candidate in candidates:
                if candidate.outcome is None:
                    report.skipped_never_attempted[(day, granularity)] += 1
                    continue
                outcome = LastAttemptOutcome(candidate.outcome)
                _rewrite(conn, candidate, outcome, granularity, window, seed=is_session)
            after = gap_rows_over(conn, granularity, window)
            report.totals_before[(day, granularity)] = sum(before.values())
            report.totals_after[(day, granularity)] = sum(after.values())
            report.changed += [
                GapCount(day, granularity, s, before.get(s, 0), after.get(s, 0))
                for s in sorted(before.keys() | after.keys())
                if before.get(s, 0) != after.get(s, 0)
            ]
    return report


@dataclass(frozen=True)
class _Candidate:
    symbol: str
    outcome: str | None
    last_attempt_ts: datetime | None


def _rewrite(
    conn: psycopg.Connection[Any],
    candidate: _Candidate,
    outcome: LastAttemptOutcome,
    granularity: str,
    window: tuple[datetime, datetime],
    *,
    seed: bool,
) -> None:
    """One symbol's day through the writer: seed it, or clear contained rows.

    The writer stamps ``last_attempt_ts`` with now; it is put back, because the
    daily pass reads it as "attempted in this pass" and the minute pass orders
    by it. The outcome is passed through unchanged.
    """
    symbol = candidate.symbol
    from_ts, to_ts = window
    ranges = None
    if seed and granularity == "minute":
        ranges = _minute_session_ranges(conn, symbol, window)
    with conn.transaction():
        with advisory_lock(conn, symbol, granularity, timeout=DAEMON_LOCK_TIMEOUT):
            update_data_gaps(
                conn,
                symbol,
                granularity,
                from_ts,
                to_ts,
                FetchStatus.UNKNOWN if seed else None,
                # A closed day's terminal rows judged a day that is not a
                # session; the reset is scoped to rows inside the day, so
                # multi-day terminal rows are untouched.
                force_reset_terminal=not seed,
                outcome=outcome,
                precomputed_ranges=ranges,
            )
            conn.execute(
                "UPDATE acquisition_state SET last_attempt_ts = %s "
                "WHERE symbol = %s AND granularity = %s AND provider = %s",
                (candidate.last_attempt_ts, symbol, granularity, _PROVIDER),
            )


def _minute_session_ranges(
    conn: psycopg.Connection[Any], symbol: str, window: tuple[datetime, datetime]
) -> list[GapRange]:
    """The window's session as one minute gap range, inside the lifecycle."""
    clamped_from, clamped_to = clamp_to_lifecycle(conn, symbol, *window)
    if clamped_from is None or clamped_to is None:
        return []
    bounds = fetch_session_bounds(conn, symbol, clamped_from, clamped_to)
    return [
        GapRange(symbol, "minute", session_open, session_close)
        for session_open, session_close in sorted(bounds.items())
    ]


def _is_session(conn: psycopg.Connection[Any], day: date) -> bool:
    rows = conn.execute(
        "SELECT calendar_id FROM trading_sessions "
        "WHERE calendar_id = ANY(%s) AND session_date = %s",
        (list(US_EQUITY_CALENDAR_IDS), day),
    ).fetchall()
    if len(rows) not in (0, len(US_EQUITY_CALENDAR_IDS)):
        raise RuntimeError(f"{day}: NYSE and NASDAQ disagree on the session ({rows})")
    return bool(rows)


def gap_rows_over(
    conn: psycopg.Connection[Any], granularity: str, window: tuple[datetime, datetime]
) -> dict[str, int]:
    """Gap rows intersecting ``window`` per US equity symbol (the dry check's
    and the report's count)."""
    rows = conn.execute(
        "SELECT g.symbol, count(*) FROM data_gaps g "
        "JOIN instruments i ON i.symbol = g.symbol "
        "WHERE i.trading_calendar_id = ANY(%s) AND g.granularity = %s "
        "AND g.gap_start <= %s AND g.gap_end >= %s GROUP BY g.symbol",
        (list(US_EQUITY_CALENDAR_IDS), granularity, window[1], window[0]),
    ).fetchall()
    return dict(rows)


def _closed_candidates(
    conn: psycopg.Connection[Any], granularity: str, window: tuple[datetime, datetime]
) -> list[_Candidate]:
    """Symbols with a gap row contained in the closed day, with their state."""
    return _unique(
        conn.execute(
            "SELECT DISTINCT g.symbol, a.last_attempt_outcome, a.last_attempt_ts "
            "FROM data_gaps g "
            "JOIN instruments i ON i.symbol = g.symbol "
            "JOIN acquisition_state a ON a.symbol = g.symbol "
            "  AND a.granularity = g.granularity AND a.provider = %s "
            "WHERE i.trading_calendar_id = ANY(%s) AND g.granularity = %s "
            "AND g.gap_start >= %s AND g.gap_end <= %s",
            (_PROVIDER, list(US_EQUITY_CALENDAR_IDS), granularity, *window),
        ).fetchall()
    )


def _opened_candidates(
    conn: psycopg.Connection[Any], granularity: str, day: date
) -> list[_Candidate]:
    """Acquired symbols with no bar on the new session and no gap row over it."""
    if granularity == "minute":
        has_bar = (
            "SELECT 1 FROM minute_ohlcv b WHERE b.symbol = a.symbol "
            "AND b.time >= ts.session_open_utc AND b.time < ts.session_close_utc"
        )
    else:
        has_bar = (
            "SELECT 1 FROM daily_ohlcv b WHERE b.symbol = a.symbol "
            "AND b.time = ts.session_date::timestamp AT TIME ZONE 'UTC'"
        )
    return _unique(
        conn.execute(
            "SELECT a.symbol, a.last_attempt_outcome, a.last_attempt_ts "
            "FROM acquisition_state a "
            "JOIN instruments i ON i.symbol = a.symbol "
            "JOIN trading_sessions ts "
            "  ON ts.calendar_id = i.trading_calendar_id AND ts.session_date = %s "
            "WHERE i.trading_calendar_id = ANY(%s) AND a.granularity = %s "
            "AND a.provider = %s "
            f"AND NOT EXISTS ({has_bar}) "
            "AND NOT EXISTS (SELECT 1 FROM data_gaps g WHERE g.symbol = a.symbol "
            "  AND g.granularity = a.granularity "
            "  AND g.gap_start <= ts.session_close_utc "
            "  AND g.gap_end >= ts.session_open_utc)",
            (day, list(US_EQUITY_CALENDAR_IDS), granularity, _PROVIDER),
        ).fetchall()
    )


def _unique(rows: list[tuple[Any, ...]]) -> list[_Candidate]:
    """One state per symbol; two rows for one symbol (two instruments sharing
    it) is an error rather than a guess."""
    seen = Counter(row[0] for row in rows)
    doubled = sorted(s for s, n in seen.items() if n > 1)
    if doubled:
        raise RuntimeError(f"several acquisition_state rows for {doubled[:10]}")
    return [_Candidate(*row) for row in rows]
