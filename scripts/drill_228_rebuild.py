"""Steps 6-7 of the slice 228 restore drill: the fallback path.

6 rebuilds ``trading_tick_drill`` on the scratch server from the *restored*
archive with the four commands runbook 200 gives, through ``rebuild_env``
(tick URLs on the scratch socket, the calendar read-only). Adopt re-hashes
every file against its job's ``manifest.json``, so this is also the full
archive verification.

7 compares the rebuild with the restored database: the ``tick_trade``
fingerprint (TD3) and the bookkeeping comparison (TD4).

Jobs are adopted in the restored database's ``request_id`` order. A
definition belongs to the unit that projected it first, so adopting in
production's order keeps that attribution comparable.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import psycopg
from cutover_227_helpers import Step, StepFailed
from drill_228_bookkeeping import compare_bookkeeping
from drill_228_context import (
    DRILL_DB,
    SHOWN_FAILURES,
    TICK_DB,
    DrillContext,
    check,
    connect_scratch,
    rebuild_env,
    scratch_url,
)
from drill_228_fingerprint import diff_fingerprints, fingerprint
from drill_228_host import run
from drill_228_lifecycle import MT_COMMAND_TIMEOUT, PG_BIN

PROVISION_SQL = Path(__file__).resolve().parent / "provision_tick_roles.sql"
#: The tick migration track (``mt data migrate apply --track``).
TICK_TRACK = "tick"


def create_drill_database(ctx: DrillContext) -> list[str]:
    """``provision_tick_roles.sql -v tick_db=trading_tick_drill`` on the scratch
    server's socket (TD5): the restored cluster has the roles, so this creates
    only the database, owned by tick_migrate with production's grants."""
    return [
        str(PG_BIN / "psql"),
        "-X",
        "-v",
        "ON_ERROR_STOP=1",
        "-v",
        f"tick_db={DRILL_DB}",
        "-d",
        scratch_url(ctx, "postgres"),
        "-f",
        str(PROVISION_SQL),
    ]


def job_order(restored: psycopg.Connection[Any], archive: Path) -> list[str]:
    """Restored job directories, in the restored database's request order;
    directories it doesn't know follow by name."""
    rows = restored.execute(
        "SELECT provider_job_id FROM tick_request"
        " WHERE provider_job_id IS NOT NULL ORDER BY request_id"
    ).fetchall()
    on_disk = {p.name for p in archive.iterdir() if p.is_dir()}
    known = [str(r[0]) for r in rows if str(r[0]) in on_disk]
    return [*known, *sorted(on_disk - set(known))]


def rebuild_commands(ctx: DrillContext, jobs: list[str]) -> list[list[str]]:
    mt = ["uv", "run", "mt", "data"]
    adopt = [
        [
            *mt,
            "tick",
            "adopt",
            "--job-id",
            job,
            "--source",
            str(ctx.restored_archive / job),
        ]
        for job in jobs
    ]
    return [
        [*mt, "migrate", "apply", "--track", TICK_TRACK],
        *adopt,
        [*mt, "tick", "pass", "--estimate-only"],
        [*mt, "tick", "ingest"],
    ]


def _run_mt(
    ctx: DrillContext, step: Step, args: list[str], env: dict[str, str]
) -> None:
    started = time.monotonic()
    result = subprocess.run(
        args,
        cwd=ctx.checkout,
        env=env,
        capture_output=True,
        text=True,
        timeout=MT_COMMAND_TIMEOUT.total_seconds(),
    )
    took = time.monotonic() - started
    step.seen.append(
        f"$ {' '.join(args[2:])}  (exit {result.returncode}, {took:.1f} s)"
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-SHOWN_FAILURES:]
        raise StepFailed(f"{' '.join(args[2:5])} exited {result.returncode}: {tail}")


def step_rebuild(ctx: DrillContext, step: Step) -> None:
    started = time.monotonic()
    provision = run(create_drill_database(ctx))
    check(step, f"provision {DRILL_DB} exit", 0, provision.returncode)
    with connect_scratch(ctx, TICK_DB) as restored:
        jobs = job_order(restored, ctx.restored_archive)
    step.seen.append(f"jobs to adopt, in order: {jobs}")
    env = rebuild_env(ctx)
    for args in rebuild_commands(ctx, jobs):
        _run_mt(ctx, step, args, env)
    ctx.timings["rebuild (s)"] = time.monotonic() - started


def step_compare_rebuild(ctx: DrillContext, step: Step) -> None:
    with (
        connect_scratch(ctx, TICK_DB) as restored,
        connect_scratch(ctx, DRILL_DB) as rebuilt,
    ):
        diffs = diff_fingerprints(fingerprint(restored), fingerprint(rebuilt))
        check(step, "tick_trade fingerprint differences", [], diffs[:SHOWN_FAILURES])
        result = compare_bookkeeping(restored, rebuilt)
    for (table, column), count in sorted(result.allowed_differences.items()):
        step.seen.append(f"allowed difference: {table}.{column} on {count} row(s)")
    for (table, rule), count in sorted(result.allowed_missing.items()):
        step.seen.append(f"allowed missing: {count} {table} row(s), {rule.value}")
    step.seen.append(f"allowed-missing requests: {result.allowed_missing_requests}")
    check(step, "bookkeeping failures", [], result.failures[:SHOWN_FAILURES])
