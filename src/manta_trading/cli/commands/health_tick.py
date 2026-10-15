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
from manta_trading.data.tick.manifest_request_reads import (
    NearDeadline,
    units_near_deadline,
)
from manta_trading.firing_schedule import schedule_for

DATABASE_CHECK = "tick database"
RETENTION_CHECK = "tick retention"


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
    ]
