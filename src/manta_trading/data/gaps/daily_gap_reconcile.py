"""Shrink existing daily gap rows to the sessions that truly lack a bar (#83).

Before the #83 fix, daily gap detection reported every session in its window as
missing, so production holds daily gap rows spanning years of sessions whose
bars are stored. Rewriting those windows through ``update_data_gaps`` would
reset terminal rows (``RETRY_EXHAUSTED``, ``PROVIDER_HOLE``) to ``UNKNOWN`` and
re-ask the provider for years of history. This module does the opposite: each
row is replaced by the sub-ranges of its own window that really lack a bar,
and every sub-range keeps the row's ``fetch_status``, ``attempt_count`` and
``last_attempt_ts``. A row whose sessions are all held is deleted. A second
call changes nothing.

A symbol with no lifecycle anchor (no instrument row, no listing or data date)
cannot be judged; its rows are left as found and counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from manta_trading.constants import DAEMON_LOCK_TIMEOUT
from manta_trading.data.gaps.compute_missing_ranges import (
    GapRange,
    clamp_to_lifecycle,
    compute_missing_ranges,
)
from manta_trading.data.locking import advisory_lock

if TYPE_CHECKING:
    import psycopg

_DAILY: Final = "daily"


@dataclass
class DailyReconcileReport:
    """Row counts by what happened to them, and the symbols that changed."""

    unchanged: int = 0
    deleted: int = 0
    shrunk: int = 0  # replaced by one narrower range
    split: int = 0  # replaced by several ranges
    skipped_no_lifecycle: int = 0
    symbols_changed: int = 0
    rows_before: int = 0
    rows_after: int = 0


@dataclass(frozen=True)
class _GapRow:
    start: datetime
    end: datetime
    fetch_status: str
    last_attempt_ts: datetime | None
    attempt_count: int


def reconcile_daily_gaps(
    conn: psycopg.Connection[Any], *, apply: bool
) -> DailyReconcileReport:
    """Reconcile every daily gap row; with ``apply=False`` only count the change.

    Each symbol is written in its own transaction under the daemons' advisory
    lock, so it can run beside them. ``conn`` must not be inside a transaction.
    """
    report = DailyReconcileReport()
    symbols = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT symbol FROM data_gaps WHERE granularity = %s "
            "ORDER BY symbol",
            (_DAILY,),
        ).fetchall()
    ]
    for symbol in symbols:
        if apply:
            with conn.transaction():
                with advisory_lock(conn, symbol, _DAILY, timeout=DAEMON_LOCK_TIMEOUT):
                    _reconcile_symbol(conn, symbol, report, apply=True)
        else:
            _reconcile_symbol(conn, symbol, report, apply=False)
    return report


def _reconcile_symbol(
    conn: psycopg.Connection[Any],
    symbol: str,
    report: DailyReconcileReport,
    *,
    apply: bool,
) -> None:
    rows = _gap_rows(conn, symbol)
    changed = False
    for row in rows:
        report.rows_before += 1
        if clamp_to_lifecycle(conn, symbol, row.start, row.end)[0] is None:
            report.skipped_no_lifecycle += 1
            report.rows_after += 1
            continue
        ranges = compute_missing_ranges(conn, symbol, _DAILY, row.start, row.end)
        if [(r.gap_start_utc, r.gap_end_utc) for r in ranges] == [(row.start, row.end)]:
            report.unchanged += 1
            report.rows_after += 1
            continue
        changed = True
        report.rows_after += len(ranges)
        if not ranges:
            report.deleted += 1
        elif len(ranges) == 1:
            report.shrunk += 1
        else:
            report.split += 1
        if apply:
            _replace(conn, symbol, row, ranges)
    if changed:
        report.symbols_changed += 1


def _gap_rows(conn: psycopg.Connection[Any], symbol: str) -> list[_GapRow]:
    return [
        _GapRow(*row)
        for row in conn.execute(
            "SELECT gap_start, gap_end, fetch_status, last_attempt_ts, attempt_count "
            "FROM data_gaps WHERE symbol = %s AND granularity = %s "
            "ORDER BY gap_start, gap_end",
            (symbol, _DAILY),
        ).fetchall()
    ]


def _replace(
    conn: psycopg.Connection[Any],
    symbol: str,
    row: _GapRow,
    ranges: list[GapRange],
) -> None:
    """Delete the row and insert its sub-ranges with the row's own state.

    A sub-range that already exists as a row keeps that row (DO NOTHING): its
    own state is the more specific record.
    """
    conn.execute(
        "DELETE FROM data_gaps WHERE symbol = %s AND granularity = %s "
        "AND gap_start = %s AND gap_end = %s",
        (symbol, _DAILY, row.start, row.end),
    )
    for rng in ranges:
        conn.execute(
            "INSERT INTO data_gaps (symbol, granularity, gap_start, gap_end, "
            "fetch_status, last_attempt_ts, attempt_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (symbol, granularity, gap_start, gap_end) DO NOTHING",
            (
                symbol,
                _DAILY,
                rng.gap_start_utc,
                rng.gap_end_utc,
                row.fetch_status,
                row.last_attempt_ts,
                row.attempt_count,
            ),
        )
