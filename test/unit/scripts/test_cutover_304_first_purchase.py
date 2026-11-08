"""``scripts/cutover_304_first_purchase.py`` and ``cutover_304_evidence.py``.

Host calls (``mt-run``, the installer, the timers) are replaced on the loaded
modules; nothing here touches production.
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
D = Decimal


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(name)


@pytest.fixture(scope="module")
def fp() -> ModuleType:
    return _load("cutover_304_first_purchase")


@pytest.fixture(scope="module")
def ev() -> ModuleType:
    return _load("cutover_304_evidence")


@pytest.fixture(scope="module")
def cutover_error() -> type[Exception]:
    error: type[Exception] = _load("cutover_common").CutoverError
    return error


ENV = "MT_TICK_SPEND_CEILING_USD=7\nMT_TICK_SPEND_30D_CEILING_USD=16\n"
NOTHING = {
    "wanted_days": {},
    "requests": [],
    "planned_usd": "0",
    "trailing_30d_usd": "0",
    "per_pass_ceiling_usd": "7",
    "cap_30d_usd": "16",
    "verdict": "nothing_to_buy",
    "reasons": [],
}
BASELINE = {"health_exit": 0, "definitions": {"ES": 10, "GC": 5}}


@pytest.fixture
def notes(fp: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fp, "NOTES_DIR", tmp_path)
    monkeypatch.setattr(fp, "START_FILE", tmp_path / "start.txt")
    monkeypatch.setattr(fp, "BASELINE_FILE", tmp_path / "baseline.json")
    return tmp_path


# -- 5.2: the steps ------------------------------------------------------------


def test_a_failed_install_still_releases_the_timers(
    fp: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    released: list[list[str]] = []

    def hold(held: list[str]) -> None:
        held.append("mt-tick.timer")

    def failing_install(*_: object) -> str:
        raise cutover_error("installer failed")

    monkeypatch.setattr(fp, "hold_timers", hold)
    monkeypatch.setattr(fp, "install", failing_install)
    monkeypatch.setattr(fp, "release_timers", lambda held: released.append(list(held)))
    with pytest.raises(cutover_error, match="installer failed"):
        fp.install_release("v0.27.0", "abc")
    assert released == [["mt-tick.timer"]]


def test_a_rerun_keeps_the_first_start_and_baselines(
    fp: ModuleType, ev: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def health() -> int:
        calls.append("h")
        return 0

    def definitions() -> dict[str, int]:
        calls.append("d")
        return {"ES": 1}

    monkeypatch.setattr(ev, "health_exit_code", health)
    monkeypatch.setattr(ev, "definition_counts", definitions)
    first = fp.record_start_and_baselines()
    second = fp.record_start_and_baselines()
    assert first == second
    assert calls == ["h", "d"]


def _drive(
    fp: ModuleType, ev: ModuleType, monkeypatch: pytest.MonkeyPatch, summary: Any
) -> list[str]:
    """Fake every host call of ``_steps``; return the calls made, in order."""
    calls: list[str] = []
    monkeypatch.setattr(fp, "preflight", lambda ref: "c" * 40)
    monkeypatch.setattr(fp, "read_env_text", lambda: ENV)
    monkeypatch.setattr(fp, "check_checkout_database", lambda checkout, prod: "tick")
    monkeypatch.setattr(fp, "read_checkout_env_text", lambda: "")
    monkeypatch.setattr(
        fp,
        "record_start_and_baselines",
        lambda: ("2026-11-08T00:00:00+00:00", BASELINE),
    )

    def note(what: str) -> None:
        calls.append(what)

    def tick(run_pass: object) -> int:
        note("tick")
        return 0

    def collect(*_: object) -> Any:
        note("collect")
        return ev.Evidence()

    monkeypatch.setattr(fp, "install_release", lambda ref, commit: note("install"))
    monkeypatch.setattr(fp, "fetch_estimate", lambda: summary)
    monkeypatch.setattr(fp, "run_passes", tick)
    monkeypatch.setattr(ev, "collect", collect)
    monkeypatch.setattr(ev, "health_exit_code", lambda: 0)
    return calls


def test_an_estimate_refusal_never_buys(
    fp: ModuleType,
    ev: ModuleType,
    cutover_error: type[Exception],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = NOTHING | {"verdict": "refused", "reasons": ["over"]}
    calls = _drive(fp, ev, monkeypatch, refused)
    with pytest.raises(cutover_error):
        fp._steps("v0.27.0")
    assert "tick" not in calls


def test_nothing_to_buy_skips_the_purchase_and_goes_to_evidence(
    fp: ModuleType, ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _drive(fp, ev, monkeypatch, NOTHING)
    assert fp._steps("v0.27.0") == 0
    assert calls == ["install", "collect"]


@pytest.mark.parametrize(
    ("exits", "runs", "final"),
    [([6, 6, 0], 3, 0), ([0], 1, 0), ([3], 1, 3), ([6, 3], 2, 3)],
)
def test_pass_exits_zero_and_three_end_the_loop_and_six_runs_again(
    fp: ModuleType, exits: list[int], runs: int, final: int
) -> None:
    seq = iter(exits)
    count = [0]

    def run_pass() -> int:
        count[0] += 1
        return next(seq)

    assert fp.run_passes(run_pass) == final
    assert count[0] == runs


def test_the_last_of_five_sixes_raises(
    fp: ModuleType, cutover_error: type[Exception]
) -> None:
    count = [0]

    def run_pass() -> int:
        count[0] += 1
        return 6

    with pytest.raises(cutover_error, match="jobs still processing after 5 runs"):
        fp.run_passes(run_pass)
    assert count[0] == fp.PASS_ATTEMPTS == 5


@pytest.mark.parametrize("code", [1, 2, 4, 5])
def test_any_other_exit_stops_with_that_code(fp: ModuleType, code: int) -> None:
    with pytest.raises(fp.PassStopped) as exc:
        fp.run_passes(lambda: code)
    assert exc.value.exit_code == code


def test_main_returns_the_stopping_exit_code(
    fp: ModuleType, notes: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stopped(ref: str) -> int:
        raise fp.PassStopped(4)

    monkeypatch.setattr(fp, "_steps", stopped)
    assert fp.main(["x", "v0.27.0"]) == 4


def test_no_argument_prints_usage_and_exits_two(fp: ModuleType) -> None:
    assert fp.main(["x"]) == 2


# -- 5.4: evidence -------------------------------------------------------------


def _session(
    date: str, status: str, ledger: int, raw: int, check: str
) -> dict[str, Any]:
    return {
        "session_date": date,
        "status": status,
        "ledger": ledger,
        "raw": raw,
        "check": check,
    }


def _coverage(*sessions: dict[str, Any]) -> dict[str, Any]:
    return {"products": [{"product": "ES", "sessions": list(sessions)}]}


def test_a_ledger_unequal_complete_session_fails_naming_it(ev: ModuleType) -> None:
    doc = _coverage(_session("2024-01-02", "complete", 5, 6, "mismatch"))
    failures = ev.ledger_failures(doc, "statistics")
    assert len(failures) == 1
    assert "2024-01-02" in failures[0]


def test_a_session_that_did_not_ingest_is_listed_not_failed(ev: ModuleType) -> None:
    doc = _coverage(
        _session("2024-01-02", "pending", 0, 0, "ok"),
        _session("2024-01-03", "complete", 4, 4, "ok"),
    )
    assert ev.ledger_failures(doc, "statistics") == []
    assert dict(ev.unfinished_sessions(doc)) == {"pending": 1}


def test_the_definition_delta_is_against_the_baseline(ev: ModuleType) -> None:
    after = {"ES": 40, "GC": 5}
    assert ev.definition_delta(BASELINE["definitions"], after) == {
        "ES": (10, 40, 30),
        "GC": (5, 5, 0),
    }


def _fake_mt_run(
    ev: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    units: list[dict[str, Any]],
    *,
    fail: str | None = None,
    sessions: list[dict[str, Any]] | None = None,
) -> None:
    def fake(args: list[str]) -> subprocess.CompletedProcess[str]:
        joined = " ".join(args)
        if fail and fail in joined:
            return subprocess.CompletedProcess(args, 4, "", "storage down")
        if "coverage" in joined:
            body: Any = _coverage(*(sessions or []))
        elif "debug requests" in joined:
            body = {"rows": []}
        elif "units" in joined:
            body = {"rows": units}
        elif "overview" in joined:
            body = {"passes": []}
        else:
            body = {"rows": [{}, {}], "exit_code": 0}
        return subprocess.CompletedProcess(args, 0, json.dumps(body), "")

    monkeypatch.setattr(ev, "mt_run", fake)
    monkeypatch.setattr(ev, "_reconcile", lambda *a: None)
    monkeypatch.setattr(ev, "run_session_check", lambda dirs: "pass")


def test_a_failing_evidence_command_stops_the_script_naming_it(
    ev: ModuleType, cutover_error: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_mt_run(ev, monkeypatch, [], fail="debug units")
    with pytest.raises(cutover_error, match="debug units"):
        ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)


# -- 5.6: the exit rule --------------------------------------------------------


def _settled(ev: ModuleType, monkeypatch: pytest.MonkeyPatch, health: int = 0) -> None:
    monkeypatch.setattr(ev, "health_exit_code", lambda: health)


def test_a_changed_health_exit_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch, health=1)
    assert ev.final_failures(ev.Evidence(), BASELINE, NOTHING)


def test_a_second_estimate_that_plans_a_request_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    planned = NOTHING | {"verdict": "allowed", "planned_usd": "1.2"}
    assert ev.final_failures(ev.Evidence(), BASELINE, planned)


def test_a_failed_reconciliation_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    found = ev.Evidence(failures=["reconciliation failed"])
    assert ev.final_failures(found, BASELINE, NOTHING) == ["reconciliation failed"]


def test_a_unit_with_no_reason_that_is_not_ingested_fails(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    units = [{"unit_id": 7, "state": "submitted", "failure_reason": None}]
    _fake_mt_run(ev, monkeypatch, units)
    found = ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)
    assert found.unfinal_units == [7]
    assert ev.final_failures(found, BASELINE, NOTHING)


def test_failed_units_with_reasons_and_coverage_findings_pass(
    ev: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _settled(ev, monkeypatch)
    units = [
        {"unit_id": 1, "state": "ingested", "failure_reason": None},
        {
            "unit_id": 2,
            "state": "verified",
            "failure_reason": "stat_reference: instrument_id 1: not a session",
        },
    ]
    sessions = [_session("2024-01-02", "retry_exhausted", 0, 0, "ok")]
    _fake_mt_run(ev, monkeypatch, units, sessions=sessions)
    found = ev.collect("2026-11-08T00:00:00+00:00", D(7), BASELINE)
    assert ev.final_failures(found, BASELINE, NOTHING) == []
