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
