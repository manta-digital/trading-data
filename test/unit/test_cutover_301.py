"""Tests for the slice 301 cutover's own logic (``scripts/cutover_301_*.py``).

Only what the real run would not exercise cleanly: the first-session check,
and timer release after a failed verify versus a failed migrate. Host calls
are replaced on the loaded module.
"""

from __future__ import annotations

import importlib
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


@pytest.fixture
def cutover() -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module("cutover_301_calendar_history")


def _sessions(*days: str) -> str:
    return "log line\n" + json.dumps([{"session_date": d} for d in days])


def test_the_first_expected_session_is_the_monday_after_the_dataset_start(
    cutover: ModuleType,
) -> None:
    assert cutover.first_session_expected() == date(2010, 6, 7)


def test_the_first_session_check_passes_on_the_expected_date(
    cutover: ModuleType,
) -> None:
    raw = _sessions("2010-06-07", "2010-06-08")
    assert cutover.check_first_session("CME_EQUITY", raw) == date(2010, 6, 7)


@pytest.mark.parametrize("raw", ["log only", "[]", '[{"session_date": "2020-01-02"}]'])
def test_the_first_session_check_fails_otherwise(cutover: ModuleType, raw: str) -> None:
    with pytest.raises(cutover.CutoverError):
        cutover.check_first_session("CME_METALS", raw)


def _host(
    cutover: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    failing: str,
) -> list[list[str]]:
    released: list[list[str]] = []
    held = ["mt-minute.timer"]

    def fail(*_a: Any, **_k: Any) -> None:
        raise cutover.CutoverError(f"{failing} failed")

    monkeypatch.setattr(cutover, "NOTES_DIR", tmp_path)
    monkeypatch.setattr(cutover, "say", lambda _text: None)
    monkeypatch.setattr(cutover, "preflight", lambda _ref: "c" * 40)
    monkeypatch.setattr(cutover, "hold_timers", lambda: held)
    monkeypatch.setattr(cutover, "release_timers", released.append)
    monkeypatch.setattr(cutover, "install", lambda _ref, _commit: "mt 1.2.3")
    monkeypatch.setattr(cutover, "restart_serve_if_active", lambda: None)
    monkeypatch.setattr(cutover, "unit_active", lambda _unit: True)
    monkeypatch.setattr(cutover, "migrate", fail if failing == "migrate" else print)
    monkeypatch.setattr(
        cutover,
        "verify_first_sessions",
        fail if failing == "verify" else (lambda: None),
    )
    return released


def test_timers_are_released_after_a_failed_verify(
    cutover: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    released = _host(cutover, monkeypatch, tmp_path, failing="verify")
    assert cutover.main(["cutover", "v1.2.3"]) == 1
    assert released == [["mt-minute.timer"]]


def test_timers_stay_held_after_a_failed_migrate(
    cutover: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    released = _host(cutover, monkeypatch, tmp_path, failing="migrate")
    assert cutover.main(["cutover", "v1.2.3"]) == 1
    assert released == []


def test_help_prints_the_steps_and_touches_nothing(
    cutover: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cutover.main(["cutover", "--help"]) == 0
    assert "Steps" in capsys.readouterr().out
