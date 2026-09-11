#!/usr/bin/env python
"""Repair the minute sessions truncated since 2026-07-16 (slice 921).

From ``REPAIR_921_WINDOW_START`` the coverage-aware seeder ended every minute
range at the session OPEN, so each session was requested as a one-minute
window and stored a single bar. Those days read as "covered" to the coarse
cagg and are never re-fetched on their own.

This script re-seeds them **through the single writer**: it issues no SQL of
its own against ``data_gaps``, calling ``update_data_gaps`` under the daemon's
advisory lock — the published reset path ``mt data pull --reset`` already
uses. It never calls the provider; the next scheduled pass does the fetching.

Modes
-----
``--check``   Read-only. Prints the before-image counts and exits 0. Safe to
              run against production from any host that has the URL.
``--apply``   Re-seeds. Per symbol, one transaction, committed as it goes, so
              a run that dies partway is resumed by rerunning.
``--verify``  Re-measures and prints the acceptance numbers (Task 7.1).

Usage
-----
    uv run python scripts/repair_921_minute_sessions.py --check
    uv run python scripts/repair_921_minute_sessions.py --apply
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import psycopg

from manta_trading.cli.commands.minute_session_mass import (
    check_minute_session_mass,
    fetch_candidate_sessions,
    fetch_session_mass,
    select_judged_session,
)
from manta_trading.config import Settings
from manta_trading.constants import (
    DAEMON_LOCK_TIMEOUT,
    GRANULARITY_SOURCE,
    HEALTH_MINUTE_SESSION_MIN_SYMBOLS,
    HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS,
    REPAIR_921_WINDOW_START,
    FetchEntryPoint,
    Granularity,
)
from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.acquisition.symbols import iter_active_instruments
from manta_trading.data.gaps.minute_accounting import (
    compute_minute_accounting,
    render_minute_accounting,
    summary_line,
)
from manta_trading.data.gaps.minute_coverage import (
    build_symbol_minute_coverage,
    compute_missing_minute_sessions,
)
from manta_trading.data.gaps.repair_921 import (
    cagg_freshness_for_session,
    CheckReport,
    RepairScanTimeout,
    build_truncated_day_index,
    inspect_symbol,
    repair_window_for,
)
from manta_trading.data.gaps.update_data_gaps import update_data_gaps
from manta_trading.data.locking import advisory_lock
from manta_trading.data.quality.fetch_status import FetchStatus

#: Units that must be idle before --apply writes. An overlapping pass would
#: be writing the same rows under the same lock; the lock makes that safe but
#: the report would be measuring a moving target.
PASS_UNITS = ("mt-minute-pass.service", "mt-daily-pass.service")

EXIT_OK = 0
EXIT_REFUSED = 2

#: Progress cadence for the universe walk.
PROGRESS_EVERY = 250

#: SC3's acceptance bar for the judged session's bar count.
#:
#: Deliberately STRICTER than the health check's computed floor
#: (HEALTH_MINUTE_SESSION_MIN_BARS_PER_MINUTE x session minutes = 975,000 for a
#: regular session). The health floor is the loosest value that still catches a
#: collapse on any ordinary day; this is a one-time cutover bar, and a cutover
#: that only just clears the alarm threshold has not demonstrated the fix.
VERIFY_MIN_SESSION_BARS = 1_000_000

#: Sessions --verify checks for residual truncation (SC3, expected 0).
VERIFY_TRAILING_SESSIONS = 5


def _unit_active(unit: str) -> bool:
    """True when systemd reports the unit active. False when systemd is absent
    (a dev host), which is the honest answer there."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _refuse_if_a_pass_is_running() -> None:
    active = [unit for unit in PASS_UNITS if _unit_active(unit)]
    if active:
        print(
            f"refusing to write: {', '.join(active)} is active. "
            "Stop the pass (or wait for it to finish) and rerun.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_REFUSED)


def _database_url() -> str:
    """The configured database URL. Never read from ambient environment."""
    settings = Settings()
    url = settings.timescale_db_url
    if not url:
        print("MT_TIMESCALE_DB_URL is not configured.", file=sys.stderr)
        raise SystemExit(EXIT_REFUSED)
    return str(url)


def _active_symbols(conn: psycopg.Connection) -> list[str]:
    return [
        row.symbol
        for row in iter_active_instruments(
            conn, ordering="most_stale_first", granularity="minute"
        )
    ]


def run_check(conn: psycopg.Connection, symbols: list[str]) -> CheckReport:
    """Measure the universe without writing anything."""
    report = CheckReport()
    # One grouped query for the whole universe: the per-symbol probe measured
    # 0.5-0.9 s each against production, which is ~2.5 h over 13k symbols.
    print(
        f"  measuring truncated days across {len(symbols):,} symbols ...",
        file=sys.stderr,
    )
    truncated_index = build_truncated_day_index(conn, symbols=symbols)
    for index, symbol in enumerate(symbols, start=1):
        findings = inspect_symbol(conn, symbol, truncated_index=truncated_index)
        report.symbols_scanned += 1
        report.truncated_symbol_days += len(findings.truncated_days)
        report.zero_width_rows += findings.zero_width_rows
        report.session_open_ended_terminal_rows += (
            findings.session_open_ended_terminal_rows
        )
        report.midnight_ended_rows += findings.midnight_ended_rows
        if findings.straddling_gap_start is not None:
            report.straddling_rows += 1
        if findings.needs_repair:
            report.symbols_needing_repair += 1
            report.per_symbol.append(findings)
        if index % PROGRESS_EVERY == 0:
            print(f"  ... {index:,}/{len(symbols):,} symbols", file=sys.stderr)
    return report


def repair_symbol(
    conn: psycopg.Connection,
    symbol: str,
    *,
    now_midnight: datetime,
    truncated_index: dict | None = None,
) -> int:
    """Re-seed one symbol. Returns the gap rows inserted.

    One transaction under the daemon's advisory lock, so an overlapping pass
    blocks rather than interleaving. Every write goes through
    ``update_data_gaps``; this function issues no ``data_gaps`` SQL.
    """
    findings = inspect_symbol(conn, symbol, truncated_index=truncated_index)
    if not findings.needs_repair:
        return 0

    coverage = build_symbol_minute_coverage(conn, symbol)
    if coverage is None:
        # The coverage builder fails safe on a stale cagg or a timeout. Seeding
        # from an untrusted read is exactly what slice 162 exists to prevent.
        print(f"  {symbol}: coverage unavailable — skipped", file=sys.stderr)
        return 0

    window_start = repair_window_for(findings)
    ranges = compute_missing_minute_sessions(
        conn,
        symbol,
        coverage,
        window_start,
        now_midnight,
        uncovered_days=set(findings.truncated_days),
        respect_terminal_rows=False,
    )

    with conn.transaction():
        with advisory_lock(conn, symbol, "minute", timeout=DAEMON_LOCK_TIMEOUT):
            result = update_data_gaps(
                conn,
                symbol,
                "minute",
                window_start,
                now_midnight,
                fetch_status_for_unfilled=FetchStatus.UNKNOWN,
                outcome=LastAttemptOutcome.PARTIAL,
                force_reset_terminal=True,
                precomputed_ranges=ranges,
            )
    return result.gaps_inserted


def run_apply(conn: psycopg.Connection, symbols: list[str]) -> tuple[int, int]:
    """Re-seed the universe. Returns (symbols repaired, rows seeded)."""
    now_midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    repaired = 0
    seeded = 0
    truncated_index = build_truncated_day_index(conn, symbols=symbols)
    for index, symbol in enumerate(symbols, start=1):
        try:
            inserted = repair_symbol(
                conn,
                symbol,
                now_midnight=now_midnight,
                truncated_index=truncated_index,
            )
        except psycopg.errors.LockNotAvailable:
            # A pass holds the lock. Skipping and reporting is correct: the
            # symbol is repaired by the next run, and never written twice.
            print(f"  {symbol}: lock held — skipped", file=sys.stderr)
            continue
        if inserted:
            repaired += 1
            seeded += inserted
        if index % PROGRESS_EVERY == 0:
            print(f"  ... {index:,}/{len(symbols):,} symbols", file=sys.stderr)
    return repaired, seeded


def run_verify(conn: psycopg.Connection, symbols: list[str]) -> bool:
    """Print every SC3/SC7 acceptance number. Returns True when all pass.

    **Reuses the health check's own code path.** The judged-session selector,
    the mass query and the rule function are imported and called, never
    re-implemented: two implementations of one measurement drift — an
    inclusive vs. exclusive close, a stale calendar literal — and the cutover
    would then certify a number ``mt data health`` does not reproduce.
    """
    now = datetime.now(UTC)
    passed = True

    # --- SC3: no truncated symbol-days remain in recent sessions -----------
    recent_start = now - timedelta(days=VERIFY_TRAILING_SESSIONS * 2)
    truncated_index = build_truncated_day_index(
        conn, since=recent_start, symbols=symbols
    )
    active = set(symbols)
    residual = sum(len(days) for sym, days in truncated_index.items() if sym in active)
    ok = residual == 0
    passed = passed and ok
    print(
        f"[{'PASS' if ok else 'FAIL'}] truncated symbol-days in the last "
        f"{VERIFY_TRAILING_SESSIONS} sessions: {residual:,} (bar: 0)"
    )

    # --- SC3/SC7: the judged session's mass, via the health check's code ---
    judged = select_judged_session(
        fetch_candidate_sessions(conn, now=now),
        now=now,
        firing_weekdays=Settings().minute_firing_days,
    )
    if judged is None:
        print("[FAIL] no completed session to judge")
        return False

    # #22: the mass read is the health check's, against a materialized-only
    # cagg refreshed hourly. Bars written since its last refresh are invisible
    # to it, so a verify run straight after a pass must wait, not judge.
    freshness = cagg_freshness_for_session(
        conn,
        GRANULARITY_SOURCE[Granularity.H4],
        judged.session_open_utc,
        judged.session_close_utc,
    )
    if freshness.stale:
        print(
            f"[WAIT] {GRANULARITY_SOURCE[Granularity.H4]} last refreshed "
            f"{freshness.last_refresh}, newest judged-session bar written "
            f"{freshness.newest_bar_written} — the hourly refresh has not caught "
            "up; re-run --verify after it"
        )
        return False

    mass = fetch_session_mass(conn, judged)
    ok = mass.total_bars >= VERIFY_MIN_SESSION_BARS
    passed = passed and ok
    print(
        f"[{'PASS' if ok else 'FAIL'}] judged session "
        f"{judged.session_open_utc:%Y-%m-%d} bars: {mass.total_bars:,} "
        f"(bar: {VERIFY_MIN_SESSION_BARS:,})"
    )

    ok = mass.symbols_meeting_min_bars >= HEALTH_MINUTE_SESSION_MIN_SYMBOLS
    passed = passed and ok
    print(
        f"[{'PASS' if ok else 'FAIL'}] symbols with "
        f"\u2265{HEALTH_MINUTE_SESSION_SYMBOL_MIN_BARS} bars: "
        f"{mass.symbols_meeting_min_bars:,} "
        f"(bar: {HEALTH_MINUTE_SESSION_MIN_SYMBOLS:,})"
    )

    # --- SC7: the health line itself, verbatim from the health check -------
    health_ok, health_detail = check_minute_session_mass(judged, mass)
    passed = passed and health_ok
    print(f"[{'PASS' if health_ok else 'FAIL'}] minute session mass: {health_detail}")

    # --- The pending counts --check reports --------------------------------
    report = run_check(conn, symbols)
    print(report.render())

    # --- The universe, from the calendar (2026-09-11) ----------------------
    # Informational: the size of the remaining work, independent of the gap
    # table, so the number does not move when a row is deleted or reseeded.
    accounting = compute_minute_accounting(conn, now=now)
    print(render_minute_accounting(accounting))
    print(summary_line(accounting))
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only report.")
    mode.add_argument("--apply", action="store_true", help="Re-seed the window.")
    mode.add_argument("--verify", action="store_true", help="Acceptance numbers.")
    parser.add_argument(
        "--symbol",
        action="append",
        help="Limit to these symbols (repeatable). Defaults to the active universe.",
    )
    args = parser.parse_args(argv)

    url = _database_url()
    try:
        return _run(args, url)
    except RepairScanTimeout as exc:
        # An unmeasured universe must never be reported as a clean one.
        print(f"scan refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


def _run(args: argparse.Namespace, url: str) -> int:
    with psycopg.connect(url) as conn:
        # The daemon's connections run in UTC (DB_BULK_SESSION); the coverage
        # index keys days by date_trunc under the session zone, so the repair
        # must read it in the same zone or the two disagree on which day a
        # 00:00 UTC bar belongs to (#22).
        with conn.cursor() as cur:
            cur.execute("SET timezone = 'UTC'")
        symbols = args.symbol or _active_symbols(conn)
        print(f"slice 921 minute-session repair — {len(symbols):,} symbols")
        print(f"window starts {REPAIR_921_WINDOW_START} (rows before it are untouched)")

        if args.verify:
            return EXIT_OK if run_verify(conn, symbols) else EXIT_REFUSED

        if args.check:
            report = run_check(conn, symbols)
            print(report.render())
            return EXIT_OK

        _refuse_if_a_pass_is_running()
        repaired, seeded = run_apply(conn, symbols)
        print(f"symbols repaired  {repaired:,}")
        print(f"gap rows seeded   {seeded:,}")
        print(f"via               {FetchEntryPoint.REFETCH}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
