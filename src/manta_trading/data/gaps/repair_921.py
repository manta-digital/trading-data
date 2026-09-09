"""Repair the minute sessions truncated between 2026-07-16 and this slice.

Slice 921 Scope 2 / Decision 3 / SC2. From ``REPAIR_921_WINDOW_START`` the
coverage-aware seeder ended every minute range at the session OPEN, so each
session was requested as a one-minute window and stored one bar. Six weeks of
sessions are present-but-truncated, and because the coarse cagg reports a day
as covered when it holds any bar at all, they read as covered and are never
re-fetched.

The repair writes **no SQL of its own against ``data_gaps``**. It calls
``update_data_gaps`` under the daemon's own advisory lock — the published
reset path ``mt data pull --reset`` already uses — so there is exactly one
writer and one set of semantics. This module holds the measurement and the
planning; ``scripts/repair_921_minute_sessions.py`` is the CLI around it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, cast

from manta_trading.constants import REPAIR_921_WINDOW_START
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

_logger = get_logger(__name__)

_UTC = timezone.utc

#: Gap statuses this repair resets. PROVIDER_HOLE and RETRY_EXHAUSTED rows
#: inside the window are terminal verdicts reached from truncated fetches, so
#: they are as wrong as the UNKNOWN ones and must not stay terminal.
TERMINAL_STATUSES = ("PROVIDER_HOLE", "RETRY_EXHAUSTED")


@dataclass(frozen=True)
class SymbolFindings:
    """What one symbol looks like inside the repair window."""

    symbol: str
    truncated_days: frozenset[date]
    zero_width_rows: int
    session_open_ended_terminal_rows: int
    midnight_ended_rows: int
    straddling_gap_start: datetime | None

    @property
    def needs_repair(self) -> bool:
        return bool(self.truncated_days) or bool(
            self.zero_width_rows
            or self.session_open_ended_terminal_rows
            or self.midnight_ended_rows
        )


@dataclass
class CheckReport:
    """Universe-wide before-image. The SC3 baseline and the issue closeout."""

    symbols_scanned: int = 0
    symbols_needing_repair: int = 0
    truncated_symbol_days: int = 0
    zero_width_rows: int = 0
    session_open_ended_terminal_rows: int = 0
    midnight_ended_rows: int = 0
    straddling_rows: int = 0
    per_symbol: list[SymbolFindings] = field(default_factory=list)

    def render(self) -> str:
        return "\n".join(
            [
                f"repair window starts       {REPAIR_921_WINDOW_START}",
                f"symbols scanned            {self.symbols_scanned:,}",
                f"symbols needing repair     {self.symbols_needing_repair:,}",
                f"truncated symbol-days      {self.truncated_symbol_days:,}",
                f"zero-width gap rows        {self.zero_width_rows:,}",
                f"session-open terminal rows {self.session_open_ended_terminal_rows:,}",
                f"midnight-ended legacy rows {self.midnight_ended_rows:,}",
                f"rows straddling the start  {self.straddling_rows:,}",
            ]
        )


def window_start_utc() -> datetime:
    """``REPAIR_921_WINDOW_START`` as a UTC instant."""
    return datetime(
        REPAIR_921_WINDOW_START.year,
        REPAIR_921_WINDOW_START.month,
        REPAIR_921_WINDOW_START.day,
        tzinfo=_UTC,
    )


def find_truncated_days(
    conn: "psycopg.Connection[object]",
    symbol: str,
    *,
    since: datetime | None = None,
) -> frozenset[date]:
    """Return the symbol's truncated session dates inside the repair window.

    **The truncation signature, defined once.** A symbol-day is truncated when
    the newest bar stored for that session is at or before the session's open:
    ``max(time) <= session_open_utc``. That is exactly what a range ending at
    the open produces — EODHD honors ``to`` precisely, so the request returned
    the opening minute and nothing after it.

    Both ``--check``'s report and ``--apply``'s coverage adjustment call this;
    the predicate is never restated elsewhere.
    """
    start = since or window_start_utc()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts.session_date
              FROM trading_sessions ts
              JOIN instruments i
                ON i.trading_calendar_id = ts.calendar_id
              JOIN LATERAL (
                    SELECT max(m.time) AS newest
                      FROM minute_ohlcv m
                     WHERE m.symbol = i.symbol
                       AND m.time >= ts.session_open_utc
                       AND m.time <  ts.session_close_utc
                   ) AS bars ON TRUE
             WHERE i.symbol = %s
               AND ts.session_open_utc >= %s
               AND ts.session_close_utc <= now()
               AND bars.newest IS NOT NULL
               AND bars.newest <= ts.session_open_utc
             ORDER BY ts.session_date
            """,
            (symbol, start),
        )
        rows = cast("list[tuple[date]]", cur.fetchall())
        return frozenset(session_date for (session_date,) in rows)


def build_truncated_day_index(
    conn: "psycopg.Connection[object]",
    *,
    since: datetime | None = None,
) -> dict[str, frozenset[date]]:
    """Return {symbol: truncated session dates} for the WHOLE universe.

    The per-symbol form (``find_truncated_days``) measured 0.5-0.9 s against
    production because its lateral join probes ``minute_ohlcv`` once per
    symbol; over 13,083 symbols that is ~2.5 hours, and a --check that takes
    hours is one nobody runs. This is the same one-grouped-query-per-universe
    shape ``build_minute_coverage_index`` uses for the same reason.

    Identical signature to ``find_truncated_days``: a symbol-day is truncated
    when the newest bar stored for that session is at or before the session's
    open. The predicate lives in one place — this function and its per-symbol
    sibling both spell it, and ``test_repair_921.py`` asserts they agree.
    """
    start = since or window_start_utc()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.symbol, ts.session_date
              FROM trading_sessions ts
              JOIN instruments i
                ON i.trading_calendar_id = ts.calendar_id
              JOIN minute_ohlcv m
                ON m.symbol = i.symbol
               AND m.time  >= ts.session_open_utc
               AND m.time  <  ts.session_close_utc
             WHERE ts.session_open_utc >= %s
               AND ts.session_close_utc <= now()
             GROUP BY m.symbol, ts.session_date, ts.session_open_utc
            HAVING max(m.time) <= ts.session_open_utc
            """,
            (start,),
        )
        index: dict[str, set[date]] = {}
        rows = cast("list[tuple[str, date]]", cur.fetchall())
        for symbol, session_date in rows:
            index.setdefault(symbol, set()).add(session_date)
    return {symbol: frozenset(days) for symbol, days in index.items()}


def find_straddling_gap_start(
    conn: "psycopg.Connection[object]",
    symbol: str,
    *,
    since: datetime | None = None,
) -> datetime | None:
    """Return the earliest ``gap_start`` of a row straddling the window start.

    ``update_data_gaps`` deletes rows CONTAINED in its window, so a row that
    begins before ``REPAIR_921_WINDOW_START`` and ends inside it survives the
    reset while the seed inserts fresh rows for sessions it already covers —
    overlapping rows, and those sessions fetched twice against the rationed
    quota. Task 6.3's decision is to widen the repair window back to this
    value so the row is contained and therefore replaced.
    """
    start = since or window_start_utc()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT min(gap_start)
              FROM data_gaps
             WHERE symbol = %s
               AND granularity = 'minute'
               AND gap_start < %s
               AND gap_end   >= %s
            """,
            (symbol, start, start),
        )
        row = cast("tuple[datetime | None] | None", cur.fetchone())
    return row[0] if row and row[0] is not None else None


def count_row_shapes(
    conn: "psycopg.Connection[object]",
    symbol: str,
    *,
    since: datetime | None = None,
) -> tuple[int, int, int]:
    """(zero-width, session-open-ended terminal, midnight-ended) row counts.

    All three are signatures of the defect inside the repair window:

    - **zero-width** — ``gap_start = gap_end``, a range that asked for a
      single instant;
    - **session-open-ended terminal** — a PROVIDER_HOLE or RETRY_EXHAUSTED
      verdict reached from a truncated fetch, so the verdict is wrong;
    - **midnight-ended** — the legacy rows that predate the coverage seeder
      but fall inside the window, reset with everything else so the window
      ends in one consistent state.
    """
    start = since or window_start_utc()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                count(*) FILTER (WHERE gap_start = gap_end),
                count(*) FILTER (
                    WHERE fetch_status = ANY(%s)
                      AND EXISTS (
                            SELECT 1
                              FROM trading_sessions ts
                              JOIN instruments i
                                ON i.trading_calendar_id = ts.calendar_id
                             WHERE i.symbol = data_gaps.symbol
                               AND ts.session_open_utc = data_gaps.gap_end
                          )
                ),
                count(*) FILTER (
                    WHERE gap_end = date_trunc('day', gap_end)
                )
              FROM data_gaps
             WHERE symbol = %s
               AND granularity = 'minute'
               AND gap_start >= %s
            """,
            (list(TERMINAL_STATUSES), symbol, start),
        )
        row = cast("tuple[int, int, int] | None", cur.fetchone())
    if row is None:
        return 0, 0, 0
    return int(row[0]), int(row[1]), int(row[2])


def inspect_symbol(
    conn: "psycopg.Connection[object]",
    symbol: str,
    *,
    since: datetime | None = None,
    truncated_index: dict[str, frozenset[date]] | None = None,
) -> SymbolFindings:
    """Measure one symbol. Pure read — this is what ``--check`` reports.

    ``truncated_index`` is the universe-wide result of
    ``build_truncated_day_index``. Pass it when walking many symbols: without
    it this falls back to the per-symbol probe, which costs ~0.5-0.9 s each
    against production and turns a 13k-symbol walk into hours.
    """
    zero_width, terminal, midnight = count_row_shapes(conn, symbol, since=since)
    if truncated_index is None:
        truncated = find_truncated_days(conn, symbol, since=since)
    else:
        truncated = truncated_index.get(symbol, frozenset())
    return SymbolFindings(
        symbol=symbol,
        truncated_days=truncated,
        zero_width_rows=zero_width,
        session_open_ended_terminal_rows=terminal,
        midnight_ended_rows=midnight,
        straddling_gap_start=find_straddling_gap_start(conn, symbol, since=since),
    )


def repair_window_for(findings: SymbolFindings) -> datetime:
    """The window start to use for this symbol (Task 6.3's decision).

    Normally ``REPAIR_921_WINDOW_START``. When a gap row straddles that
    instant, the window is widened back to that row's ``gap_start`` so
    ``update_data_gaps``' containment delete reaches it — otherwise the row
    survives beside freshly-seeded overlapping rows and its sessions are
    fetched twice. The widening is bounded by the row's own extent, never by
    history_start, so provider holes further back are not pulled in.
    """
    default = window_start_utc()
    if findings.straddling_gap_start is None:
        return default
    return min(default, findings.straddling_gap_start)
