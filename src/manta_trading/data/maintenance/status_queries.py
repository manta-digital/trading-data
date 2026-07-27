"""DB fetch helpers for the data_status view and data_gaps table (slice 147 T6).

All queries use psycopg row factories; no manual column indexing.
No magic string column names — StatusRow/GapRow field names are the reference.

fetch_all_health_counts uses a lightweight GROUP BY query rather than
fetching all rows and counting in Python, to avoid materializing 114k+ rows
just for the footer aggregate.

**Every read of ``data_status`` here goes through
``data.maintenance.status_coverage`` (slice 167 D6)**, so the coverage caggs
behind ``bars_summary`` are freshness-asserted on the same connection, on every
read. Do not add a direct ``FROM data_status`` query to this module — the
slice-167 tests assert by grep that none exists outside the accessor. Each
fetch has a ``*_with_freshness`` form returning the verdict for callers that
surface staleness, and a thin wrapper for those that do not; the guard runs in
both cases. ``data_gaps`` is a plain table, not cagg-derived, so
``fetch_symbol_gaps`` is deliberately unguarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from manta_trading.cli.rendering.status_table import GapRow, StatusRow
from manta_trading.data.maintenance.status_coverage import (
    DATA_STATUS_RELATION,
    CoverageFreshness,
    query_data_status,
)
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

_logger = get_logger(__name__)


def fetch_status_rows_with_freshness(
    conn: psycopg.Connection[Any],
    *,
    symbol: str | None,
    health_filter: list[str] | None,
    granularity: str | None = None,
) -> tuple[list[StatusRow], CoverageFreshness]:
    """Query data_status with optional filters, plus its coverage freshness.

    Reads through ``status_coverage`` — the single guarded door (slice 167 D6) —
    so the returned rows always arrive with a verdict describing how current the
    coverage behind them is. Callers that surface staleness to an operator want
    this; ``fetch_status_rows`` wraps it for those that do not.

    Applies WHERE clauses for symbol, health, and granularity as AND conditions
    in a single parameterized query.
    """
    from psycopg.rows import dict_row

    conditions: list[str] = []
    params: list[Any] = []

    if symbol is not None:
        conditions.append("symbol = %s")
        params.append(symbol)
    if health_filter is not None:
        conditions.append("health = ANY(%s)")
        params.append(health_filter)
    if granularity is not None:
        conditions.append("granularity = %s")
        params.append(granularity)

    where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    sql = f"""
        SELECT
            symbol,
            granularity,
            health,
            bars_stored,
            first_bar_ts,
            last_bar_ts,
            gap_count,
            last_attempt_ts,
            last_attempt_outcome,
            target_end_ts,
            effective_start
        FROM {DATA_STATUS_RELATION}
        {where_clause}
        ORDER BY symbol, granularity
    """

    rows, freshness = query_data_status(conn, sql, params, row_factory=dict_row)

    return _to_status_rows(rows), freshness


def fetch_status_rows(
    conn: psycopg.Connection[Any],
    *,
    symbol: str | None,
    health_filter: list[str] | None,
    granularity: str | None = None,
) -> list[StatusRow]:
    """Query data_status view with optional filters.

    Thin wrapper over ``fetch_status_rows_with_freshness`` for callers that do
    not surface staleness. The guard still runs — a stale cagg is logged at
    ERROR by the accessor either way; only the verdict is discarded here.
    """
    rows, _ = fetch_status_rows_with_freshness(
        conn,
        symbol=symbol,
        health_filter=health_filter,
        granularity=granularity,
    )
    return rows


def _to_status_rows(rows: list[Any]) -> list[StatusRow]:
    """Map dict rows to StatusRow. Field names are the reference — no indexing."""
    return [
        StatusRow(
            symbol=r["symbol"],
            granularity=r["granularity"],
            health=r["health"],
            bars_stored=r["bars_stored"],
            first_bar_ts=r["first_bar_ts"],
            last_bar_ts=r["last_bar_ts"],
            gap_count=r["gap_count"],
            last_attempt_ts=r["last_attempt_ts"],
            last_attempt_outcome=r["last_attempt_outcome"],
            target_end_ts=r["target_end_ts"],
            effective_start=r["effective_start"],
        )
        for r in rows
    ]


def fetch_symbol_gaps(
    conn: psycopg.Connection[Any],
    symbol: str,
) -> list[GapRow]:
    """Return all data_gaps rows for a symbol, ordered by gap_start ASC."""
    from psycopg.rows import dict_row

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT symbol, granularity, gap_start, gap_end, "
            "       fetch_status, attempt_count, last_attempt_ts "
            "FROM data_gaps "
            "WHERE symbol = %s "
            "ORDER BY gap_start ASC",
            (symbol,),
        )
        rows = cur.fetchall()

    return [
        GapRow(
            symbol=r["symbol"],
            granularity=r["granularity"],
            gap_start=r["gap_start"],
            gap_end=r["gap_end"],
            fetch_status=r["fetch_status"],
            attempt_count=r["attempt_count"],
            last_attempt_ts=r["last_attempt_ts"],
        )
        for r in rows
    ]


def fetch_all_health_counts_with_freshness(
    conn: psycopg.Connection[Any],
) -> tuple[dict[str, int], CoverageFreshness]:
    """Health counts over the full unfiltered view, plus coverage freshness.

    Reads through the guarded accessor (slice 167 D6). Uses a single GROUP BY
    query (cheaper than fetching all rows in Python).
    """
    from psycopg.rows import dict_row

    rows, freshness = query_data_status(
        conn,
        f"SELECT health, COUNT(*)::int AS cnt FROM {DATA_STATUS_RELATION} "
        "GROUP BY health",
        row_factory=dict_row,
    )

    return {r["health"]: r["cnt"] for r in rows}, freshness


def fetch_all_health_counts(
    conn: psycopg.Connection[Any],
) -> dict[str, int]:
    """Return a dict of {health: count} over the full unfiltered data_status view.

    Thin wrapper over ``fetch_all_health_counts_with_freshness``; the guard runs
    regardless, only the verdict is discarded.
    """
    counts, _ = fetch_all_health_counts_with_freshness(conn)
    return counts
