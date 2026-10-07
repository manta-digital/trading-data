"""The restore drill's runner, report and rebuild environment (slice 228).

Steps are stubbed: the runner's contract is exit 0 only when every step
passed, a report written either way, and cleanup run even when a step fails.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import drill_tick_restore as drill  # noqa: E402
from backup_clusters import BackupCluster  # noqa: E402
from cutover_227_helpers import Step, StepFailed  # noqa: E402
from drill_228_context import (  # noqa: E402
    READ_ONLY_URL_PARAM,
    DrillContext,
    rebuild_env,
)

CALENDAR_URL = "postgresql://mt_app:secret@db.example:5432/trading"
TICK = BackupCluster(
    "17/tick",
    "MT_TICK_MAINTENANCE_URL",
    Path("/data/backup/17-tick"),
    None,
    None,
    "15 2 * * *",
    "30 2 * * 0",
)


@pytest.fixture
def ctx(tmp_path: Path) -> DrillContext:
    (tmp_path / ".env").write_text(f'MT_TIMESCALE_DB_URL="{CALENDAR_URL}"\n')
    context = DrillContext(
        tmp_path, TICK, Path("/data/tick-archive"), "20261007T000000Z"
    )
    return context


def ok(ctx: DrillContext, step: Step) -> None:
    step.seen.append("fine")


def boom(ctx: DrillContext, step: Step) -> None:
    raise StepFailed("expected 3, seen 2")


def never(ctx: DrillContext, step: Step) -> None:
    raise AssertionError("a step after a failure must not run")


def test_all_steps_passing_exits_zero_with_the_pass_line(
    ctx: DrillContext, tmp_path: Path
) -> None:
    report = tmp_path / "notes" / "r.md"
    specs = [("a", "x", ok), ("b", "y", ok)]
    assert drill.execute(ctx, report, specs) == 0
    text = report.read_text()
    assert text.rstrip().endswith(drill.PASS_LINE)
    assert "## Step 8: clean up — PASS" in text


def test_a_failing_step_exits_nonzero_writes_the_report_and_stops(
    ctx: DrillContext, tmp_path: Path
) -> None:
    report = tmp_path / "r.md"
    assert (
        drill.execute(
            ctx, report, [("a", "x", ok), ("b", "y", boom), ("c", "z", never)]
        )
        == 1
    )
    text = report.read_text()
    assert "## Step 1: b — FAIL" in text and "expected 3, seen 2" in text
    assert "## Step 2: c — not run" in text
    assert text.rstrip().endswith("FAIL")
    assert drill.PASS_LINE not in text


def test_cleanup_runs_when_a_step_raises(
    ctx: DrillContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def stop(d: Path, run: Any) -> bool:
        calls.append("stop")
        return True

    monkeypatch.setattr(drill, "stop_scratch_server", stop)
    monkeypatch.setattr(
        drill, "remove_drill_dir", lambda d, sudo_n: calls.append(f"rm {d.name}")
    )

    def make_dir_then_fail(c: DrillContext, step: Step) -> None:
        c.drill = tmp_path / "228-drill-x"
        raise StepFailed("restic exited 1")

    assert drill.execute(ctx, tmp_path / "r.md", [("a", "x", make_dir_then_fail)]) == 1
    assert calls == ["stop", "rm 228-drill-x"]


def test_a_cleanup_failure_fails_the_run_naming_the_path(
    ctx: DrillContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def left(d: Path, sudo_n: Any) -> None:
        raise drill.DrillError(f"could not remove {d}; it is left in place")

    monkeypatch.setattr(drill, "stop_scratch_server", lambda d, run: False)
    monkeypatch.setattr(drill, "remove_drill_dir", left)
    ctx.drill = tmp_path / "228-drill-x"
    report = tmp_path / "r.md"
    assert drill.execute(ctx, report, [("a", "x", ok)]) == 1
    assert "228-drill-x; it is left in place" in report.read_text()


def test_a_partial_run_never_claims_the_full_pass(
    ctx: DrillContext, tmp_path: Path
) -> None:
    report = tmp_path / "r.md"
    assert drill.execute(ctx, report, [("a", "x", ok), ("b", "y", ok)], through=0) == 0
    text = report.read_text()
    assert "## Step 1" not in text
    assert drill.PASS_LINE not in text and "partial run" in text


def test_help_works(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        drill.main(["--help"])
    assert exited.value.code == 0
    assert "restore drill" in capsys.readouterr().out


# --- rebuild_env ---------------------------------------------------------------------


def test_the_calendar_url_is_forced_read_only_and_the_tick_urls_are_not(
    ctx: DrillContext, tmp_path: Path
) -> None:
    ctx.drill = tmp_path / "228-drill-x"
    env = rebuild_env(ctx)
    assert env["MT_TIMESCALE_DB_URL"] == f"{CALENDAR_URL}?{READ_ONLY_URL_PARAM}"
    assert "default_transaction_read_only" in env["MT_TIMESCALE_DB_URL"].replace(
        "%3D", "="
    )
    sock = str(tmp_path / "228-drill-x" / "sock")
    for key, user in [
        ("MT_TICK_DB_URL", "tick_app"),
        ("MT_TICK_MAINTENANCE_URL", "tick_migrate"),
    ]:
        assert env[key] == f"postgresql://{user}@/trading_tick_drill?host={sock}"
        assert "options" not in env[key]
    assert env["MT_TICK_ARCHIVE_DIR"] == str(
        tmp_path / "228-drill-x" / "restic-restore" / "data" / "tick-archive"
    )


def test_a_calendar_url_with_its_own_options_is_refused(
    ctx: DrillContext, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        f"MT_TIMESCALE_DB_URL={CALENDAR_URL}?options=-c%20x%3Dy\n"
    )
    ctx.drill = tmp_path / "228-drill-x"
    with pytest.raises(StepFailed, match="already sets options"):
        rebuild_env(ctx)


# --- step 6 commands ------------------------------------------------------------------


def test_the_drill_database_is_provisioned_on_the_scratch_socket_only(
    ctx: DrillContext, tmp_path: Path
) -> None:
    import drill_228_rebuild as rebuild

    ctx.drill = tmp_path / "228-drill-x"
    args = rebuild.create_drill_database(ctx)
    assert "tick_db=trading_tick_drill" in args
    target = args[args.index("-d") + 1]
    assert target == f"postgresql://postgres@/postgres?host={tmp_path}/228-drill-x/sock"
    assert args[-1].endswith("scripts/provision_tick_roles.sql")
    assert CALENDAR_URL not in " ".join(args) and "@db.example" not in " ".join(args)


def test_the_rebuild_runs_the_four_commands_adopting_each_job_in_order(
    ctx: DrillContext, tmp_path: Path
) -> None:
    import drill_228_rebuild as rebuild

    ctx.drill = tmp_path / "228-drill-x"
    commands = [" ".join(c[2:]) for c in rebuild.rebuild_commands(ctx, ["J2", "J1"])]
    restored = ctx.restored_archive
    assert commands == [
        "mt data migrate apply --track tick",
        f"mt data tick adopt --job-id J2 --source {restored}/J2",
        f"mt data tick adopt --job-id J1 --source {restored}/J1",
        "mt data tick pass --estimate-only",
        "mt data tick ingest",
    ]
