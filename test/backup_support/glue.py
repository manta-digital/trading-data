"""Stub harness for ``scripts/backup_health_cron.sh`` (slices 920 and 924).

The glue runs with ``check_backup_health.sh`` and ``logger`` stubbed on
``PATH``, so a test chooses what the checker reports and counts journal
transitions, while the flag files and the log are written for real. Shared by
the writer's own tests and the 924 reader's writer-to-reader test.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).parents[2]
GLUE = REPO_ROOT / "scripts" / "backup_health_cron.sh"
INNER_PASS = "PASS archive healthy (mode=on, unarchived_bytes=0)"


def write_stub(bin_dir: Path, name: str, stdout_lines: list[str], rc: int) -> None:
    """An executable ``name`` in ``bin_dir`` that prints the lines and exits rc."""
    body = "#!/usr/bin/env bash\n"
    for line in stdout_lines:
        body += f"echo {line!r}\n"
    body += f"exit {rc}\n"
    stub = bin_dir / name
    stub.write_text(body)
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)


@dataclass
class GlueLayout:
    root: Path
    bin: Path
    env_file: Path
    flag: Path
    stale_flag: Path
    log: Path
    journal: Path

    def stub_checker(self, lines: list[str], rc: int) -> None:
        write_stub(self.bin, "check_backup_health.sh", lines, rc)

    def run(self, *extra: str) -> subprocess.CompletedProcess[str]:
        args = [
            "--env-file", str(self.env_file),
            "--url-key", "MT_TIMESCALE_MAINTENANCE_URL",
            "--pgdata", str(self.root / "pgdata"),
            "--wal-dir", str(self.root / "wal"),
            "--stamp", str(self.root / "stamp"),
            "--stale-after", "180",
            "--system-stamp", str(self.root / "system-stamp"),
            "--base-dir", str(self.root / "base"),
            "--flag", str(self.flag),
            "--stale-flag", str(self.stale_flag),
            "--log", str(self.log),
            *extra,  # last, so a test's own --url-key wins
        ]  # fmt: skip
        env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}")
        return subprocess.run(
            [str(GLUE), *args], capture_output=True, text=True, timeout=60, env=env
        )

    def journal_lines(self) -> list[str]:
        if not self.journal.exists():
            return []
        return self.journal.read_text().splitlines()


def make_glue(root: Path, *, flag: str, stale_flag: str, log: str) -> GlueLayout:
    """Glue writing ``flag``/``stale_flag``/``log`` under ``root``; checker PASS.

    The harness's own files (stub bin, env file, journal) live in a sibling
    directory so ``root`` holds only what the glue writes.
    """
    harness = root.parent / f"{root.name}-harness"
    layout = GlueLayout(
        root=root,
        bin=harness / "bin",
        env_file=harness / "env",
        flag=root / flag,
        stale_flag=root / stale_flag,
        log=root / log,
        journal=harness / "journal",
    )
    root.mkdir(exist_ok=True)
    layout.bin.mkdir(parents=True)
    layout.env_file.write_text(
        'MT_TIMESCALE_MAINTENANCE_URL="postgresql://stub/none"\n'
    )
    # logger(1) stub: append its message to a file so transitions can be counted.
    stub = layout.bin / "logger"
    stub.write_text(
        '#!/usr/bin/env bash\nshift 2\necho "$*" >> ' + repr(str(layout.journal)) + "\n"
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    layout.stub_checker([INNER_PASS, "FLAGS archive=0 stale=0"], 0)
    return layout
