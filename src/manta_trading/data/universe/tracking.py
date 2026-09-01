"""Index constituent tracking — core logic for slice 161.

Maintains a daily point-in-time snapshot of SP500 membership in
``universe_members``, sourced from the fja05680/sp500 GitHub CSV.

Public API:
  parse_sp500_csv, latest_loaded_date, import_sp500_csv,
  get_active_members, apply_universe_diff,
  UniverseTrackingError
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import TYPE_CHECKING, Any

from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

_logger = get_logger(__name__)

_UNIVERSE_SP500 = "sp500"


class UniverseTrackingError(RuntimeError):
    """Raised for malformed CSV data or unrecoverable tracking failures."""


def parse_sp500_csv(text: str) -> list[tuple[date, set[str]]]:
    """Parse the fja05680/sp500 CSV into an ordered list of (date, symbols) tuples.

    Format: ``date,tickers`` — one row per change date, comma-separated tickers
    in the second field (which itself is a quoted CSV value).

    Returns rows sorted ascending by date. Skips the header row.

    Raises:
        UniverseTrackingError: If the text is empty, the header is wrong, or
            any row has an unparseable date or empty symbol set.
    """
    if not text or not text.strip():
        raise UniverseTrackingError("SP500 CSV is empty")

    reader = csv.reader(io.StringIO(text))
    rows: list[tuple[date, set[str]]] = []

    for i, row in enumerate(reader):
        if i == 0:
            if not row or row[0].strip().lower() != "date":
                raise UniverseTrackingError(f"SP500 CSV header unexpected: {row!r}")
            continue

        if len(row) < 2:
            raise UniverseTrackingError(
                f"SP500 CSV row {i + 1} has fewer than 2 fields: {row!r}"
            )

        try:
            change_date = date.fromisoformat(row[0].strip())
        except ValueError as exc:
            raise UniverseTrackingError(
                f"SP500 CSV row {i + 1}: unparseable date {row[0]!r}"
            ) from exc

        symbols = {s.strip() for s in row[1].split(",") if s.strip()}
        if not symbols:
            raise UniverseTrackingError(
                f"SP500 CSV row {i + 1} ({change_date}): empty symbol set"
            )

        rows.append((change_date, symbols))

    if not rows:
        raise UniverseTrackingError("SP500 CSV contains no data rows")

    rows.sort(key=lambda r: r[0])
    return rows


def latest_loaded_date(conn: "psycopg.Connection[Any]") -> date | None:
    """Return the most recent date for which sp500 data is loaded, or None.

    Considers both added_date and removed_date so that removal-only change
    rows (no new additions) are still counted as applied.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT MAX(GREATEST(added_date, COALESCE(removed_date, '0001-01-01'))) "
            "FROM universe_members WHERE universe_name = %s",
            (_UNIVERSE_SP500,),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def get_active_members(
    conn: "psycopg.Connection[Any]",
    universe_name: str,
) -> set[str]:
    """Return the set of currently active symbols for the given universe."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol FROM universe_members "
            "WHERE universe_name = %s AND removed_date IS NULL",
            (universe_name,),
        )
        return {row[0] for row in cur.fetchall()}


def apply_universe_diff(
    conn: "psycopg.Connection[Any]",
    universe_name: str,
    fetched: set[str],
    as_of_date: date,
) -> tuple[int, int]:
    """Insert additions and mark departures for the given universe snapshot.

    Returns:
        ``(added, removed)`` counts.

    Idempotent: re-running with the same ``as_of_date`` and same symbol set
    inserts nothing new and does not double-close already-closed rows.
    """
    active = get_active_members(conn, universe_name)
    additions = fetched - active
    departures = active - fetched

    with conn.cursor() as cur:
        for symbol in additions:
            cur.execute(
                "INSERT INTO universe_members (universe_name, symbol, added_date) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (universe_name, symbol, added_date) DO NOTHING",
                (universe_name, symbol, as_of_date),
            )
        for symbol in departures:
            cur.execute(
                "UPDATE universe_members "
                "SET removed_date = %s "
                "WHERE universe_name = %s AND symbol = %s AND removed_date IS NULL",
                (as_of_date, universe_name, symbol),
            )
    conn.commit()
    return len(additions), len(departures)


def import_sp500_csv(
    conn: "psycopg.Connection[Any]",
    csv_text: str,
    *,
    on_progress: "Any | None" = None,
) -> tuple[int, int]:
    """Import SP500 constituent history from the fja05680/sp500 CSV.

    Applies each change-date row in chronological order. Rows with dates
    already loaded (date <= latest_loaded_date) are skipped, making this
    call idempotent and safe to re-run as the CSV is updated upstream.

    Args:
        conn: psycopg connection to the trading DB.
        csv_text: Full text of the historical components CSV.
        on_progress: Optional callable(rows_done, rows_total, change_date) for
            progress reporting.

    Returns:
        ``(rows_imported, rows_skipped)`` — rows from the CSV, not symbol counts.
    """
    rows = parse_sp500_csv(csv_text)
    cutoff = latest_loaded_date(conn)

    imported = 0
    skipped = 0

    for i, (change_date, symbols) in enumerate(rows):
        if on_progress:
            on_progress(i, len(rows), change_date)

        if cutoff is not None and change_date <= cutoff:
            skipped += 1
            continue

        active = get_active_members(conn, _UNIVERSE_SP500)
        if not active:
            # First row: seed the full snapshot.
            with conn.cursor() as cur:
                for symbol in symbols:
                    cur.execute(
                        "INSERT INTO universe_members (universe_name, symbol, added_date) "
                        "VALUES (%s, %s, %s) "
                        "ON CONFLICT (universe_name, symbol, added_date) DO NOTHING",
                        (_UNIVERSE_SP500, symbol, change_date),
                    )
            conn.commit()
        else:
            apply_universe_diff(conn, _UNIVERSE_SP500, symbols, change_date)

        _logger.debug("universe sp500: applied change row %s", change_date)
        imported += 1

    return imported, skipped
