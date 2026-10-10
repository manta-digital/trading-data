"""Tests for the slice 227 cutover (``scripts/cutover_227_tick_backup.py``).

The pure helpers run on the real rendered cron file and the real pre-227
fixture. The steps run with their host calls (``psql``, ``shell``, ``run``,
``setup_backup``) replaced, following ``test_cutover_921.py``: the module is
loaded with ``scripts/`` on ``sys.path`` and its names patched.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FIXTURE = Path(__file__).parent / "fixtures" / "cron_main_pre227.txt"
CHECKOUT = "/home/manta/source/repos/manta/trading-data"
BUCKET = "b2:manta.trading.data"
MAIN_KEY = "MT_TIMESCALE_MAINTENANCE_URL"


def _load(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    # Siblings another test file already imported (the 228 drill reuses
    # cutover_227_helpers) hold the old classes; reload them all together.
    for cached in [m for m in sys.modules if m.startswith("cutover_227_")]:
        del sys.modules[cached]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def helpers() -> ModuleType:
    return _load("cutover_227_helpers")


@pytest.fixture(scope="module")
def cutover() -> ModuleType:
    return _load("cutover_227_tick_backup")


@pytest.fixture(scope="module")
def rendered() -> str:
    """The cron file setup-backup would install on manta9000."""
    deploy = ROOT / "deploy"
    result = subprocess.run(
        [
            str(deploy / "lib" / "render_cron.sh"),
            "--template",
            str(deploy / "cron.d" / "manta-trading-backup"),
            "--cluster-template",
            str(deploy / "cron.d" / "manta-trading-backup.cluster"),
            "--clusters",
            str(deploy / "backup-clusters.conf"),
            "--interval",
            "60",
            "--checkout",
            CHECKOUT,
            "--env-file",
            f"{CHECKOUT}/.env",
            "--host-root",
            "/data/backup",
            "--cron-user",
            "manta",
            "--keep-days",
            "7",
            "--pgdata",
            "17/main=/var/lib/postgresql/17/main",
            "--pgdata",
            "17/tick=/data/postgresql/17/tick",
            "--remote-prefix",
            BUCKET,
            "--restic-prefix",
            "system",
        ],  # fmt: skip
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _new_args(helpers: ModuleType) -> Any:
    """17/main's new arguments, from the real table as the cutover reads them."""
    from backup_clusters import TABLE_PATH, cluster

    main = cluster(TABLE_PATH, "17/main")
    return helpers.NewArgs(main.url_key, main.replication_host, BUCKET)


# --- pure helpers ------------------------------------------------------------------


def test_tick_commands_extracted_from_the_rendered_file(
    helpers: ModuleType, rendered: str
) -> None:
    commands = helpers.block_commands(rendered, "17/tick")
    assert sorted(commands) == ["health", "metadata", "push", "weekly"]
    assert commands["weekly"].startswith(f"{CHECKOUT}/scripts/cron_weekly_backup.sh ")
    assert "--url-key MT_TICK_MAINTENANCE_URL" in commands["metadata"]
    assert commands["health"].endswith(">/dev/null 2>&1")
    assert all("manta " not in c.split("/")[0] for c in commands.values())


def test_missing_block_or_job_fails(helpers: ModuleType, rendered: str) -> None:
    with pytest.raises(helpers.StepFailed, match="no '# cluster 17/x' block"):
        helpers.block_commands(rendered, "17/x")
    cut = "\n".join(x for x in rendered.splitlines() if "17-tick/metadata" not in x)
    with pytest.raises(helpers.StepFailed, match="lacks jobs: \\['metadata'\\]"):
        helpers.block_commands(cut, "17/tick")


def test_production_lines_of_the_pre227_file(helpers: ModuleType) -> None:
    assert helpers.production_lines(FIXTURE.read_text()) == (
        FIXTURE.read_text().splitlines()
    )


def test_strip_and_compare_accepts_the_expected_change(
    helpers: ModuleType, rendered: str
) -> None:
    old = FIXTURE.read_text()
    assert helpers.production_changes(old, rendered, _new_args(helpers)) == []


def test_rerun_compares_the_rendered_file_with_itself(
    helpers: ModuleType, rendered: str
) -> None:
    """Review F001: after step 4, a re-run's "old" file is the rendered one."""
    assert helpers.production_changes(rendered, rendered, _new_args(helpers)) == []


def test_new_args_follow_the_table(helpers: ModuleType, rendered: str) -> None:
    """Review F003: a different replication host in the table is stripped too."""
    other = rendered.replace("--replication-host 127.0.0.1", "--replication-host ::1")
    args = helpers.NewArgs(MAIN_KEY, "::1", BUCKET)
    assert helpers.production_changes(FIXTURE.read_text(), other, args) == []


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("--keep-days 7 ", "--keep-days 8 "),
        ("0 3 * * 0 manta", "0 4 * * 0 manta"),
        ("/data/backup/base ", "/data/backup/17-main/base "),
    ],
)
def test_strip_and_compare_refuses_any_other_change(
    helpers: ModuleType, rendered: str, before: str, after: str
) -> None:
    changed = rendered.replace(before, after, 1)
    assert changed != rendered
    old = FIXTURE.read_text()
    changes = helpers.production_changes(old, changed, _new_args(helpers))
    assert changes and after.strip() in changes[0]


def test_setup_not_ok_patterns(helpers: ModuleType, cutover: ModuleType) -> None:
    output = "\n".join(
        [
            "OK 17/main dir-base",
            "MISSING 17/tick dir-root /data/backup/17-tick",
            "PENDING RESTART 17/tick archive_mode",
            "DRIFT cron.d /etc/cron.d/manta-trading-backup",
            "MISSING 17/tick lifecycle 17-tick/base/ (add in the B2 console: x)",
            "DRIFT 17/main dir-base-owner-mode 'manta:manta 775' 'root:root 755'",
        ]
    )
    assert helpers.setup_not_ok(output, cutover.CHECK_ALLOWED) == [
        "DRIFT 17/main dir-base-owner-mode 'manta:manta 775' 'root:root 755'"
    ]
    applied = helpers.setup_not_ok(output, cutover.APPLY_ALLOWED)
    assert "PENDING RESTART 17/tick archive_mode" not in applied
    assert "DRIFT cron.d /etc/cron.d/manta-trading-backup" in applied
    assert not any("lifecycle" in x for x in applied)
    pending_main = "PENDING RESTART 17/main archive_mode"
    assert helpers.setup_not_ok(pending_main, cutover.APPLY_ALLOWED) == [pending_main]


def test_apply_accepts_tick_pre_restart_lines(
    helpers: ModuleType, cutover: ModuleType
) -> None:
    """The lines the 2026-10-05 cutover saw before tick's restart."""
    output = "\n".join(
        [
            "DRIFT 17/tick archive_mode 'on' 'off'",
            "PENDING RESTART 17/tick archive_command (shown as (disabled) …)",
            "PENDING RESTART 17/tick archive_mode",
            "DRIFT 17/tick archive_mode source postgresql.auto.conf '(none)'",
            "DRIFT 17/tick archive_command source postgresql.auto.conf '(none)'",
            "DRIFT 17/main archive_mode 'on' 'off'",
        ]
    )
    assert helpers.setup_not_ok(output, cutover.APPLY_ALLOWED) == [
        "DRIFT 17/tick archive_command source postgresql.auto.conf '(none)'",
        "DRIFT 17/main archive_mode 'on' 'off'",
    ]


def test_health_flags(helpers: ModuleType) -> None:
    line = "2026-10-04T14:00:00+00:00 PASS a PASS b FLAGS archive=0 stale=1"
    assert helpers.health_flags(line) == (0, 1)
    assert helpers.health_flags("2026 FAIL cannot_check") is None


# --- steps with host calls replaced -----------------------------------------------


@pytest.fixture
def ctx(cutover: ModuleType, tmp_path: Path) -> Any:
    from backup_clusters import BackupCluster

    def row(name: str, root: Path) -> BackupCluster:
        return BackupCluster(name, MAIN_KEY, root, None, None, "0 2 * * *", "0 3 * * 0")

    tick_root = tmp_path / "17-tick"
    (tick_root / "wal").mkdir(parents=True)
    return cutover.Context(
        checkout=tmp_path,
        main=row("17/main", tmp_path / "main"),
        tick=row("17/tick", tick_root),
        bucket_remote=BUCKET,
    )


def _step(cutover: ModuleType) -> Any:
    return cutover.Step(1, "t", "e")


def test_failed_step_stops_later_steps_and_report_still_written(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def ok(_c: Any, _s: Any) -> None:
        calls.append("ok")

    def bad(_c: Any, s: Any) -> None:
        cutover.require(False, s, "boom")

    monkeypatch.setattr(
        cutover, "STEPS", [("a", "x", ok), ("b", "y", bad), ("c", "z", ok)]
    )
    steps = [cutover.Step(n, t, e) for n, (t, e, _a) in enumerate(cutover.STEPS, 1)]
    assert cutover.run_steps(ctx, steps) is False
    assert calls == ["ok"]
    assert [s.status for s in steps] == ["PASS", "FAIL", "not run"]
    report = cutover.render_report(steps, "2026-10-04T14:00:00+00:00", "FAIL", [])
    assert "## Step 2: b — FAIL" in report and "FAILED: boom" in report
    assert "## Step 3: c — not run" in report
    assert report.startswith("---\ndocType: notes\n")


def test_weekly_failure_does_not_arm(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ctx.tick.backup_root
    monkeypatch.setattr(cutover, "tick_commands", lambda: {"weekly": "w"})

    def failing(_cmd: str, _step: Any) -> subprocess.CompletedProcess[str]:
        (root / "base.log").write_text("error: reconcile guards refused\n")
        return subprocess.CompletedProcess([], 1, "", "")

    monkeypatch.setattr(cutover, "shell", failing)
    with pytest.raises(cutover.StepFailed, match="not armed"):
        cutover.step_weekly(ctx, _step(cutover))
    assert not (root / "RECONCILE-ARMED").exists()


def test_weekly_success_arms(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = ctx.tick.backup_root
    monkeypatch.setattr(cutover, "tick_commands", lambda: {"weekly": "w"})

    def passing(_cmd: str, _step: Any) -> subprocess.CompletedProcess[str]:
        done = "=== weekly backup done (unarmed): 2026-10-04T14:00:00+00:00\n"
        (root / "base.log").write_text(f"reconcile skipped: not armed\n{done}")
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(cutover, "shell", passing)
    cutover.step_weekly(ctx, _step(cutover))
    assert (root / "RECONCILE-ARMED").exists()


def test_same_day_rerun_skips_weekly(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import datetime

    root = ctx.tick.backup_root
    (root / "base" / datetime.now().strftime("%Y%m%d")).mkdir(parents=True)
    (root / "RECONCILE-ARMED").touch()
    monkeypatch.setattr(cutover, "shell", lambda *_a: pytest.fail("weekly ran"))
    step = _step(cutover)
    cutover.step_weekly(ctx, step)
    assert "skipped (re-run)" in step.seen[-1]


def _restart_host(
    cutover: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    main: str,
    tick: str,
    held: int,
) -> list[list[str]]:
    pending = {"17/main": main, "17/tick": tick}
    monkeypatch.setattr(
        cutover,
        "psql",
        lambda c, sql: "on" if sql.startswith("SHOW") else pending[c],
    )
    monkeypatch.setattr(cutover, "tick_locks_held", lambda: held)
    restarts: list[list[str]] = []
    monkeypatch.setattr(cutover, "run", lambda args, **_k: restarts.append(args))
    return restarts


def test_pending_restart_on_production_stops(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    restarts = _restart_host(cutover, monkeypatch, main="t", tick="t", held=0)
    with pytest.raises(cutover.StepFailed, match="production has a pending restart"):
        cutover.step_restart(ctx, _step(cutover))
    assert restarts == []


def test_held_lock_stops_before_the_restart(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    restarts = _restart_host(cutover, monkeypatch, main="f", tick="t", held=1)
    with pytest.raises(cutover.StepFailed, match="advisory lock"):
        cutover.step_restart(ctx, _step(cutover))
    assert restarts == []


def test_pending_tick_restarts_only_tick(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    restarts = _restart_host(cutover, monkeypatch, main="f", tick="t", held=0)
    cutover.step_restart(ctx, _step(cutover))
    assert restarts == [["systemctl", "restart", "postgresql@17-tick"]]


def test_wal_wait_gives_up_at_the_bound(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cutover, "psql", lambda *_a: "0000000100000000000000AB")
    monkeypatch.setattr(cutover, "WAL_SWITCH_WAIT_S", 0)
    with pytest.raises(cutover.StepFailed, match="AB.zst absent after 0 s"):
        cutover.step_switch(ctx, _step(cutover))
    (ctx.tick.backup_root / "wal" / "0000000100000000000000AB.zst").touch()
    cutover.step_switch(ctx, _step(cutover))


def _production_host(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch, check: str, rc: int
) -> None:
    ctx.conf_sums = "abc  /etc/postgresql/17/main/postgresql.conf"
    monkeypatch.setattr(cutover, "conf_sums", lambda: ctx.conf_sums)
    ctx.main.backup_root.mkdir(parents=True)
    (ctx.main.backup_root / "backup-health.log").write_text(
        "2026 PASS x FLAGS archive=0 stale=0\n"
    )
    monkeypatch.setattr(
        cutover,
        "setup_backup",
        lambda *_a: subprocess.CompletedProcess([], rc, check, ""),
    )


def test_lifecycle_missing_passes_and_is_reported(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = "MISSING 17/tick lifecycle 17-tick/base/ (add in the B2 console: r)"
    _production_host(cutover, ctx, monkeypatch, f"OK x\n{missing}\nSUMMARY", 0)
    cutover.step_production(ctx, _step(cutover))
    assert ctx.lifecycle == [missing]
    report = cutover.render_report(
        [], "2026-10-04T14:00:00+00:00", "PASS", ctx.lifecycle
    )
    assert f"## B2 console rules to add (TD10)\n\n- {missing}" in report


def test_any_other_not_ok_fails_step_12(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _production_host(cutover, ctx, monkeypatch, "DRIFT 17/tick dir-wal\nSUMMARY", 1)
    with pytest.raises(cutover.StepFailed, match="not clean"):
        cutover.step_production(ctx, _step(cutover))


def test_unreadable_production_log_fails_step_12_with_a_report(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F002: a missing log is a recorded failure, not an escape."""
    ctx.conf_sums = "abc"
    monkeypatch.setattr(cutover, "conf_sums", lambda: "abc")
    monkeypatch.setattr(
        cutover, "STEPS", [("production", "x", cutover.step_production)]
    )
    steps = [cutover.Step(1, "production", "x")]
    assert cutover.run_steps(ctx, steps) is False
    assert steps[0].status == "FAIL"
    assert "production health is not clean" in steps[0].seen[-1]


def test_unexpected_host_error_is_recorded_not_raised(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_c: Any, _s: Any) -> None:
        raise OSError("cron file unreadable")

    monkeypatch.setattr(cutover, "STEPS", [("x", "y", broken)])
    steps = [cutover.Step(1, "x", "y")]
    assert cutover.run_steps(ctx, steps) is False
    assert steps[0].seen[-1] == "cron file unreadable"


def test_failed_push_fails_the_offsite_step(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review F004: the push's own exit status is checked and recorded."""
    monkeypatch.setattr(cutover, "tick_commands", lambda: {"push": "p"})
    monkeypatch.setattr(
        cutover,
        "shell",
        lambda *_a: subprocess.CompletedProcess([], 1, "", "rclone: 403"),
    )
    monkeypatch.setattr(cutover, "run", lambda *_a, **_k: pytest.fail("checked"))
    step = _step(cutover)
    with pytest.raises(cutover.StepFailed, match="push failed"):
        cutover.step_offsite(ctx, step)
    assert "rclone: 403" in step.seen


def test_hung_job_is_a_step_failure(
    cutover: ModuleType, ctx: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def hung(_c: Any, _s: Any) -> None:
        raise subprocess.TimeoutExpired("bash", 1, stderr=b"partial")

    monkeypatch.setattr(cutover, "STEPS", [("x", "y", hung)])
    steps = [cutover.Step(1, "x", "y")]
    assert cutover.run_steps(ctx, steps) is False
    assert steps[0].seen[-1] == "partial"
