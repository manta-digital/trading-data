"""Cagg freshness assertion for derived-data readers (slice 168).

A TimescaleDB refresh policy only reconsiders the last ``start_offset`` of data,
so any interruption longer than that leaves a hole the policy **never heals** on
resume. Slice 163's Phase D hit this in production: job 1003
(``minute_4hour_ohlcv`` refresh) was paused for restructuring and left paused.
The daemon's coverage index reads that exact cagg, so its leading edge froze
while raw ``minute_ohlcv`` kept growing, and ~349 of 4,198 symbols were
re-seeded every cycle for four days. It was silent — gap rows land under
``ON CONFLICT DO NOTHING``, so nothing errored.

163 added a ``preflight()`` guard covering exactly one path: maintenance tooling
refusing to repair a cagg while the coverage-index cagg's refresh is paused. The
other causes — crashed job, policy failing every fire, out-of-band ``alter_job``,
restart mid-maintenance — never pass through maintenance tooling. This module
puts the check in the **reader** path instead.

Design invariants (slice 168 D1-D6):

- Indeterminate freshness is **stale** (D3). A probe that cannot answer refuses.
- Never auto-remediate (D4) — no ``refresh_continuous_aggregate`` in a read path.
- The threshold is ``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``. The
  ceiling is required: the daily caggs' 21/90/270-day offsets would otherwise
  let a daily cagg stalled 100 days pass every ``start_offset``-relative check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class StalenessSignal(StrEnum):
    """Why a cagg was judged stale. Every dispatch and log site uses these
    members — never a bare string.

    The first four are the D1 signals, OR'd: any one alone is sufficient to
    refuse. The last two are the indeterminate causes (D3/F001), which are
    treated as stale rather than as an exemption.
    """

    LAG_EXCEEDS_THRESHOLD = "LAG_EXCEEDS_THRESHOLD"
    """raw ``max(time)`` minus cagg ``max(time_bucket)`` exceeds the threshold."""

    NOT_SCHEDULED = "NOT_SCHEDULED"
    """The refresh policy exists but is paused (``scheduled = false``) — the
    exact shape of the 163 incident."""

    LAST_SUCCESS_TOO_OLD = "LAST_SUCCESS_TOO_OLD"
    """``now() - last_successful_finish`` exceeds the threshold: the policy is
    scheduled but has not actually completed within the budget."""

    LAST_RUN_FAILED = "LAST_RUN_FAILED"
    """``last_run_status <> 'Success'`` — the policy is firing and failing, which
    a ``scheduled``-only check reports as healthy."""

    NO_JOB_ROW = "NO_JOB_ROW"
    """No refresh policy exists for the view at all. Never self-healing — the
    strongest form of the incident, not an exemption from it."""

    PROBE_FAILED = "PROBE_FAILED"
    """A catalog read or edge probe raised (timeout, connection loss). Freshness
    is indeterminate, and indeterminate is stale (D3)."""


@dataclass(frozen=True)
class FreshnessVerdict:
    """The result of one freshness evaluation for a single cagg view.

    Attributes:
        view_name: The cagg view the verdict describes.
        is_fresh:  True only when no signal fired.
        signals:   Every signal that fired, not just the first — the ERROR log
                   names all of them so an operator sees the full picture.
        lag:       Measured raw-to-cagg lag, or None when it could not be
                   measured (an empty table or a failed probe).
        threshold: The resolved ``min(start_offset, ceiling)`` the lag was
                   judged against, or None when it could not be resolved.
        detail:    Human-readable summary for the ERROR log.
    """

    view_name: str
    is_fresh: bool
    signals: tuple[StalenessSignal, ...]
    lag: timedelta | None
    threshold: timedelta | None
    detail: str


@dataclass(frozen=True)
class _JobRow:
    """One cagg refresh policy, as read from the TimescaleDB job catalog."""

    job_id: int
    scheduled: bool
    start_offset: timedelta | None
    last_run_status: str | None
    last_successful_finish: datetime | None
