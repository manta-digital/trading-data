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
from typing import TYPE_CHECKING, cast

from manta_trading.constants import (
    CAGG_BASE_GRANULARITY,
    CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT,
    GRANULARITY_SOURCE,
    MAX_COVERAGE_SOURCE_STALENESS,
    Granularity,
)
from manta_trading.logging import get_logger

if TYPE_CHECKING:
    import psycopg

logger = get_logger(__name__)

# Reverse of GRANULARITY_SOURCE: view name -> granularity. Built once from the
# single source of truth rather than restated, so adding a granularity there
# extends freshness coverage automatically.
_VIEW_GRANULARITY: dict[str, Granularity] = {
    source: granularity for granularity, source in GRANULARITY_SOURCE.items()
}

# The catalog proc_name identifying a continuous aggregate's refresh policy.
# Same value cagg_repair.py filters on; defined here so this module has no
# import-time dependency on the maintenance sweep.
_PROC_REFRESH = "policy_refresh_continuous_aggregate"

# TimescaleDB stores a refresh policy's start_offset inside jobs.config (jsonb)
# as an interval *string* ("1 day", "270 days", "04:00:00") — there is no
# start_offset column. Casting in SQL means psycopg hands back a timedelta
# rather than this module re-implementing PostgreSQL interval parsing.
_JOB_SQL = (
    "SELECT j.job_id, j.scheduled, "
    "(j.config ->> 'start_offset')::interval AS start_offset, "
    "s.last_run_status, s.last_successful_finish "
    "FROM timescaledb_information.jobs j "
    "LEFT JOIN timescaledb_information.job_stats s USING (job_id) "
    "WHERE j.hypertable_name = %s AND j.proc_name = %s"
)

# TimescaleDB's own spelling of a successful job run in job_stats.
_STATUS_SUCCESS = "Success"


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


def _set_probe_timeout(cur: psycopg.Cursor[object]) -> None:
    """Bound every statement this module issues on the caller's connection.

    ``SET LOCAL`` so the bound is scoped to the caller's transaction and never
    leaks into the reader's own subsequent queries (the coverage-index scan has
    its own, much larger, budget). Called before *every* probe — including the
    paths that early-return — so no query this module issues can run unbounded.
    """
    cur.execute(
        f"SET LOCAL statement_timeout = '{CAGG_FRESHNESS_PROBE_STATEMENT_TIMEOUT}'"
    )


def _read_refresh_job(
    conn: psycopg.Connection[object], view_name: str
) -> _JobRow | None:
    """The view's refresh policy from the job catalog, or None if it has none.

    ``timescaledb_information.jobs.hypertable_name`` carries the *view* name for
    a cagg's refresh policy (the ``cagg_repair.py`` precedent). One round trip
    supplies all four D1 catalog inputs. ``view_name`` is bound, never
    interpolated.

    Returns:
        The parsed row, or None when no refresh policy matches — the caller
        turns that into ``NO_JOB_ROW``.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(_JOB_SQL, (view_name, _PROC_REFRESH))
        row = cast(
            "tuple[int, bool, timedelta | None, str | None, datetime | None] | None",
            cur.fetchone(),
        )
    if row is None:
        return None
    job_id, scheduled, start_offset, last_run_status, last_successful_finish = row
    return _JobRow(
        job_id=int(job_id),
        scheduled=bool(scheduled),
        start_offset=start_offset,
        last_run_status=last_run_status,
        last_successful_finish=last_successful_finish,
    )


def _cagg_max(conn: psycopg.Connection[object], view_name: str) -> datetime | None:
    """The cagg's leading edge: ``max(time_bucket)``, or None if empty."""
    return _max_probe(conn, view_name, "time_bucket")


def _raw_max(conn: psycopg.Connection[object], source_table: str) -> datetime | None:
    """The raw hypertable's leading edge: ``max(time)``, or None if empty."""
    return _max_probe(conn, source_table, "time")


def _max_probe(
    conn: psycopg.Connection[object], relation: str, column: str
) -> datetime | None:
    """One bounded ``max()`` edge probe.

    ``relation`` is never caller input: it arrives already resolved through
    ``GRANULARITY_SOURCE`` (see ``_resolve_source_table``), so the identifier
    cannot be attacker-controlled. Identifiers cannot be bound parameters in
    PostgreSQL, which is why this is interpolated rather than passed as %s.
    """
    with conn.cursor() as cur:
        _set_probe_timeout(cur)
        cur.execute(f"SELECT max({column}) FROM {relation}")  # noqa: S608
        row = cast("tuple[datetime | None] | None", cur.fetchone())
    if row is None:
        return None
    return row[0]


def _resolve_source_table(view_name: str) -> str:
    """The raw hypertable a cagg view derives from.

    Granularity-agnostic by construction (D5): both sides come from
    ``GRANULARITY_SOURCE``, so slice 167 consumes this unchanged for the daily
    caggs. Minute caggs derive from ``minute_ohlcv``, daily caggs from
    ``daily_ohlcv`` — the two base hypertables, which are the entries mapping to
    themselves.

    Raises:
        ValueError: ``view_name`` is not a known cagg view. This is a caller
            bug and must not be absorbed into a staleness refusal (F001).
    """
    granularity = _VIEW_GRANULARITY.get(view_name)
    if granularity is None:
        raise ValueError(
            f"{view_name!r} is not a known continuous aggregate view "
            f"(expected one of {sorted(_VIEW_GRANULARITY)})"
        )
    base = GRANULARITY_SOURCE[CAGG_BASE_GRANULARITY[granularity]]
    if view_name == base:
        raise ValueError(
            f"{view_name!r} is a base hypertable, not a continuous "
            f"aggregate — nothing to assert freshness against"
        )
    return base


def _resolve_threshold(start_offset: timedelta | None) -> timedelta:
    """The staleness budget: ``min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)``.

    The ceiling is load-bearing, not defensive. A refresh policy only reconsiders
    the last ``start_offset`` of data, so ``start_offset`` alone is the natural
    budget — but the daily caggs use 21/90/270-day offsets, which would let a
    daily cagg stalled 100 days pass every ``start_offset``-relative check.

    ``start_offset is None`` (a policy configured without one) falls back to the
    ceiling alone rather than to "no bound".
    """
    if start_offset is None:
        return MAX_COVERAGE_SOURCE_STALENESS
    return min(start_offset, MAX_COVERAGE_SOURCE_STALENESS)
