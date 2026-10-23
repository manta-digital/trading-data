"""The backup rows of ``mt data health`` (slice 924).

``scripts/backup_health_cron.sh`` runs every half hour per cluster and writes
two flag files under the cluster root — ``ARCHIVE-BROKEN`` and
``BACKUP-STALE`` — plus one line per run to ``backup-health.log``. From
2026-10-16 to 19 both flags sat there unread. This module reads them: one
verdict check per root in ``MT_BACKUP_ROOTS``, failing on a flag, on a checker
that has gone quiet, or on a root it cannot read.

The parsing and judging functions are pure; ``read_root`` is the only I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from manta_trading.cli.commands.health import HealthCheck, _ago
from manta_trading.constants import (
    BACKUP_ARCHIVE_FLAG,
    BACKUP_HEALTH_LOG,
    BACKUP_ROOTS_SEPARATOR,
    BACKUP_STALE_FLAG,
)

#: Name of the single check reported when the setting itself is unusable.
SETTING_CHECK = "backup"
CHECK_PREFIX = "backup "
PROBLEM_SEPARATOR = " | "

FAIL_PREFIX = "FAIL "
FAILED_PREFIX = "FAILED "
DETECTED_PREFIX = "detected:"

#: The class each flag carries in the checker's ``FAILED archive=… stale=…``
#: line. Both flag files hold the whole checker output, so a flag's detail is
#: narrowed to its own class's failures when that line names them.
FLAG_CLASS: dict[str, str] = {
    BACKUP_ARCHIVE_FLAG: "archive",
    BACKUP_STALE_FLAG: "stale",
}

NOT_SET = "MT_BACKUP_ROOTS is not set — no backup cluster is judged"


# ---------------------------------------------------------------------------
# Pure rules
# ---------------------------------------------------------------------------


def parse_backup_roots(raw: str | None) -> list[str] | str:
    """The roots in ``MT_BACKUP_ROOTS``, or an error string.

    Looser than the cluster table (which also requires ``/data/`` and a
    restricted character set): this value is only read here, never rendered
    into a root cron line, so absolute and free of ``..`` is enough.
    """
    entries = [e.strip() for e in (raw or "").split(BACKUP_ROOTS_SEPARATOR)]
    roots = list(dict.fromkeys(e for e in entries if e))
    if not roots:
        return NOT_SET
    for root in roots:
        path = PurePosixPath(root)
        if not path.is_absolute() or ".." in path.parts:
            return (
                f"MT_BACKUP_ROOTS entry {root!r} is not an absolute path "
                "without '..' — no backup cluster is judged"
            )
    return roots


def _failed_names(lines: list[str], flag_class: str | None) -> set[str] | None:
    """Check names of ``flag_class`` from the ``FAILED`` line, if one names any."""
    if flag_class is None:
        return None
    failed = [line for line in lines if line.startswith(FAILED_PREFIX)]
    if not failed:
        return None
    for field in failed[-1].removeprefix(FAILED_PREFIX).split():
        key, _, names = field.partition("=")
        if key == flag_class and names:
            return set(names.split(","))
    return None


def summarise_flag(name: str, text: str) -> str:
    """``<name> since <detected>: <check>: <text>; …`` from a flag file (D2)."""
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    detected = next(
        (
            line.removeprefix(DETECTED_PREFIX).strip()
            for line in lines
            if line.startswith(DETECTED_PREFIX)
        ),
        None,
    )
    head = f"{name} since {detected}" if detected else f"{name} (no detected time)"
    fails = [
        line.removeprefix(FAIL_PREFIX) for line in lines if line.startswith(FAIL_PREFIX)
    ]
    own = _failed_names(lines, FLAG_CLASS.get(name))
    if own is not None:
        fails = [f for f in fails if f.partition(":")[0].strip() in own] or fails
    if fails:
        parts = []
        for fail in fails:
            check, _, rest = fail.partition(":")
            parts.append(f"{check.strip()}: {rest.strip()}" if rest else check.strip())
        return f"{head}: {'; '.join(parts)}"
    return f"{head}: {lines[0] if lines else '(empty)'}"


@dataclass(frozen=True)
class RootObservation:
    """What ``read_root`` found under one root. Flags hold the file's text."""

    root: str
    read_error: str | None = None
    archive_flag: str | None = None
    stale_flag: str | None = None
    log_mtime: datetime | None = None


def _hours(threshold: timedelta) -> str:
    return f"{threshold.total_seconds() / 3600:.0f} h"


def check_backup_root(
    obs: RootObservation, now: datetime, threshold: timedelta
) -> HealthCheck:
    """One verdict per root; every problem found, joined in a fixed order (D1)."""
    name = f"{CHECK_PREFIX}{obs.root}"
    if obs.read_error is not None:
        # Nothing was read, so there is no flag or liveness to judge.
        return HealthCheck(name, False, obs.read_error)
    limit = _hours(threshold)
    problems: list[str] = []
    if obs.archive_flag is not None:
        problems.append(summarise_flag(BACKUP_ARCHIVE_FLAG, obs.archive_flag))
    if obs.stale_flag is not None:
        problems.append(summarise_flag(BACKUP_STALE_FLAG, obs.stale_flag))
    if obs.log_mtime is None:
        problems.append(
            f"no {BACKUP_HEALTH_LOG} — backup_health_cron.sh has never run here"
        )
    else:
        ran = _ago(now, obs.log_mtime)
        if now - obs.log_mtime > threshold:
            problems.append(
                f"checker last ran {ran}; limit {limit} "
                "— backup_health_cron.sh is not running"
            )
    if problems:
        return HealthCheck(name, False, PROBLEM_SEPARATOR.join(problems))
    return HealthCheck(name, True, f"no flags; checker ran {ran}; limit {limit}")
