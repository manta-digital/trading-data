"""The restore drill's lifecycle rules (slice 228 TD1 step 0/8, TD5).

``tmp_path`` stands in for ``/data/restore-test``. Host commands are stubs
that record their calls; the ``rm`` stub removes only inside ``tmp_path``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_228_lifecycle as lc  # noqa: E402


class Host:
    """Stubbed ``run`` and ``sudo_n``: records calls; ``rm`` acts in tmp only."""

    def __init__(self, root: Path, running: set[Path] | None = None) -> None:
        self.root, self.running = root, running or set()
        self.calls: list[list[str]] = []

    def _done(self, args: list[str], code: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, code, "", "")

    def run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        pgdata = Path(args[args.index("-D") + 1])
        if args[1] == "status":
            return self._done(args, 0 if pgdata in self.running else 3)
        self.running.discard(pgdata)
        return self._done(args)

    def sudo_n(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(["sudo", "-n", *args])
        target = Path(args[-1])
        assert target.is_relative_to(self.root)
        shutil.rmtree(target)
        return self._done(args)

    def rm_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:3] == ["sudo", "-n", "rm"]]


@pytest.fixture
def root(tmp_path: Path) -> Path:
    return tmp_path / "restore-test"


@pytest.fixture
def lock(tmp_path: Path) -> Iterator[lc.DrillLock]:
    held = lc.acquire_drill_lock(tmp_path / ".lock")
    yield held
    held.release()


# --- 4.3: lock and marker ------------------------------------------------------------


def test_a_second_acquire_refuses_naming_the_holder(lock: lc.DrillLock) -> None:
    with pytest.raises(lc.DrillError, match=f"pid {os.getpid()}"):
        lc.acquire_drill_lock(lock.path)


def test_the_lock_can_be_taken_again_after_release(tmp_path: Path) -> None:
    lc.acquire_drill_lock(tmp_path / ".lock").release()
    lc.acquire_drill_lock(tmp_path / ".lock").release()


def test_the_marker_is_written_with_stamp_and_pid(root: Path) -> None:
    drill = lc.create_drill_dir("20261007T120000Z", root)
    assert drill.name == "228-drill-20261007T120000Z"
    assert [p.name for p in drill.iterdir()] == [lc.MARKER_NAME]
    marker = json.loads((drill / lc.MARKER_NAME).read_text())
    assert marker == {"stamp": "20261007T120000Z", "pid": os.getpid()}


# --- 4.5: path check and cleanup -----------------------------------------------------


def test_assert_drill_path_rejects_an_unmarked_directory(root: Path) -> None:
    (root / "228-drill-x").mkdir(parents=True)
    with pytest.raises(lc.DrillError, match="not a marked"):
        lc.assert_drill_path(root / "228-drill-x", root)


def test_assert_drill_path_rejects_a_path_outside_the_root(
    root: Path, tmp_path: Path
) -> None:
    root.mkdir()
    with pytest.raises(lc.DrillError, match="is not under"):
        lc.assert_drill_path(tmp_path / "elsewhere", root)


def test_assert_drill_path_rejects_a_dotdot_escape(root: Path) -> None:
    drill = lc.create_drill_dir("a", root)
    with pytest.raises(lc.DrillError, match="is not under"):
        lc.assert_drill_path(drill / ".." / ".." / "outside", root)


def test_assert_drill_path_rejects_a_symlink_escaping_the_root(
    root: Path, tmp_path: Path
) -> None:
    drill = lc.create_drill_dir("a", root)
    (tmp_path / "victim").mkdir()
    (drill / "link").symlink_to(tmp_path / "victim")
    with pytest.raises(lc.DrillError, match="is not under"):
        lc.assert_drill_path(drill / "link", root)


def test_assert_drill_path_accepts_a_path_inside_a_marked_directory(root: Path) -> None:
    drill = lc.create_drill_dir("a", root)
    inside = drill / "restic-restore" / "data"
    inside.mkdir(parents=True)
    assert lc.assert_drill_path(inside, root) == inside.resolve()


@pytest.mark.parametrize("case", ["unmarked", "outside", "dotdot", "inside"])
def test_remove_drill_dir_refuses_without_any_rm(
    root: Path, tmp_path: Path, case: str
) -> None:
    host = Host(tmp_path)
    drill = lc.create_drill_dir("a", root)
    (root / "228-drill-b").mkdir()
    (drill / "sub").mkdir()
    target = {
        "unmarked": root / "228-drill-b",
        "outside": tmp_path,
        "dotdot": drill / ".." / "..",
        "inside": drill / "sub",
    }[case]
    with pytest.raises(lc.DrillError):
        lc.remove_drill_dir(target, host.sudo_n, root)
    assert host.rm_calls() == []
    assert drill.exists()


def test_a_marked_drill_directory_is_stopped_then_removed(
    root: Path, tmp_path: Path
) -> None:
    drill = lc.create_drill_dir("a", root)
    (drill / lc.PGDATA_NAME).mkdir()
    host = Host(tmp_path, running={(drill / lc.PGDATA_NAME).resolve()})
    assert lc.stop_scratch_server(drill, host.run, root) is True
    lc.remove_drill_dir(drill, host.sudo_n, root)
    verbs = [c[1] if c[0] != "sudo" else "rm" for c in host.calls]
    assert verbs == ["status", "stop", "rm"]
    assert "-m" in host.calls[1] and "fast" in host.calls[1] and "60" in host.calls[1]
    assert not drill.exists()


def test_a_failed_removal_names_the_path_left_behind(root: Path) -> None:
    drill = lc.create_drill_dir("a", root)

    def sudo_expired(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1, "", "a password is required")

    with pytest.raises(lc.DrillError, match="left in place"):
        lc.remove_drill_dir(drill, sudo_expired, root)
    assert drill.exists()


# --- 4.7: leftover sweep ------------------------------------------------------------


def test_sweep_stops_and_removes_a_marked_leftover(
    root: Path, tmp_path: Path, lock: lc.DrillLock
) -> None:
    drill = lc.create_drill_dir("old", root)
    (drill / lc.PGDATA_NAME).mkdir()
    host = Host(tmp_path, running={(drill / lc.PGDATA_NAME).resolve()})
    assert lc.sweep_leftovers(lock, host.run, host.sudo_n, root) == [drill]
    assert [c[1] for c in host.calls[:2]] == ["status", "stop"]
    assert not drill.exists()


def test_sweep_refuses_an_unmarked_leftover_without_rm(
    root: Path, tmp_path: Path, lock: lc.DrillLock
) -> None:
    (root / "228-drill-stray").mkdir(parents=True)
    host = Host(tmp_path)
    with pytest.raises(lc.DrillError, match="228-drill-stray"):
        lc.sweep_leftovers(lock, host.run, host.sudo_n, root)
    assert host.rm_calls() == []


def test_sweep_refuses_without_the_lock(root: Path, tmp_path: Path) -> None:
    lc.create_drill_dir("old", root)
    host = Host(tmp_path)
    with pytest.raises(lc.DrillError, match="drill lock"):
        lc.sweep_leftovers(
            lc.DrillLock(tmp_path / ".lock"), host.run, host.sudo_n, root
        )
    assert host.calls == []
