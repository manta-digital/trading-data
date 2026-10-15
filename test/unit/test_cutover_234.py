"""Tests for the slice 234 cutover's own logic (``scripts/cutover_234_*.py``).

Only what the real cutover run would not exercise cleanly: the size parse,
the connection boundary, the purchase guard, the missing-key stop, and timer
release on failure. Host calls are replaced on the loaded modules.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


@pytest.fixture
def mods() -> dict[str, ModuleType]:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    # Other cutover tests reload cutover_227_*; load one consistent set.
    for cached in [
        m for m in sys.modules if m.startswith(("cutover_227_", "cutover_234_"))
    ]:
        del sys.modules[cached]
    return {
        "checks": importlib.import_module("cutover_234_checks"),
        "host": importlib.import_module("cutover_234_host"),
        "main": importlib.import_module("cutover_234_tick_wiring"),
        "steps": importlib.import_module("cutover_227_helpers"),
    }


def _step(mods: dict[str, ModuleType]) -> Any:
    return mods["steps"].Step(1, "t", "e")


# -- memory bound ------------------------------------------------------------------


def test_g_suffixed_memory_max_parses_1024_based(mods) -> None:
    checks = mods["checks"]
    assert checks.memory_max_bytes("[Slice]\nMemoryMax=14G\n") == 15032385536
    assert checks.memory_max_bytes("MemoryMax = 512M") == 512 * 1024**2
    assert (
        checks.memory_max_bytes(
            (ROOT / "deploy/systemd/manta-acquisition.slice").read_text()
        )
        == 14 * 1024**3
    )
    with pytest.raises(ValueError, match="MemoryMax"):
        checks.memory_max_bytes("[Slice]\n")


def _fake_fire(mods, monkeypatch, *, memory_max: str, oom: int = 0) -> None:
    main, host = mods["main"], mods["host"]

    class Sampler:
        def __init__(self, _unit: str) -> None:
            self.memory_max, self.oom_kill = memory_max, oom

        def start(self) -> None: ...

        def stop(self) -> None: ...

    journal = type("J", (), {"entries": [], "find": lambda self, n: [(0, f"{n}: 3G")]})
    monkeypatch.setattr(host, "installed_memory_max", lambda: 14 * 1024**3)
    monkeypatch.setattr(host, "CgroupSampler", Sampler)
    monkeypatch.setattr(main, "fire_unit", lambda _u, _a: ("cursor", "now"))
    monkeypatch.setattr(main, "unit_result", lambda _u: ("success", "0"))
    monkeypatch.setattr(main, "read_unit_journal", lambda _c, _u: journal())
    monkeypatch.setattr(main, "_tick_last_run", lambda: {"outcome": "COMPLETE"})


def test_fire_passes_when_memory_max_matches(mods, monkeypatch) -> None:
    _fake_fire(mods, monkeypatch, memory_max=str(14 * 1024**3))
    step = _step(mods)
    mods["main"].step_fire(None, step)
    assert "purchases submitted: 0" in step.seen


def test_a_memory_max_mismatch_fails(mods, monkeypatch) -> None:
    _fake_fire(mods, monkeypatch, memory_max="max")
    with pytest.raises(mods["steps"].StepFailed, match="memory.max"):
        mods["main"].step_fire(None, _step(mods))


def test_an_oom_kill_fails(mods, monkeypatch) -> None:
    _fake_fire(mods, monkeypatch, memory_max=str(14 * 1024**3), oom=1)
    with pytest.raises(mods["steps"].StepFailed, match="OOM"):
        mods["main"].step_fire(None, _step(mods))


# -- connections -------------------------------------------------------------------


def test_the_budget_fits_at_the_boundary_and_fails_one_past(mods) -> None:
    checks = mods["checks"]
    load = checks.ClusterLoad(max_connections=30, superuser_reserved=3, live=16)
    assert load.headroom == 11
    assert checks.fits(load, 11)
    assert not checks.fits(load, 12)


def test_the_needs_come_from_the_constants(mods) -> None:
    from manta_trading.data.tick.constants import TICK_API_POOL_MAX, TICK_INGEST_WORKERS

    checks = mods["checks"]
    assert checks.TICK_API_CONNECTIONS == 2 * TICK_API_POOL_MAX == 8
    assert checks.PRIMARY_API_EXTRA == 4  # the 231 "Dev values" table's figure
    assert checks.TICK_PASS_CONNECTIONS == 1 + TICK_INGEST_WORKERS


def test_workers_in_the_serve_unit_fails_step_6(mods, monkeypatch, tmp_path) -> None:
    main, host = mods["main"], mods["host"]
    (tmp_path / "mt-serve.service").write_text(
        "[Service]\nExecStart=/opt/manta-trading/.venv/bin/mt serve --workers 4\n"
    )
    monkeypatch.setattr(host, "SYSTEMD_DIR", tmp_path)
    with pytest.raises(mods["steps"].StepFailed, match="--workers"):
        main.step_connections(None, _step(mods))


# -- purchase guard ----------------------------------------------------------------


def _estimate(requests: list[dict[str, Any]], planned: str) -> dict[str, Any]:
    summary = {"verdict": "ok", "requests": requests, "planned_usd": planned}
    return {"phases": [{"name": "purchase", "outcome": "ok", "summary": summary}]}


def test_the_guard_passes_only_when_nothing_is_wanted(mods) -> None:
    wants = mods["checks"].purchase_wants
    assert wants(_estimate([], "0")) == []
    request = {"schema": "tbbo", "start": "2026-10-01", "end": "2026-10-02", "days": 1}
    assert wants(_estimate([request], "1.25")) == [
        "request tbbo 2026-10-01 → 2026-10-02 (1 day(s))",
        "planned cost 1.25 USD",
    ]
    skipped = {"phases": [{"name": "purchase", "outcome": "skipped", "summary": {}}]}
    assert wants(skipped) == ["purchase phase did not plan (outcome skipped)"]


# -- settings ----------------------------------------------------------------------


def test_a_missing_key_stops_step_1_naming_the_command(mods, monkeypatch) -> None:
    main = mods["main"]
    secret = "postgresql://app:hunter2@h/tick"
    env = f"MT_TICK_DB_URL={secret}\nMT_TIMESCALE_DB_URL=postgresql://x:pw@h/db\n"
    monkeypatch.setattr(main, "preflight", lambda _ref: "c" * 40)
    monkeypatch.setattr(main, "out", lambda _args, sudo=False: env)
    monkeypatch.setattr(main.getpass, "getuser", lambda: ROOT.owner())
    ctx = main.Context("v1", ROOT)
    step = _step(mods)
    with pytest.raises(mods["steps"].StepFailed, match="add-tick-env"):
        main.step_preconditions(ctx, step)
    seen = "\n".join(step.seen)
    assert f"sudo {ROOT / 'deploy/add-tick-env.sh'} --from {ROOT / '.env'}" in seen
    assert "MT_DATABENTO_API_KEY" in seen
    assert "hunter2" not in seen and "pw@" not in seen


def test_the_key_list_is_the_scripts(mods) -> None:
    keys = mods["checks"].tick_env_keys((ROOT / "deploy/add-tick-env.sh").read_text())
    assert len(keys) == 5 and "MT_TICK_MAINTENANCE_URL" not in keys


# -- timers ------------------------------------------------------------------------


def test_timers_are_released_when_a_step_fails(mods, monkeypatch, tmp_path) -> None:
    main = mods["main"]
    calls: list[list[str]] = []
    monkeypatch.setattr(main, "unit_active", lambda unit: unit.endswith(".timer"))
    monkeypatch.setattr(main, "wait_for_unit_to_end", lambda _unit: None)
    monkeypatch.setattr(main, "run", lambda args, **_k: calls.append(args))
    monkeypatch.setattr(main, "say", lambda _text: None)
    monkeypatch.setattr(main, "NOTES_DIR", tmp_path)

    def boom(_ctx: Any, _steps: Any) -> bool:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(main, "run_steps", boom)
    monkeypatch.chdir(ROOT)
    with pytest.raises(RuntimeError):
        main.main(["cutover", "v1"])
    started = [c[-1] for c in calls if c[:2] == ["systemctl", "start"]]
    assert started == list(main.HELD_TIMERS)
