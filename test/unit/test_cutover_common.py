"""``cutover_common.migrate`` takes its track (slice 234, Task 7.1).

Every host command is replaced: ``out`` records the argv it is handed and
answers ``migrate status``/``apply`` from a scripted ledger.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import cutover_common as common  # noqa: E402


@pytest.mark.parametrize(
    ("track", "migration"),
    [("kalshi", "kalshi_008_amended_status"), ("minute", "060_pass_kind_tick")],
)
def test_the_track_reaches_status_and_apply(
    monkeypatch: pytest.MonkeyPatch, track: str, migration: str
) -> None:
    calls: list[list[str]] = []
    applied = False

    def fake_out(args: list[str], *, sudo: bool = False) -> str:
        nonlocal applied
        calls.append(args)
        if args[:2] == ["cat", str(common.ENV_FILE)]:
            return ""
        verb = args[args.index("migrate") + 1]
        if verb == "apply":
            applied = True
            return json.dumps({"applied": [migration]})
        pending = [] if applied else [{"id": migration}]
        return json.dumps({"connected": True, "applied": [], "pending": pending})

    monkeypatch.setattr(common, "out", fake_out)
    monkeypatch.setattr(common, "verify_migrate_target", lambda _c, _p: "market")
    monkeypatch.setattr(common.Path, "read_text", lambda _self: "")
    common.migrate(track, migration)
    migrate_calls = [c for c in calls if "migrate" in c]
    assert [c[c.index("migrate") + 1] for c in migrate_calls] == [
        "status",
        "apply",
        "status",
    ]
    for call in migrate_calls:
        assert call[call.index("--track") + 1] == track


def test_an_undefined_migration_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_out(args: list[str], *, sudo: bool = False) -> str:
        if "migrate" not in args:
            return ""
        return json.dumps({"connected": True, "applied": [], "pending": []})

    monkeypatch.setattr(common, "out", fake_out)
    monkeypatch.setattr(common, "verify_migrate_target", lambda _c, _p: "market")
    monkeypatch.setattr(common.Path, "read_text", lambda _self: "")
    with pytest.raises(common.CutoverError, match="does not define"):
        common.migrate("minute", "060_pass_kind_tick")


def test_status_failure_to_connect_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_out(args: list[str], *, sudo: bool = False) -> Any:
        return json.dumps({"connected": False, "error": "boom"})

    monkeypatch.setattr(common, "out", fake_out)
    with pytest.raises(common.CutoverError, match="boom"):
        common._migrate_status("minute")


class _Done:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def _preflight_with(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, by_name: str, listing: str
) -> str:
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy/install-production.sh").write_text("")
    (tmp_path / ".env").write_text("")
    monkeypatch.chdir(tmp_path)
    sha = "a" * 40

    def fake_run(args: list[str], **_k: object) -> _Done:
        if args[:2] == ["git", "rev-parse"]:
            return _Done(sha)
        if args == ["git", "ls-remote", "origin"]:
            return _Done(listing)
        return _Done(by_name)

    monkeypatch.setattr(common, "run", fake_run)
    return common.preflight(sha)


def test_preflight_accepts_a_sha_that_a_remote_ref_points_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listing = f"{'a' * 40}\trefs/heads/234-slice.x\n{'b' * 40}\trefs/heads/main\n"
    assert _preflight_with(monkeypatch, tmp_path, "", listing) == "a" * 40


def test_preflight_refuses_a_sha_no_remote_ref_points_at(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    listing = f"{'b' * 40}\trefs/heads/main\n"
    with pytest.raises(common.CutoverError, match="not on origin"):
        _preflight_with(monkeypatch, tmp_path, "", listing)


def test_journal_cursor_reads_the_whole_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A never-logged unit has no unit-scoped cursor; the global one is read."""
    calls: list[list[str]] = []

    def fake_out(args: list[str], *, sudo: bool = False) -> str:
        calls.append(args)
        return "-- cursor: s=abc;i=1"

    monkeypatch.setattr(common, "out", fake_out)
    assert common.journal_cursor() == "s=abc;i=1"
    assert calls == [["journalctl", "-n", "0", "--show-cursor", "-q"]]
