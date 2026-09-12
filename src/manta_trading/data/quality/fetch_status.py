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


OPEN_FETCH_STATUSES: tuple[FetchStatus, ...] = (
    FetchStatus.UNKNOWN,
    FetchStatus.FAILED_RETRYABLE,
)
"""The statuses that mean a gap is still open — we are still asking.

``UNKNOWN`` was never attempted; ``FAILED_RETRYABLE`` was attempted and will
be retried. The other two are terminal: ``PROVIDER_HOLE`` is the provider's
answer that nothing is there, and ``RETRY_EXHAUSTED`` a terminal failure
reported separately as ``has_retry_exhausted``.

Defined once because two places count on it and they sit next to each other
on screen: the ``data_status.gap_count`` predicate rendered into migration
056, and the status footer's "still asking" figure printed directly beneath
that count. If the two ever disagreed the footer would contradict the number
above it (922 re-review F002).
"""
