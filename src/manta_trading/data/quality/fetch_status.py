"""FetchStatus enum — lifecycle states for a data_gaps row."""

from __future__ import annotations

from enum import StrEnum


class FetchStatus(StrEnum):
    """Fetch lifecycle status for a gap in the data_gaps table.

    Values are stored as TEXT in the DB; the CHECK constraint is derived
    from this enum by _fetch_status_check_sql() in migrations/minute.py.
    """

    UNKNOWN = "UNKNOWN"
    PROVIDER_HOLE = "PROVIDER_HOLE"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    RETRY_EXHAUSTED = "RETRY_EXHAUSTED"
