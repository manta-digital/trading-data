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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from manta_trading.constants import MINUTE_PROVIDER_PUBLICATION_LAG
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


@dataclass(frozen=True)
class SessionJudgement:
    """What a response said about the sessions its chunk covered."""

    outcome: LastAttemptOutcome
    #: Sessions with no bar that closed within the publication lag — ask again.
    unpublished: list[datetime] = field(default_factory=list)
    #: Sessions with no bar that closed longer ago — the provider answered and
    #: had nothing; record a hole so nothing asks again.
    empty: list[datetime] = field(default_factory=list)


def judge_chunk_by_sessions(
    outcome: LastAttemptOutcome,
    bars: list[dict],
    session_bounds: dict[datetime, datetime],
    chunk_end: datetime,
    *,
    now: datetime | None = None,
    publication_lag: timedelta = MINUTE_PROVIDER_PUBLICATION_LAG,
) -> SessionJudgement:
    """Refine ``classify_outcome``'s verdict for a minute chunk.

    A response that carried bars is judged per session. Every judged session
    holding a bar: SUCCESS. A session without one is ``unpublished`` when it
    closed within ``publication_lag`` of ``now`` — the outcome is PARTIAL so
    the row stays open — and ``empty`` otherwise: the provider has had time
    to publish and answered with nothing for it, which is what an illiquid
    name's quiet session looks like (AAA on 2026-09-02 held one after-hours
    bar and no trade). Empty sessions do not hold the outcome back; the
    caller records them as PROVIDER_HOLE so neither the seed nor the repair
    asks for them again.

    A response that carried NO bars (``EMPTY``: an empty body or a 404 — the
    provider answered) is judged the same way: every judged session is
    missing, so the ones past the lag are ``empty`` and the ones inside it
    are ``unpublished``. Before this, a bar-less answer for a long-closed
    session climbed the five-attempt ladder to RETRY_EXHAUSTED at five times
    the credits of a hole recorded once (2026-09-11: 500k such answers on
    2020–2025 sessions). A transient failure carried no answer and passes
    through untouched, as does a chunk with no judgeable session.
    """
    if outcome not in (
        LastAttemptOutcome.SUCCESS,
        LastAttemptOutcome.PARTIAL,
        LastAttemptOutcome.EMPTY,
    ):
        return SessionJudgement(outcome)
    if not bars and outcome is not LastAttemptOutcome.EMPTY:
        return SessionJudgement(outcome)
    judged = [open_ for open_, close in session_bounds.items() if close <= chunk_end]
    if not judged:
        return SessionJudgement(outcome)
    missing = sessions_without_bars(bars, session_bounds, chunk_end)
    if not missing:
        return SessionJudgement(LastAttemptOutcome.SUCCESS)
    moment = now or datetime.now(_UTC)
    unpublished = [s for s in missing if session_bounds[s] + publication_lag > moment]
    empty = [s for s in missing if s not in unpublished]
    if unpublished:
        return SessionJudgement(LastAttemptOutcome.PARTIAL, unpublished, empty)
    return SessionJudgement(LastAttemptOutcome.SUCCESS, [], empty)
