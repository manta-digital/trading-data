"""
Acquisition state: enums, row dataclass, and repository.

No existing Granularity enum was found in the codebase; this module defines
one alongside LastAttemptOutcome. Both are StrEnums so they compare equal to
their string values in SQL parameterized queries and Python conditionals.

The string values defined here are the canonical source of truth. The SQL
migration records the same values in inline comments and must be kept in sync
if values ever change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from manta_trading.logging import get_logger

_logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Granularity(StrEnum):
    """Time granularity for acquired data.

    SQL migration cross-reference: 770_create_acquisition_state.sql (granularity column comment).
    """

    DAILY = "daily"
    MINUTE = "minute"
    TICK = "tick"


class AcquisitionStatus(StrEnum):
    """Legacy lifecycle status — retained for historic migration SQL only.

    Removed from acquisition_state in migration 019 (slice 142).
    Do not use in new code.
    """

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    OK = "ok"
    FAILED = "failed"
    UNFILLABLE = "unfillable"


class LastAttemptOutcome(StrEnum):
    """Outcome of the most recent fetch attempt for an acquisition_state row.

    Values are stored as TEXT in the DB; the CHECK constraint is derived
    from this enum by _outcome_check_sql() in migrations/minute.py.
    """

    SUCCESS = "success"
    PARTIAL = "partial"
    EMPTY = "empty"
    TRANSIENT_FAILURE = "transient_failure"


# ---------------------------------------------------------------------------
# Row dataclass
# ---------------------------------------------------------------------------


@dataclass
class AcquisitionStateRow:
    """One row from the acquisition_state table.

    Fields mirror table columns after migration 030 (slice 152), which
    dropped ``last_adjusted_ca_snapshot_id`` along with the rest of the
    adjusted-on-write machinery. Nullable columns use ``datetime | None``
    or ``str | None`` as appropriate.
    """

    symbol: str
    granularity: Granularity
    provider: str
    last_attempt_ts: datetime | None = None
    last_attempt_outcome: LastAttemptOutcome | None = None
    updated_at: datetime | None = None


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

# Columns selected in all acquisition_state queries (post-030 shape).
_COLS = (
    "symbol, granularity, provider, last_attempt_ts, last_attempt_outcome, updated_at"
)


def _row_to_state(row: dict) -> AcquisitionStateRow:
    """Map a psycopg3 dict row to an AcquisitionStateRow."""
    raw_outcome = row.get("last_attempt_outcome")
    return AcquisitionStateRow(
        symbol=row["symbol"],
        granularity=Granularity(row["granularity"]),
        provider=row["provider"],
        last_attempt_ts=row.get("last_attempt_ts"),
        last_attempt_outcome=LastAttemptOutcome(raw_outcome) if raw_outcome else None,
        updated_at=row.get("updated_at"),
    )


class AcquisitionStateRepository:
    """Read/write access to the acquisition_state table.

    Uses a psycopg3 ConnectionPool. All SQL is parameterized — never
    string-formatted values into queries.

    Args:
        pool: An open psycopg3 ConnectionPool pointing at the TimescaleDB instance.
    """

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert(self, row: AcquisitionStateRow) -> None:
        """Insert or update an acquisition_state row.

        Uses ``INSERT ... ON CONFLICT (symbol, granularity, provider) DO UPDATE``
        so that ``updated_at`` is always refreshed on every write.
        """
        sql = """
            INSERT INTO acquisition_state (
                symbol, granularity, provider,
                last_attempt_ts, last_attempt_outcome,
                updated_at
            ) VALUES (
                %s, %s, %s,
                %s, %s,
                NOW()
            )
            ON CONFLICT (symbol, granularity, provider) DO UPDATE SET
                last_attempt_ts      = EXCLUDED.last_attempt_ts,
                last_attempt_outcome = EXCLUDED.last_attempt_outcome,
                updated_at           = NOW()
        """
        params = (
            row.symbol,
            str(row.granularity),
            row.provider,
            row.last_attempt_ts,
            str(row.last_attempt_outcome) if row.last_attempt_outcome else None,
        )
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)

    def get(
        self,
        symbol: str,
        granularity: Granularity,
        provider: str,
    ) -> AcquisitionStateRow | None:
        """Fetch a single row by primary key. Returns None if not found."""
        sql = (
            f"SELECT {_COLS} FROM acquisition_state "
            "WHERE symbol = %s AND granularity = %s AND provider = %s"
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, (symbol, str(granularity), provider))
                row = cur.fetchone()

        return _row_to_state(row) if row is not None else None

    def list(
        self,
        *,
        symbol: str | None = None,
        granularity: Granularity | None = None,
        provider: str | None = None,
    ) -> list[AcquisitionStateRow]:
        """List rows with optional AND-combined filters.

        All filter arguments are optional. With no arguments, returns all rows.
        """
        sql = (
            f"SELECT {_COLS} FROM acquisition_state "
            "WHERE (%s::text IS NULL OR symbol = %s) "
            "  AND (%s::text IS NULL OR granularity = %s) "
            "  AND (%s::text IS NULL OR provider = %s) "
            "ORDER BY symbol, granularity, provider"
        )
        granularity_str = str(granularity) if granularity is not None else None
        params = (
            symbol,
            symbol,
            granularity_str,
            granularity_str,
            provider,
            provider,
        )
        with self._pool.connection() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

        return [_row_to_state(r) for r in rows]
