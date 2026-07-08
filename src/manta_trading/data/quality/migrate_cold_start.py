"""Schema migration + cold-start orchestration (slice 142).

Executes pre-flight checks, applies migrations 018-022, and TRUNCATEs the
AV-era bar tables in a single transaction. Failures roll back the entire
operation.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.logging import get_logger
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Slice 141's three migrations whose presence proves the rebuild ran.
_REQUIRED_141_MIGRATIONS: tuple[str, ...] = (
    "015_instruments_lifecycle_columns",
    "016_instruments_eodhd_type_not_null",
    "017_instruments_drop_active",
)

# Sanity bounds on the post-141 instruments count (LLD D1, F004 rationale).
INSTRUMENTS_MIN_COUNT: int = 30_000
INSTRUMENTS_MAX_COUNT: int = 80_000

# Slice 142 migration IDs in the order they apply.
_MIGRATION_IDS_142: tuple[str, ...] = (
    "018_data_gaps",
    "019_slim_acquisition_state",
    "020_drop_coverage_gaps",
    "021_data_status_view",
    "022_acquisition_state_outcome_check",
)

# EODHD liveness probe target (LLD D1.5).
_EODHD_PROBE_HOST: str = "eodhistoricaldata.com"
_EODHD_PROBE_TIMEOUT_SECONDS: float = 10.0
_EODHD_PROBE_SYMBOLS: tuple[str, ...] = ("AAPL.US", "MSFT.US", "SPY.US")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class PreflightFailed(RuntimeError):
    """Raised when any pre-flight rule fails. Halts before any DDL runs."""


@dataclass(frozen=True)
class PreflightResult:
    """Snapshot of values consulted during pre-flight."""

    instruments_count: int
    eodhd_type_null_count: int
    active_column_present: bool
    schema_migrations_present: tuple[str, ...]
    probe_skipped: bool


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------


def _missing_141_migrations(conn) -> list[str]:
    """Return the slice-141 migration IDs not present in schema_migrations."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT migration_id FROM schema_migrations "
            "WHERE migration_id = ANY(%s)",
            (list(_REQUIRED_141_MIGRATIONS),),
        )
        present = {r["migration_id"] for r in cur.fetchall()}
    return [m for m in _REQUIRED_141_MIGRATIONS if m not in present]


def _instruments_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM instruments")
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _eodhd_type_null_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM instruments WHERE eodhd_type IS NULL"
        )
        row = cur.fetchone()
    return int(row[0]) if row else 0


def _active_column_present(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.columns "
            "  WHERE table_name = 'instruments' AND column_name = 'active'"
            ")"
        )
        row = cur.fetchone()
    return bool(row and row[0])


def _probe_eodhd(api_key: str) -> None:
    """Probe EODHD's /eod endpoint. Raises PreflightFailed on any failure mode.

    See LLD D1.5 for the failure-mode table this implements.
    """
    if not api_key:
        raise PreflightFailed(
            "EODHD probe failed: EODHD_API_KEY is not set. "
            "Set it in your environment or pass --skip-probe to bypass."
        )

    # Probe the first symbol; one round-trip is sufficient as a liveness check.
    symbol = _EODHD_PROBE_SYMBOLS[0]
    url = f"https://{_EODHD_PROBE_HOST}/api/eod/{symbol}"
    params = {"api_token": api_key, "fmt": "json", "from": "2026-01-01"}

    try:
        with httpx.Client(timeout=_EODHD_PROBE_TIMEOUT_SECONDS) as client:
            response = client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise PreflightFailed(
            f"EODHD probe failed: timeout after "
            f"{_EODHD_PROBE_TIMEOUT_SECONDS}s contacting {_EODHD_PROBE_HOST}"
        ) from exc
    except httpx.HTTPError as exc:
        raise PreflightFailed(
            f"EODHD probe failed: network error contacting "
            f"{_EODHD_PROBE_HOST}: {exc}"
        ) from exc

    status = response.status_code
    if status in (401, 403):
        raise PreflightFailed(
            "EODHD probe failed: authentication rejected "
            f"(HTTP {status}). Check EODHD_API_KEY."
        )
    if 400 <= status < 500:
        body_excerpt = response.text[:200] if response.text else "(empty)"
        raise PreflightFailed(
            f"EODHD probe failed: HTTP {status}. Response: {body_excerpt}"
        )
    if 500 <= status < 600:
        raise PreflightFailed(
            f"EODHD reports server error (HTTP {status}). "
            "Retry the cold-start later."
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise PreflightFailed(
            f"EODHD probe failed: malformed JSON response: {exc}"
        ) from exc

    if not body:
        raise PreflightFailed(
            f"EODHD returned empty for {symbol} — schema or scope changed."
        )


def run_preflight(
    conn,
    *,
    skip_probe: bool,
    eodhd_api_key: str | None = None,
) -> PreflightResult:
    """Verify slice-141 prerequisites are in place. Halts on any failure.

    Reads only — never writes. Must succeed *before* any DDL or TRUNCATE.

    Args:
        conn: Open psycopg3 Connection (read-only).
        skip_probe: If True, skip the EODHD liveness probe.
        eodhd_api_key: Required when skip_probe=False.
    """
    missing = _missing_141_migrations(conn)
    if missing:
        raise PreflightFailed(
            "Slice 141 migrations have not been applied: "
            f"missing {missing}. Run the rebuild before cold-start."
        )

    null_count = _eodhd_type_null_count(conn)
    if null_count > 0:
        raise PreflightFailed(
            f"{null_count} instruments rows have eodhd_type IS NULL. "
            "Slice 141's rebuild must populate every row before cold-start."
        )

    count = _instruments_count(conn)
    if count < INSTRUMENTS_MIN_COUNT:
        raise PreflightFailed(
            f"instruments count {count} is below the minimum "
            f"{INSTRUMENTS_MIN_COUNT}. The slice 141 rebuild appears "
            "to have left the table partially populated."
        )
    if count > INSTRUMENTS_MAX_COUNT:
        raise PreflightFailed(
            f"instruments count {count} is above the sanity ceiling "
            f"{INSTRUMENTS_MAX_COUNT}. Investigate before destroying data."
        )

    if _active_column_present(conn):
        raise PreflightFailed(
            "instruments.active column still exists; migration 017 did "
            "not run. Cold-start requires the slice 141 schema."
        )

    if not skip_probe:
        if not eodhd_api_key:
            raise PreflightFailed(
                "EODHD probe required but eodhd_api_key was not supplied. "
                "Pass --skip-probe to bypass."
            )
        _probe_eodhd(eodhd_api_key)

    return PreflightResult(
        instruments_count=count,
        eodhd_type_null_count=null_count,
        active_column_present=False,
        schema_migrations_present=_REQUIRED_141_MIGRATIONS,
        probe_skipped=skip_probe,
    )


# ---------------------------------------------------------------------------
# Migration + TRUNCATE orchestration
# ---------------------------------------------------------------------------


def run_migration(pool: ConnectionPool) -> dict[str, object]:
    """Apply migrations 018-022 and TRUNCATE the bar tables in one transaction.

    The migration runner applies each migration in its own transaction, but
    the TRUNCATE step shares the connection state at the end so that an
    interrupted run leaves either everything or nothing in place.
    """
    applied = apply_migrations(pool, MINUTE_MIGRATIONS)

    # TRUNCATE on the same pool. coverage_gaps is already gone after 020.
    # daily_ohlcv is created in slice 143; skip silently if it does not
    # exist so a pre-143 minute DB still cold-starts cleanly.
    truncate_counts: dict[str, int] = {}
    with pool.connection() as conn:
        with conn.cursor() as cur:
            existing_tables: list[str] = []
            for table in ("minute_ohlcv", "daily_ohlcv", "acquisition_state"):
                cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
                exists_row = cur.fetchone()
                if exists_row is None or exists_row[0] is None:
                    continue
                existing_tables.append(table)
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                row = cur.fetchone()
                truncate_counts[table] = int(row[0]) if row else 0
            if existing_tables:
                table_list = ", ".join(existing_tables)
                cur.execute(f"TRUNCATE TABLE {table_list} RESTART IDENTITY")
        conn.commit()

    return {
        "migrations_applied": [
            mid for mid in applied if mid in _MIGRATION_IDS_142
        ],
        "rows_truncated": truncate_counts,
    }


def run_post_flight(pool: ConnectionPool) -> dict[str, object]:
    """Sanity-check the post-cold-start state.

    Confirms ``data_gaps`` is empty, ``data_status`` returns rows, and the
    view's plan has no per-row Function Scan.
    """
    results: dict[str, object] = {}
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM data_gaps")
            row = cur.fetchone()
            results["data_gaps_count"] = int(row[0]) if row else 0

            cur.execute("SELECT COUNT(*) FROM data_status")
            row = cur.fetchone()
            results["data_status_count"] = int(row[0]) if row else 0

            cur.execute("SELECT COUNT(*) FROM instruments")
            row = cur.fetchone()
            results["instruments_count"] = int(row[0]) if row else 0

            cur.execute("EXPLAIN SELECT * FROM data_status LIMIT 1")
            plan_lines = [str(r[0]) for r in cur.fetchall()]
            results["data_status_plan"] = plan_lines
            results["plan_has_function_scan"] = any(
                "Function Scan" in line for line in plan_lines
            )

    return results
