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

import errno
import os
import stat
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

from manta_trading.cli.commands.health import HealthCheck, _ago
from manta_trading.constants import (
    BACKUP_ARCHIVE_FLAG,
    BACKUP_HEALTH_LOG,
    BACKUP_ROOTS_SEPARATOR,
    BACKUP_STALE_FLAG,
    HEALTH_BACKUP_CHECKER_QUIET_AFTER,
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


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def _cannot_read(path: Path, exc: OSError) -> str:
    return f"cannot read {path}: {exc.strerror or exc}"


def _read_flag(path: Path) -> str | None:
    try:
        return path.read_text(errors="replace")
    except FileNotFoundError:
        # Absent is the healthy state: the cron removes a flag when it clears.
        return None


def _log_mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        # Absent is judged by check_backup_root: the checker never ran here.
        return None


def read_root(root: str) -> RootObservation:
    """Two flag reads and one stat under ``root``. Never raises ``OSError``."""
    base = Path(root)
    current = base
    try:
        if not stat.S_ISDIR(base.stat().st_mode):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR))
        current = base / BACKUP_ARCHIVE_FLAG
        archive = _read_flag(current)
        current = base / BACKUP_STALE_FLAG
        stale = _read_flag(current)
        current = base / BACKUP_HEALTH_LOG
        mtime = _log_mtime(current)
    except OSError as exc:
        # Swallowing is correct: the error becomes this root's failing verdict
        # (cannot read <path>: <reason>), so it is reported, not lost.
        return RootObservation(root, read_error=_cannot_read(current, exc))
    return RootObservation(root, None, archive, stale, mtime)


def gather_backup(settings: Any, *, now: datetime) -> list[HealthCheck]:
    """One verdict per root in ``MT_BACKUP_ROOTS``; no database access."""
    roots = parse_backup_roots(settings.backup_roots)
    if isinstance(roots, str):
        return [HealthCheck(SETTING_CHECK, False, roots)]
    return [
        check_backup_root(read_root(root), now, HEALTH_BACKUP_CHECKER_QUIET_AFTER)
        for root in roots
    ]


def gather_before_connect(settings: Any, *, now: datetime) -> list[HealthCheck]:
    """The rows read before the primary connection opens (234 TD6, 924 D6).

    Neither the tick rows nor the backup rows touch the primary database, so
    they print even when it is down — during an outage the archive is exactly
    what the operator needs to see.
    """
    from manta_trading.cli.commands.health_tick import gather_tick

    return [*gather_tick(settings, now=now), *gather_backup(settings, now=now)]
