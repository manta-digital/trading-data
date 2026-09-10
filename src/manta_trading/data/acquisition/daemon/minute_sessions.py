"""Judge a minute chunk by the sessions it was meant to collect (slice 921, #22).

``classify_outcome`` compares the latest bar's DATE with the range end's
date. For minute data that is the wrong granularity: EODHD dates the 20:00 ET
after-hours bar of session D-1 as 00:00 UTC on day D, so a response holding
only that spillover bar reads as "reached day D" and the gap row for D's
session is deleted with no session bars behind it. The 2026-09-10 cutover
did exactly this on 4,278 symbols. The judgement here is per SESSION: a
session counts as collected only when the response holds a bar strictly
after its open and at or before its close — the same predicate the repair's
truncated-day index applies to stored bars ("newest bar at or before the
open" = not collected), so the daemon and the repair agree on what a
truncated session is.
"""

from __future__ import annotations

from bisect import bisect_right
from datetime import UTC, datetime

from manta_trading.data.acquisition.state import LastAttemptOutcome

_UTC = UTC


def bar_timestamp(bar: dict) -> datetime | None:
    """UTC timestamp of one EODHD minute bar, or None when it carries none.

    Mirrors ``_bar_to_row``: the epoch ``timestamp`` wins, the ISO
    ``datetime`` string is the fallback, both read as UTC.
    """
    ts_epoch = bar.get("timestamp")
    try:
        if ts_epoch is not None:
            return datetime.fromtimestamp(int(ts_epoch), tz=_UTC)
        return datetime.fromisoformat(bar.get("datetime", "")).replace(tzinfo=_UTC)
    except (ValueError, TypeError, OverflowError):
        # A bar without a readable timestamp cannot satisfy any session;
        # _bar_to_row skips the same bar on insert.
        return None


def sessions_without_bars(
    bars: list[dict],
    session_bounds: dict[datetime, datetime],
    chunk_end: datetime,
) -> list[datetime]:
    """Opens of the sessions in the chunk that the response left empty.

    Only sessions that close at or before ``chunk_end`` are judged — a
    session the chunk cuts through was never asked for in full. A session is
    satisfied by any bar in ``(open, close]``.
    """
    stamps = sorted(ts for ts in (bar_timestamp(b) for b in bars) if ts is not None)
    missing: list[datetime] = []
    for session_open, session_close in sorted(session_bounds.items()):
        if session_close > chunk_end:
            continue
        index = bisect_right(stamps, session_open)
        if index < len(stamps) and stamps[index] <= session_close:
            continue
        missing.append(session_open)
    return missing


def judge_chunk_by_sessions(
    outcome: LastAttemptOutcome,
    bars: list[dict],
    session_bounds: dict[datetime, datetime],
    chunk_end: datetime,
) -> tuple[LastAttemptOutcome, list[datetime]]:
    """Refine ``classify_outcome``'s verdict for a minute chunk.

    Returns the outcome to account and the sessions found empty. A response
    that carried bars is SUCCESS when every judged session holds one, and
    PARTIAL when any does not — regardless of the latest bar's date, which
    is what ``classify_outcome`` looked at. A chunk with no judgeable session
    (a legacy row spanning only non-trading time) keeps the classifier's
    verdict, as does any outcome that carried no bars.
    """
    if outcome not in (LastAttemptOutcome.SUCCESS, LastAttemptOutcome.PARTIAL):
        return outcome, []
    if not bars:
        return outcome, []
    judged = [open_ for open_, close in session_bounds.items() if close <= chunk_end]
    if not judged:
        return outcome, []
    missing = sessions_without_bars(bars, session_bounds, chunk_end)
    if missing:
        return LastAttemptOutcome.PARTIAL, missing
    return LastAttemptOutcome.SUCCESS, []
