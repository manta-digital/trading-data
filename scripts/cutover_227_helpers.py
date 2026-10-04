"""Pure helpers for the slice 227 cutover (``cutover_227_tick_backup.py``).

No host access here: everything takes text in and gives values back, so the
unit tests exercise exactly what the cutover runs. Constants that name host
facts the cutover and these helpers share live here too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

MAIN = "17/main"
TICK = "17/tick"
CRON_FILE = Path("/etc/cron.d/manta-trading-backup")
CLUSTER_MARKER = "# cluster "
#: Cron job → the script that identifies its line.
JOBS = {
    "health": "backup_health_cron.sh",
    "push": "sync_wal_offsite.sh",
    "metadata": "cron_nightly_metadata.sh",
    "weekly": "cron_weekly_backup.sh",
}
_JOB_LINE = re.compile(r"^(?:\S+\s+){5}(\S+)\s+(.*)$")


class StepFailed(RuntimeError):
    """A step saw something other than what it expected."""


class StepStatus(StrEnum):
    NOT_RUN = "not run"
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass
class Step:
    number: int
    title: str
    expected: str = ""
    seen: list[str] = field(default_factory=list)
    status: StepStatus = StepStatus.NOT_RUN


def _job_lines(lines: list[str]) -> list[str]:
    return [x for x in lines if _JOB_LINE.match(x) and not x.startswith("#")]


def block_lines(text: str, name: str) -> list[str]:
    """Job lines from ``# cluster <name>`` to the next comment line."""
    lines = text.splitlines()
    marker = f"{CLUSTER_MARKER}{name}"
    if marker not in lines:
        raise StepFailed(f"no '{marker}' block in the cron file")
    block: list[str] = []
    for line in lines[lines.index(marker) + 1 :]:
        if line.startswith("#"):
            break
        block.append(line)
    return _job_lines(block)


def block_commands(text: str, name: str) -> dict[str, str]:
    """The commands of one cluster block (user and schedule removed), by job."""
    commands: dict[str, str] = {}
    for line in block_lines(text, name):
        match = _JOB_LINE.match(line)
        if match is None:  # block_lines keeps only job lines
            raise StepFailed(f"not a cron job line: {line}")
        for job, script in JOBS.items():
            if f"/scripts/{script} " in match.group(2):
                commands[job] = match.group(2)
    missing = sorted(set(JOBS) - set(commands))
    if missing:
        raise StepFailed(f"cron block {name} lacks jobs: {missing}")
    return commands


def production_lines(text: str) -> list[str]:
    """The job lines production had before 227: the 17/main block and the
    host block, or every job line of a file without blocks (pre-227)."""
    jobs = _job_lines(text.splitlines())
    if f"{CLUSTER_MARKER}{MAIN}" not in text.splitlines():
        return jobs
    host = [j for j in jobs if j.split()[5] == "root"]
    return block_lines(text, MAIN) + host


@dataclass(frozen=True)
class NewArgs:
    """The values 227 adds to production's lines: its table row's url_key and
    replication_host, and the bucket root its metadata job now names."""

    url_key: str
    replication_host: str | None
    bucket_remote: str


def strip_new_args(line: str, args: NewArgs) -> str:
    """Remove the argument pairs 227 adds to production's lines (TD3)."""
    line = line.replace(f" --url-key {args.url_key}", "")
    if args.replication_host is not None:
        line = line.replace(f" --replication-host {args.replication_host}", "")
    return line.replace(f" --remote {args.bucket_remote} --dest", " --dest")


def production_changes(old: str, new: str, args: NewArgs) -> list[str]:
    """How production's lines differ once both sides lose the new arguments.

    Both sides are stripped: on a re-run the "old" file is already the
    rendered one (review F001).
    """
    before = [strip_new_args(x, args) for x in production_lines(old)]
    after = [strip_new_args(x, args) for x in production_lines(new)]
    if len(before) != len(after):
        return [f"production job count {len(before)} -> {len(after)}"]
    return [f"- {b}\n+ {a}" for b, a in zip(before, after, strict=True) if b != a]


def setup_not_ok(output: str, allowed: tuple[str, ...]) -> list[str]:
    """setup-backup lines that are not OK and match no ``allowed`` pattern.

    A missing lifecycle rule is never counted (TD10: storage, not data).
    """
    bad: list[str] = []
    for line in output.splitlines():
        if line.split(" ", 1)[0] not in ("DRIFT", "MISSING", "PENDING"):
            continue
        if re.match(r"^MISSING \S+ lifecycle ", line):
            continue
        if not any(re.search(pattern, line) for pattern in allowed):
            bad.append(line)
    return bad


def lifecycle_missing(output: str) -> list[str]:
    """The ``MISSING <cluster> lifecycle ...`` lines: B2 console rules to add."""
    return [x for x in output.splitlines() if re.match(r"^MISSING \S+ lifecycle ", x)]


def health_flags(log_line: str) -> tuple[int, int] | None:
    """(archive, stale) from a health log line, or None without a summary."""
    match = re.search(r"FLAGS archive=(\d+) stale=(\d+)", log_line)
    return (int(match.group(1)), int(match.group(2))) if match else None


def render_report(
    steps: list[Step], started: str, outcome: str, notes: list[str]
) -> str:
    """The cutover report: frontmatter, outcome, one section per step."""
    out = [
        "---",
        "docType: notes",
        "slice: backup-coverage-for-tick-archive-and-database",
        "project: trading-data",
        f"dateCreated: {started[:10].replace('-', '')}",
        f"dateUpdated: {started[:10].replace('-', '')}",
        "---",
        "",
        "# Slice 227 cutover report",
        "",
        f"Started {started}. Outcome: **{outcome}**.",
        "",
    ]
    if notes:
        out += ["## B2 console rules to add (TD10)", "", *[f"- {n}" for n in notes], ""]
    for step in steps:
        out += [f"## Step {step.number}: {step.title} — {step.status}", ""]
        out += [f"Expected: {step.expected}", "", "Seen:", "", "```"]
        out += [*step.seen, "```", ""]
    return "\n".join(out)
