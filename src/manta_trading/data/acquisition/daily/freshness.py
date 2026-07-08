"""
Daily acquisition freshness helpers.

Single source of truth for the staleness thresholds and output-size values
used by the orchestrator. All functions are pure (no I/O) and accept an
optional ``today`` override so tests can fix the reference date without
monkey-patching.
"""

from __future__ import annotations

import datetime
from enum import StrEnum

# ---------------------------------------------------------------------------
# Enums and constants — reference these everywhere; never repeat the literals.
# ---------------------------------------------------------------------------


class OutputSize(StrEnum):
    """AlphaVantage outputsize values.

    COMPACT returns the last 100 trading days; FULL returns up to 20+ years.
    These are the only valid values for fetch_daily_ohlcv's output_size param.
    """

    COMPACT = "compact"
    FULL = "full"


MIN_DAYS: int = 5
"""A symbol is "fresh" if its last success is strictly less than this many
calendar days old (gap < MIN_DAYS → skip re-fetch).

Set to 5 to tolerate weekends and single-day holidays: the last trading day
is at most 3 calendar days ago after a long weekend, so 5 provides margin.

TODO: promote to DaemonConfig / settings so operators can tune without
      a code change (tracked for a future config slice)."""

RECENT_DAYS: int = 100
"""Use "compact" output (last ~100 days) when the gap is within this many
days; use "full" otherwise."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utc_date(ts: datetime.datetime) -> datetime.date:
    """Return the UTC calendar date of a datetime.

    Naive datetimes are assumed to already represent UTC. Timezone-aware
    datetimes are converted to UTC first so the date component reflects the
    UTC calendar day, not the local-tz calendar day.
    """
    if ts.tzinfo is None:
        return ts.date()
    return ts.astimezone(datetime.timezone.utc).date()


def _resolve_output_size(
    last_success_ts: datetime.datetime | None,
    *,
    recent_days: int = RECENT_DAYS,
    today: datetime.date | None = None,
) -> str:
    """Return the AlphaVantage ``outputsize`` value appropriate for the gap.

    Args:
        last_success_ts: UTC datetime of the last successful fetch, or None.
        recent_days: Gap threshold in calendar days (default: RECENT_DAYS).
        today: Reference date for gap calculation; defaults to today in UTC.

    Returns:
        ``"compact"`` if gap ≤ recent_days, ``"full"`` otherwise (or if no
        prior success exists).
    """
    if last_success_ts is None:
        return OutputSize.FULL

    ref = today if today is not None else datetime.datetime.now(
        datetime.timezone.utc
    ).date()
    gap = (ref - _utc_date(last_success_ts)).days
    return OutputSize.COMPACT if gap <= recent_days else OutputSize.FULL


def _is_fresh(
    last_success_ts: datetime.datetime | None,
    *,
    min_days: int = MIN_DAYS,
    today: datetime.date | None = None,
) -> bool:
    """Return True iff *last_success_ts* is within *min_days* of today.

    Used for **status reporting** ("how current is the data we hold?").
    For the daemon work-queue question ("do we need to re-fetch today?"),
    use ``_is_attempt_fresh`` instead — which keys on ``last_attempt_ts``
    so a symbol whose last bar is from 2017 (delisted) but which we
    fetched today is correctly classified as not-needing-re-fetch.

    A symbol is "fresh" when the gap is **strictly less than** ``min_days``
    calendar days — i.e. gap ∈ {0, 1, …, min_days-1}.

    Both sides of the gap computation are UTC calendar dates — mixing a
    tz-aware ``last_success_ts`` with a local-tz "today" silently corrupts
    the gap by up to ±1 day and is the reason fresh symbols can look stale.

    Args:
        last_success_ts: UTC datetime of the last successful fetch, or None.
        min_days: Freshness threshold (default: MIN_DAYS).
        today: Reference date (UTC); defaults to today in UTC.

    Returns:
        True if fresh (should skip), False if stale (should re-fetch).
    """
    if last_success_ts is None:
        return False

    ref = today if today is not None else datetime.datetime.now(
        datetime.timezone.utc
    ).date()
    gap = (ref - _utc_date(last_success_ts)).days
    return gap < min_days


def _is_attempt_fresh(
    last_attempt_ts: datetime.datetime | None,
    *,
    min_days: int = MIN_DAYS,
    today: datetime.date | None = None,
) -> bool:
    """Return True iff *last_attempt_ts* is within *min_days* of today.

    Used by the daemon work-queue and orchestrator skip-if-recent checks
    to answer "do we need to re-fetch this symbol today?". Keying on
    *attempt* rather than *success* is what lets a delisted-but-known
    ticker (last bar 2017, but we tried EODHD this morning and got
    no-new-rows) correctly skip re-fetch on the next cycle. Using
    ``_is_fresh(last_success_ts)`` for this question instead causes
    the daemon to hammer dormant tickers every cycle.

    Args:
        last_attempt_ts: UTC datetime of the last fetch attempt
            (success or failure), or None for never-attempted symbols.
        min_days: Freshness threshold (default: MIN_DAYS).
        today: Reference date (UTC); defaults to today in UTC.

    Returns:
        True if attempted recently enough to skip, False otherwise.
    """
    if last_attempt_ts is None:
        return False

    ref = today if today is not None else datetime.datetime.now(
        datetime.timezone.utc
    ).date()
    gap = (ref - _utc_date(last_attempt_ts)).days
    return gap < min_days
