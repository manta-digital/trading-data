"""The tick rows of ``mt data health`` (slice 234 TD5, TD6).

``tick database`` reports the tick line's state (230's vocabulary) and is
informational in every state: a tick outage prints loudly and never changes
the exit code. Making it count is a PM decision. ``tick retention`` runs only
when the database answered, and fails health while any in-flight unit's
download deadline is inside the warning window, naming the job, the deadline
and what to run.

Reads go through ``cli/tick_composed.read_line`` (its own short-lived
connection), so nothing here shares the primary database's connection, and
these rows print even when the primary is down.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from manta_trading.cli.commands.health import CheckSeverity, HealthCheck
from manta_trading.data.acquisition.pass_runs import PassKind
from manta_trading.data.tick.constants import (
    TICK_RETENTION_WARN_DAYS,
    TICK_RUN_UNIT_COMMAND,
)
from manta_trading.data.tick.family_report import (
    LOAD_COMPLETE_ONLY,
    FamilyStatus,
    ReadProgress,
    StatisticsFamilyStatus,
    futures_completeness,
    unavailable_at,
)
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.manifest_request_reads import (
    NearDeadline,
    units_near_deadline,
)
from manta_trading.data.tick.tick_status import TickSessionStatus
from manta_trading.firing_schedule import schedule_for

DATABASE_CHECK = "tick database"
RETENTION_CHECK = "tick retention"
#: The one row a partway failure of the futures read becomes (305 D5).
COMPLETENESS_CHECK = "futures completeness"
#: Statuses that are a defect a run can fix: a retry or a re-ingest.
_DEFECT_STATUSES = (
    TickSessionStatus.FAILED.value,
    TickSessionStatus.RETRY_EXHAUSTED.value,
)


def retention_window() -> timedelta:
    """How far ahead of a deadline ``tick retention`` fails."""
    if schedule_for(PassKind.TICK).is_manual:
        return timedelta(days=TICK_RETENTION_WARN_DAYS)
    # A timer would make this "before the next firing" (TD5); not built.
    raise AssertionError("tick retention has no window for a timed tick pass")


def check_retention(near: list[NearDeadline], window: timedelta) -> HealthCheck:
    if not near:
        return HealthCheck(
            RETENTION_CHECK,
            True,
            f"no in-flight unit within {window.days} d of its download deadline",
        )
    jobs = ", ".join(
        f"job {n.job_id or '(none)'} ({n.units} units) by "
        f"{n.deadline.astimezone(UTC):%Y-%m-%d %H:%M} UTC"
        for n in near
    )
    return HealthCheck(RETENTION_CHECK, False, f"{jobs} — run {TICK_RUN_UNIT_COMMAND}")


def _counts(report: FamilyStatus) -> str:
    """Each non-zero bucket other than ``complete``, by name."""
    return ", ".join(
        f"{count} {status.replace('_', ' ')}"
        for status, count in report.buckets.items()
        if count and status != TickSessionStatus.COMPLETE.value
    )


def _detail(report: FamilyStatus, findings: int) -> str:
    """One line: complete over sessions, the source edge, caught up or not, then
    the non-zero buckets, the cross-tier counts and the content findings."""
    if report.source_edge is None:
        return "no dataset edge; completeness not judged"
    total = sum(report.buckets.values())
    complete = report.buckets[TickSessionStatus.COMPLETE.value]
    parts = [f"{complete:,}/{total:,} complete to {report.source_edge:%Y-%m-%d}"]
    verdict = "caught up" if report.caught_up else "not caught up"
    parts.append(f"{verdict}: {_counts(report)}" if _counts(report) else verdict)
    parts += [
        f"{x.check} {report.schema}: {x.over} over, {x.no_trades} no_trades"
        for x in report.cross_tier
    ]
    if findings:
        parts.append(f"{findings} content findings")
    if report.load_complete_only:
        parts.append(LOAD_COMPLETE_ONLY)
    return "; ".join(parts)


def family_check(product: str, report: FamilyStatus) -> HealthCheck:
    """One family's row (D5), the first matching rule: ``FAIL`` (counted) for a
    defect a run can fix; ``INFO`` for work not yet done or what the provider
    delivered; else ``OK`` (counted). Pure."""
    name = f"futures {product} {report.schema}"
    findings = len(report.findings) if isinstance(report, StatisticsFamilyStatus) else 0
    detail = _detail(report, findings)
    defect = any(report.buckets[s] for s in _DEFECT_STATUSES) or any(
        x.over for x in report.cross_tier
    )
    if defect:
        return HealthCheck(name, False, detail)
    informational = (
        report.caught_up is not True
        or findings
        or any(x.no_trades for x in report.cross_tier)
    )
    if informational:
        return HealthCheck(name, False, detail, CheckSeverity.INFORMATIONAL)
    return HealthCheck(name, True, detail)


def _futures_rows(settings: Any) -> list[HealthCheck]:
    """One composed read of every product's families: a row per wanted family,
    or one counted ``FAIL`` row if the read stopped partway (indeterminate counts
    as failed, as 168 D3 does for cagg freshness)."""
    from manta_trading.cli.tick_composed import read_line
    from manta_trading.data.tick.line_state import TickUnavailable, unavailable_text
    from manta_trading.data.tick.tick_calendar import calendar_url
    from manta_trading.data.tick.universe import TICK_UNIVERSE

    progress = ReadProgress()

    async def read(conn: Conn) -> dict[str, tuple[FamilyStatus, ...]]:
        return await futures_completeness(
            conn, calendar_url(settings), TICK_UNIVERSE, progress, cross_tier=True
        )

    found = read_line(settings, read)
    if isinstance(found, TickUnavailable):
        failed = unavailable_text(unavailable_at(progress, found))
        return [HealthCheck(COMPLETENESS_CHECK, False, failed)]
    return [
        family_check(product, report)
        for product, reports in found.items()
        for report in reports
        if report.wanted_start is not None  # a tick tier nothing wants has no row
    ]


def gather_tick(settings: Any, *, now: datetime) -> list[HealthCheck]:
    """The tick rows. The only I/O is one composed read."""
    from manta_trading.cli.tick_composed import read_line
    from manta_trading.data.tick.line_state import TickUnavailable, unavailable_text

    window = retention_window()
    near = read_line(settings, lambda conn: units_near_deadline(conn, now, window))
    if isinstance(near, TickUnavailable):
        return [
            HealthCheck(
                DATABASE_CHECK,
                False,
                unavailable_text(near),
                CheckSeverity.INFORMATIONAL,
            )
        ]
    return [
        HealthCheck(DATABASE_CHECK, True, "ok", CheckSeverity.INFORMATIONAL),
        check_retention(near, window),
        *_futures_rows(settings),
    ]
