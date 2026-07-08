"""Outcome classification for EODHD HTTP responses.

Implements Decision F from slice-145 design: given an HTTP response and
the requested range, classify the outcome as a LastAttemptOutcome enum
value, then map that to the FetchStatus to assign to unfilled gap rows.

HTTP 4xx (other than 429) raises ProviderResponseError — callers must
not swallow this; it indicates a vendor schema change or our bug and
the daemon lets it propagate to crash the symbol-update.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from manta_trading.data.acquisition.state import LastAttemptOutcome
from manta_trading.data.quality.fetch_status import FetchStatus


class ProviderResponseError(Exception):
    """Raised for HTTP 4xx responses (other than 429).

    Indicates either a vendor schema change or a bug on our side.
    The daemon does not catch this; it propagates and terminates the
    symbol's cycle entry.
    """


# Mapping from LastAttemptOutcome → FetchStatus for unfilled gap rows.
# 'success' maps to None — no unfilled rows; range is fully covered.
_OUTCOME_TO_FETCH_STATUS: dict[LastAttemptOutcome, FetchStatus | None] = {
    LastAttemptOutcome.SUCCESS: None,
    LastAttemptOutcome.PARTIAL: FetchStatus.UNKNOWN,
    LastAttemptOutcome.EMPTY: FetchStatus.PROVIDER_HOLE,
    LastAttemptOutcome.TRANSIENT_FAILURE: FetchStatus.FAILED_RETRYABLE,
}

# Verify the mapping covers every enum value (compile-time check).
assert set(_OUTCOME_TO_FETCH_STATUS) == set(LastAttemptOutcome), (
    "outcome_to_fetch_status mapping is not exhaustive — update after adding "
    "a new LastAttemptOutcome value"
)


def classify_outcome(
    response: Any,
    range_start: datetime,
    range_end: datetime,
) -> LastAttemptOutcome:
    """Classify an EODHD HTTP response into a LastAttemptOutcome.

    Args:
        response:    An httpx.Response (or any object with .status_code,
                     .json(), and optional .request attributes).
        range_start: Requested window start (UTC).
        range_end:   Requested window end (UTC).

    Returns:
        A LastAttemptOutcome enum value.

    Raises:
        ProviderResponseError: For HTTP 4xx responses (other than 429).
        ValueError:            If response.status_code is not a recognized
                               HTTP status class.
    """
    status_code: int = response.status_code

    # HTTP error classes — handle before body inspection
    if status_code == 429 or status_code >= 500:
        return LastAttemptOutcome.TRANSIENT_FAILURE

    if status_code == 404:
        # EODHD uses 404 to indicate no intraday data exists for this symbol.
        # Treat as empty — caller will mark the gap PROVIDER_HOLE.
        return LastAttemptOutcome.EMPTY

    if 400 <= status_code < 500:
        # Other 4xx — vendor change or our bug; raise so it surfaces clearly.
        raise ProviderResponseError(
            f"EODHD returned HTTP {status_code} for range "
            f"{range_start!r}–{range_end!r}. Investigate provider contract."
        )

    if status_code != 200:
        # Unexpected status (1xx, 3xx) — treat as transient
        return LastAttemptOutcome.TRANSIENT_FAILURE

    # HTTP 200 — inspect body
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 — any parse failure is transient
        return LastAttemptOutcome.TRANSIENT_FAILURE

    # EODHD quirk: sometimes returns {"error": "..."} with status 200
    if isinstance(body, dict) and "error" in body:
        return LastAttemptOutcome.TRANSIENT_FAILURE

    if not isinstance(body, list):
        # Unexpected body shape — treat as transient
        return LastAttemptOutcome.TRANSIENT_FAILURE

    if len(body) == 0:
        return LastAttemptOutcome.EMPTY

    # Non-empty list: check if coverage reaches range_end
    # EODHD daily bars have a 'date' field; minute bars have a 'datetime' field.
    # We look for the latest date/datetime in the response and compare to range_end.
    latest_bar_ts = _latest_bar_ts(body, range_end)
    if latest_bar_ts is None:
        # Could not determine coverage — treat as partial
        return LastAttemptOutcome.PARTIAL

    range_end_date = range_end.date()
    if latest_bar_ts.date() >= range_end_date:
        return LastAttemptOutcome.SUCCESS
    else:
        return LastAttemptOutcome.PARTIAL


def outcome_to_fetch_status(outcome: LastAttemptOutcome) -> FetchStatus | None:
    """Map a LastAttemptOutcome to a FetchStatus for unfilled gap rows.

    Returns:
        FetchStatus to assign, or None if the outcome means full coverage
        (success — no gap rows should be inserted).
    """
    return _OUTCOME_TO_FETCH_STATUS[outcome]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _latest_bar_ts(bars: list[dict], range_end: datetime) -> datetime | None:
    """Return the latest timestamp found in a list of bar dicts, or None."""

    latest: datetime | None = None
    for bar in bars:
        ts_str: str | None = bar.get("date") or bar.get("datetime")
        if not ts_str:
            continue
        try:
            ts = _parse_bar_ts(ts_str)
            if latest is None or ts > latest:
                latest = ts
        except (ValueError, TypeError):
            continue
    return latest


def _parse_bar_ts(ts_str: str) -> datetime:
    """Parse a date string (YYYY-MM-DD or ISO datetime) to a UTC datetime."""
    from datetime import date, timezone

    ts_str = ts_str.strip()
    if "T" in ts_str or " " in ts_str:
        from datetime import datetime as _dt
        try:
            return _dt.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    # Plain date YYYY-MM-DD
    from datetime import datetime as _dt
    d = date.fromisoformat(ts_str[:10])
    return _dt(d.year, d.month, d.day, tzinfo=timezone.utc)
