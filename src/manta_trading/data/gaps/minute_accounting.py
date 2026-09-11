"""The minute universe, accounted from the calendar (2026-09-11).

Every earlier "how much is left" number was read from ``data_gaps``, and the
gap table has never tracked everything: the pre-#22 deletion bug removed
rows all the way back, 880 index instruments were counted as symbols, and
symbols nothing had ever seeded had no rows to count. This accounting does
not consult the gap table for the size of the work. It starts from what
SHOULD exist — every active non-index symbol × every session of its calendar
from its listing (or the calendar's start) to the last closed session — and
classifies each symbol-session by what the database holds:

- **covered**: a bar in one of the session's 4-hour aggregate buckets;
- **no_trade**: not covered, and the daily bar shows zero volume or is
  absent — nothing traded, so no minute bars exist anywhere;
- the remainder traded (daily volume > 0) but has no minute bars, split by
  the gap table's word on it: **hole** (PROVIDER_HOLE), **exhausted**
  (RETRY_EXHAUSTED), **unknown** (UNKNOWN), or **untracked** (no row at all).

``expected - covered`` is the hole in the universe; ``hole + exhausted +
unknown + untracked`` is the part any provider could still fill.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from manta_trading.constants import HEALTH_MINUTE_SESSION_CALENDAR
from manta_trading.data.universe.eodhd_classification import EodhdType

#: The 4-hour aggregate buckets (UTC hour they start) that a regular
#: 13:30–20:00 UTC session lands in. A symbol-session is covered when any
#: bar sits in either bucket on the session's UTC day; early closes (17:00,
#: 18:00 UTC) fall in the first.
_SESSION_BUCKET_HOURS_UTC: tuple[int, ...] = (12, 16)

_COVERAGE_SOURCE = "minute_4hour_ohlcv"


@dataclass(frozen=True)
class MinuteAccountingRow:
    """One year of the accounting, or the total when ``year`` is None."""

    year: int | None
    expected: int
    covered: int
    no_trade: int
    hole: int
    exhausted: int
    unknown: int
    untracked: int

    @property
    def missing(self) -> int:
        return self.expected - self.covered

    @property
    def fillable(self) -> int:
        """Traded sessions with no minute bars — what a provider could still fill."""
        return self.hole + self.exhausted + self.unknown + self.untracked

    @property
    def coverage_pct(self) -> float:
        return 100.0 * self.covered / self.expected if self.expected else 0.0


_ACCOUNTING_SQL = f"""
WITH act AS MATERIALIZED (
    SELECT i.symbol,
           COALESCE(i.trading_calendar_id, %(calendar)s) AS cal,
           GREATEST(%(since)s::date,
                    COALESCE(i.first_listing_date, i.first_data_date, %(since)s::date))
               AS floor_d
      FROM instruments i
     WHERE i.delisted_date IS NULL
       AND COALESCE(i.eodhd_type, '') <> %(index_type)s
),
expected AS MATERIALIZED (
    SELECT a.symbol, s.session_date AS d
      FROM act a
      JOIN trading_sessions s
        ON s.calendar_id = a.cal
       AND s.session_date >= a.floor_d
       AND s.session_close_utc <= %(now)s
),
covered AS MATERIALIZED (
    SELECT c.symbol, (c.time_bucket AT TIME ZONE 'UTC')::date AS d
      FROM {_COVERAGE_SOURCE} c
      JOIN act a ON a.symbol = c.symbol
     WHERE EXTRACT(HOUR FROM (c.time_bucket AT TIME ZONE 'UTC')) = ANY(%(hours)s)
       AND c.time_bucket >= %(since)s::date - INTERVAL '1 day'
     GROUP BY 1, 2
),
gapsess AS MATERIALIZED (
    SELECT g.symbol, s.session_date AS d, MAX(g.fetch_status) AS st
      FROM data_gaps g
      JOIN act a ON a.symbol = g.symbol
      JOIN trading_sessions s
        ON s.calendar_id = a.cal
       AND s.session_open_utc >= g.gap_start
       AND s.session_close_utc <= g.gap_end
     WHERE g.granularity = 'minute'
     GROUP BY 1, 2
),
dly AS MATERIALIZED (
    SELECT d.symbol, (d.time AT TIME ZONE 'UTC')::date AS d, d.volume
      FROM daily_ohlcv d
      JOIN act a ON a.symbol = d.symbol
     WHERE d.time >= %(since)s::date - INTERVAL '1 day'
),
judged AS (
    SELECT e.d,
           c.symbol IS NOT NULL AS covered,
           (y.symbol IS NOT NULL AND y.volume > 0) AS traded,
           g.st
      FROM expected e
      LEFT JOIN covered c ON c.symbol = e.symbol AND c.d = e.d
      LEFT JOIN gapsess g ON g.symbol = e.symbol AND g.d = e.d
      LEFT JOIN dly y ON y.symbol = e.symbol AND y.d = e.d
)
SELECT EXTRACT(YEAR FROM d)::int AS year,
       COUNT(*) AS expected,
       COUNT(*) FILTER (WHERE covered) AS covered,
       COUNT(*) FILTER (WHERE NOT covered AND NOT traded) AS no_trade,
       COUNT(*) FILTER (WHERE NOT covered AND traded AND st = 'PROVIDER_HOLE') AS hole,
       COUNT(*) FILTER (WHERE NOT covered AND traded AND st = 'RETRY_EXHAUSTED')
           AS exhausted,
       COUNT(*) FILTER (WHERE NOT covered AND traded AND st = 'UNKNOWN') AS unknown,
       COUNT(*) FILTER (WHERE NOT covered AND traded AND st IS NULL) AS untracked
  FROM judged
 GROUP BY 1
 ORDER BY 1
"""


def calendar_start(conn: psycopg.Connection[Any], calendar: str) -> datetime | None:
    """The first session the calendar table knows — the accounting's start."""
    row = conn.execute(
        "SELECT MIN(session_open_utc) FROM trading_sessions WHERE calendar_id = %s",
        (calendar,),
    ).fetchone()
    return row[0] if row else None


def compute_minute_accounting(
    conn: psycopg.Connection[Any],
    *,
    now: datetime,
    calendar: str = HEALTH_MINUTE_SESSION_CALENDAR,
) -> list[MinuteAccountingRow]:
    """Per-year rows followed by the total. Empty when the calendar is empty.

    Reads the calendar, ``instruments``, the 4-hour aggregate, ``data_gaps``
    and ``daily_ohlcv``; several minutes against the production universe, so
    this is a report, not a per-symbol check.
    """
    start = calendar_start(conn, calendar)
    if start is None:
        return []
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            _ACCOUNTING_SQL,
            {
                "calendar": calendar,
                "since": start.date(),
                "now": now,
                "hours": list(_SESSION_BUCKET_HOURS_UTC),
                "index_type": EodhdType.INDEX.value,
            },
        )
        years = [MinuteAccountingRow(**row) for row in cur.fetchall()]
    if not years:
        return []
    total = MinuteAccountingRow(
        year=None,
        expected=sum(r.expected for r in years),
        covered=sum(r.covered for r in years),
        no_trade=sum(r.no_trade for r in years),
        hole=sum(r.hole for r in years),
        exhausted=sum(r.exhausted for r in years),
        unknown=sum(r.unknown for r in years),
        untracked=sum(r.untracked for r in years),
    )
    return [*years, total]


def render_minute_accounting(rows: list[MinuteAccountingRow]) -> str:
    """A fixed-width table, the total last; one line when nothing is known."""
    if not rows:
        return "minute accounting: the trading calendar is empty — nothing to account"
    header = (
        f"{'year':>5} {'expected':>11} {'covered':>11} {'cov%':>6} "
        f"{'no_trade':>9} {'hole':>9} {'exhausted':>9} {'unknown':>9} {'untracked':>9}"
    )
    lines = [header]
    for r in rows:
        label = "total" if r.year is None else str(r.year)
        lines.append(
            f"{label:>5} {r.expected:>11,} {r.covered:>11,} {r.coverage_pct:>6.1f} "
            f"{r.no_trade:>9,} {r.hole:>9,} {r.exhausted:>9,} {r.unknown:>9,} "
            f"{r.untracked:>9,}"
        )
    return "\n".join(lines)


def summary_line(rows: list[MinuteAccountingRow]) -> str:
    """The one number: ``expected``, ``covered``, and what could still be filled."""
    if not rows:
        return "minute accounting: no calendar"
    t = rows[-1]
    return (
        f"minute universe: {t.covered:,}/{t.expected:,} symbol-sessions covered "
        f"({t.coverage_pct:.1f}%); {t.no_trade:,} untraded; "
        f"{t.fillable:,} fillable (hole {t.hole:,}, unknown {t.unknown:,}, "
        f"untracked {t.untracked:,}, exhausted {t.exhausted:,})"
    )
