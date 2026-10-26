"""The slice 173 cutover script's control flow (Task 5.2).

``scripts/`` is not a package: the modules load by path with the directory on
``sys.path``, as ``test_cutover_172.py`` does. Every host, subprocess and
database seam is patched (the 173 script reuses 172's steps, so its seams are
patched on both modules). One case per row of the design's failure table:
the exit code, whether the timers end held or released, and the step named.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
TIMERS = ("mt-minute-pass.timer", "mt-daily-pass.timer", "mt-kalshi-pass.timer")


def _load(name: str) -> Any:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cutover() -> Any:
    return _load("cutover_173_eligible_rollup_swap")


def _health(failing: tuple[str, ...] = ()) -> str:
    from cutover_173_checks import REQUIRED_HEALTH

    checks = [
        {"name": name, "ok": name not in failing, "detail": f"{name} detail"}
        for name in REQUIRED_HEALTH
    ]
    return "INFO pool opened\n" + json.dumps({"healthy": not failing, "checks": checks})


class Harness:
    """Records every seam call, in order, as a readable event; ``state`` is
    the swap state the catalog reports, which the migrate stub moves."""

    def __init__(self, cutover: Any) -> None:
        self.cutover = cutover
        self.prior = cutover.prior
        self.checks = cutover.checks
        self.State = cutover.checks.SwapState
        self.events: list[str] = []
        self.state: Any = self.State.BEFORE
        self.state_error: Exception | None = None
        self.mt_codes: dict[str, int] = {}
        self.verify_output = "[]"
        self.health_output = _health()
        self.installed = "old"
        self.install_error: Exception | None = None
        self.migrate_effect: Any = None  # called after the state moves

    def mt(self, *args: str, as_owner: bool = False) -> tuple[int, str]:
        name = args[0]
        if "--target" in args:
            name += " next"
        if "--verify" in args:
            assert as_owner, "rebuild-coverage refreshes; it needs the owner's URL"
            name += " --verify"
        self.events.append(f"mt {name}")
        output = self.verify_output if args[0] == "verify" else ""
        return self.mt_codes.get(name, 0), output

    def run(self, args: list[str], **_kwargs: Any) -> Any:
        if args[0] == "mt-run":
            self.events.append("health")
            return subprocess.CompletedProcess(args, 1, stdout=self.health_output)
        self.events.append(" ".join(args))
        return subprocess.CompletedProcess(args, 0, stdout="")

    def read_state(self, _conn: Any) -> Any:
        if self.state_error is not None:
            raise self.state_error
        return self.state

    def install(self, *_args: str) -> str:
        self.events.append("install")
        if self.install_error is not None:
            raise self.install_error
        return "mt, version 0.25.0"

    def migrate(self, *_args: str) -> None:
        self.events.append("migrate")
        self.state = self.State.DONE
        if self.migrate_effect is not None:
            self.migrate_effect(self)

    def patches(self, tmp_path: Path) -> list[Any]:
        c, p, k = self.cutover, self.prior, self.checks
        conn = MagicMock()
        conn.__enter__ = MagicMock(return_value=conn)
        conn.__exit__ = MagicMock(return_value=False)
        shared = {"_mt": self.mt, "_connect": lambda: conn, "run": self.run}
        return [
            *(
                patch.object(m, n, side_effect=f)
                for m in (c, p)
                for n, f in shared.items()
            ),
            patch.object(p, "_installed_commit", side_effect=lambda: self.installed),
            patch.object(p, "install", side_effect=self.install),
            patch.object(p, "unit_active", return_value=True),
            patch.object(p, "wait_for_unit_to_end"),
            patch.object(c, "preflight", return_value="abc123def456"),
            patch.object(c, "migrate", side_effect=self.migrate),
            patch.object(
                c,
                "restart_serve_if_active",
                side_effect=lambda: self.events.append("restart serve"),
            ),
            patch.object(c, "start_log"),
            patch.object(c, "NOTES_DIR", tmp_path),
            patch.object(k, "read_swap_state", side_effect=self.read_state),
            patch.object(k, "row_counts", return_value={"minute_5min_ohlcv": 7}),
        ]

    def run_main(self, tmp_path: Path) -> int:
        patches = self.patches(tmp_path)
        for p in patches:
            p.start()
        try:
            return self.cutover.main(["v0.25.0"])
        finally:
            for p in reversed(patches):
                p.stop()

    @property
    def stopped(self) -> list[str]:
        return [e.split()[-1] for e in self.events if e.startswith("systemctl stop")]

    @property
    def started(self) -> list[str]:
        return [e.split()[-1] for e in self.events if e.startswith("systemctl start")]


@pytest.fixture()
def harness(cutover: Any) -> Harness:
    return Harness(cutover)


def _report(tmp_path: Path) -> str:
    return next(tmp_path.glob("*-173-cutover.md")).read_text()


def _failed_step(tmp_path: Path) -> str:
    return next(
        line.split("**")[1]
        for line in _report(tmp_path).splitlines()
        if line.startswith("- **") and line.endswith(": FAIL")
    )


def test_clean_run_order(harness: Harness, tmp_path: Path) -> None:
    assert harness.run_main(tmp_path) == 0
    assert harness.events == [
        "mt verify next",
        "mt rebuild-coverage next --verify",
        *(f"systemctl stop {t}" for t in TIMERS),
        "install",
        "migrate",
        "mt verify",
        "health",
        "restart serve",
        *(f"systemctl start {t}" for t in TIMERS),
    ]
    assert "Outcome: PASS" in _report(tmp_path)
    assert "minute_5min_ohlcv: 7" in _report(tmp_path)


def test_precheck_refusal_changes_nothing_and_names_the_view(
    harness: Harness, tmp_path: Path
) -> None:
    harness.mt_codes["verify next"] = 2
    harness.verify_output = json.dumps(
        [
            {"view": "minute_hourly_ohlcv_next", "in_parity": False},
            {"view": "minute_5min_ohlcv_next", "in_parity": True},
        ]
    )
    assert harness.run_main(tmp_path) == 1
    assert harness.stopped == harness.started == []
    assert "install" not in harness.events and "migrate" not in harness.events
    assert _failed_step(tmp_path) == "precheck"
    assert "out of parity: minute_hourly_ohlcv_next\n" in _report(tmp_path)


def test_precheck_refuses_an_unverified_coverage(
    harness: Harness, tmp_path: Path
) -> None:
    harness.mt_codes["rebuild-coverage next --verify"] = 1
    assert harness.run_main(tmp_path) == 1
    assert harness.stopped == []
    assert "minute_coverage_next --verify exited 1" in _report(tmp_path)


def test_rolled_back_rename_releases_the_timers(
    harness: Harness, tmp_path: Path
) -> None:
    def roll_back(h: Harness) -> None:
        h.state = h.State.BEFORE
        raise h.cutover.prior.CutoverError("apply did not report 064")

    harness.migrate_effect = roll_back
    assert harness.run_main(tmp_path) == 1
    assert harness.started == harness.stopped == list(TIMERS)
    assert _failed_step(tmp_path) == "swap"


def test_failed_drop_leaves_the_timers_held(harness: Harness, tmp_path: Path) -> None:
    def drop_fails(h: Harness) -> None:
        h.state = h.State.SWAPPED
        raise h.cutover.prior.CutoverError("DROP MATERIALIZED VIEW failed")

    harness.migrate_effect = drop_fails
    assert harness.run_main(tmp_path) == 1
    assert harness.stopped == list(TIMERS)
    assert harness.started == []
    assert _failed_step(tmp_path) == "swap"
    assert "restart serve" not in harness.events


def test_failed_post_swap_verify_leaves_the_timers_held(
    harness: Harness, tmp_path: Path
) -> None:
    harness.mt_codes["verify"] = 2
    harness.verify_output = json.dumps(
        [{"view": "minute_5min_ohlcv", "in_parity": False}]
    )
    assert harness.run_main(tmp_path) == 1
    assert harness.started == []
    assert _failed_step(tmp_path) == "verify"
    assert "out of parity: minute_5min_ohlcv" in _report(tmp_path)


def test_failing_health_leaves_the_timers_held(
    harness: Harness, tmp_path: Path
) -> None:
    harness.health_output = _health(failing=("value parity", "cagg minute_coverage"))
    assert harness.run_main(tmp_path) == 1
    assert harness.started == []
    report = _report(tmp_path)
    assert "value parity: value parity detail" in report
    assert "cagg minute_coverage: cagg minute_coverage detail" in report


def test_leftover_prev_after_the_swap_leaves_the_timers_held(
    harness: Harness, tmp_path: Path
) -> None:
    harness.migrate_effect = lambda h: setattr(h, "state", h.State.SWAPPED)
    assert harness.run_main(tmp_path) == 1
    assert harness.started == []
    assert "_prev objects remain" in _report(tmp_path)


def test_failed_install_releases_the_timers(harness: Harness, tmp_path: Path) -> None:
    harness.install_error = harness.prior.CutoverError("installer exited 1")
    assert harness.run_main(tmp_path) == 1
    assert harness.started == list(TIMERS)
    assert _failed_step(tmp_path) == "install"


def test_unreadable_state_after_a_failure_keeps_the_timers_held(
    harness: Harness, tmp_path: Path
) -> None:
    def lose_the_database(h: Harness) -> None:
        h.state_error = OSError("connection refused")
        raise h.cutover.prior.CutoverError("apply failed")

    harness.migrate_effect = lose_the_database
    assert harness.run_main(tmp_path) == 1
    assert harness.started == []


def test_kill_mid_swap_then_rerun_completes(harness: Harness, tmp_path: Path) -> None:
    def killed(h: Harness) -> None:
        h.state = h.State.SWAPPED
        raise KeyboardInterrupt

    harness.migrate_effect = killed
    with pytest.raises(KeyboardInterrupt):
        harness.run_main(tmp_path)
    assert harness.started == []
    assert "Outcome: FAIL" in _report(tmp_path)

    harness.events.clear()
    harness.migrate_effect = None
    harness.installed = "abc123def456"  # the killed run installed the ref
    for old in tmp_path.glob("*-173-cutover.md"):
        old.unlink()
    assert harness.run_main(tmp_path) == 0
    # The _next objects are gone, so the precheck does not re-verify them.
    assert "mt verify next" not in harness.events
    assert "install" not in harness.events
    assert harness.events.index("migrate") < harness.events.index("restart serve")
    assert harness.started == list(TIMERS)


def test_unexpected_swap_state_is_refused_at_precheck(
    harness: Harness, tmp_path: Path
) -> None:
    harness.state_error = harness.checks.CheckFailed("unexpected swap state")
    assert harness.run_main(tmp_path) == 1
    assert harness.stopped == []
    assert _failed_step(tmp_path) == "precheck"


@pytest.mark.parametrize(
    ("names", "state"),
    [
        ("current next", "before"),
        ("current prev", "swapped"),
        ("current", "done"),
    ],
)
def test_swap_state_from_names(cutover: Any, names: str, state: str) -> None:
    k = cutover.checks
    sets = {"current": k.CURRENT, "next": k.NEXT, "prev": k.PREV}
    existing = set().union(*(sets[n] for n in names.split()))
    assert k.swap_state(existing) == state


def test_swap_state_refuses_a_mixed_set(cutover: Any) -> None:
    k = cutover.checks
    mixed = set(k.CURRENT) - {"minute_coverage"} | {"minute_coverage_prev"}
    with pytest.raises(k.CheckFailed, match="missing current"):
        k.swap_state(mixed)
