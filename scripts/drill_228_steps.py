"""Steps 0-5 of the slice 228 restore drill: the primary restore path.

0 prepare, 1 hold production, 2 make the restore point current, 3 restore
the archive, 4 restore the database, 5 compare it with production. Each step
records expected against seen and raises ``StepFailed`` (or ``DrillError``)
naming the first difference. Production is only read; the one write is step
2's ``pg_switch_wal``.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from cutover_227_helpers import Step, StepFailed
from cutover_227_host import TICK_LOCK_KEYS
from drill_228_bookkeeping import BOOKKEEPING_TABLES, table_md5
from drill_228_context import (
    DrillContext,
    check,
    connect_production,
    connect_scratch,
    scratch_url,
)
from drill_228_fingerprint import diff_fingerprints, fingerprint
from drill_228_host import (
    backed_up_set,
    check_archive_against_snapshot,
    load_excludes,
    parse_restic_ls,
    postgres_value,
    prime_sudo,
    restic,
    run,
    sudo_n,
)
from drill_228_lifecycle import (
    BASE_EXTRACT_TIMEOUT,
    DRILL_ROOT,
    PG_BIN,
    RESTIC_TIMEOUT,
    RESTORE_NAME,
    VERIFYBACKUP_TIMEOUT,
    WAL_WAIT,
    acquire_drill_lock,
    assert_drill_path,
    create_drill_dir,
    sweep_leftovers,
)
from drill_228_scratch import (
    PRIMARY_SETTINGS,
    check_reached,
    empty_auto_conf,
    last_restored_segment,
    start_scratch,
    write_scratch_conf,
)
from psycopg import sql

#: Room for the archive copy and a scratch server holding both databases.
SPACE_FACTOR = 3
_BASE_NAME = re.compile(r"^\d{8}$")
_EVIDENCE_LINES = 8
_WAL_POLL_SECONDS = 2.0


def _tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def latest_base(ctx: DrillContext) -> Path:
    bases = sorted(p for p in ctx.base_dir.iterdir() if _BASE_NAME.match(p.name))
    if not bases:
        raise StepFailed(f"no base backup under {ctx.base_dir}")
    return bases[-1]


# --- Step 0 ---------------------------------------------------------------------


def step_prepare(ctx: DrillContext, step: Step) -> None:
    ctx.lock = acquire_drill_lock()
    step.seen.append(f"drill lock held: {ctx.lock.path}")
    prime_sudo()
    removed = sweep_leftovers(ctx.lock, run, sudo_n)
    step.seen.append(f"leftovers removed: {[str(p) for p in removed] or 'none'}")
    ctx.base = latest_base(ctx)
    footprint = _tree_bytes(ctx.archive) + _tree_bytes(ctx.base)
    free = shutil.disk_usage(DRILL_ROOT).free
    step.seen.append(
        f"free space: need {SPACE_FACTOR} x {footprint} bytes (archive + base "
        f"{ctx.base.name}), seen {free} free on {DRILL_ROOT}"
    )
    if free < SPACE_FACTOR * footprint:
        raise StepFailed(
            f"not enough space: need {SPACE_FACTOR * footprint}, have {free}"
        )
    ctx.drill = create_drill_dir(ctx.stamp)
    step.seen.append(f"drill directory: {ctx.drill}")


# --- Step 1 ---------------------------------------------------------------------


def _take_locks(prod: psycopg.Connection[Any], step: Step) -> None:
    for key in TICK_LOCK_KEYS:
        row = prod.execute("SELECT pg_try_advisory_lock(%s)", (key,)).fetchone()
        check(step, f"tick advisory lock {key} taken", True, row is not None and row[0])


def _privileges(prod: psycopg.Connection[Any], step: Step) -> None:
    rows = prod.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        " AND NOT has_table_privilege(format('public.%I', tablename), 'SELECT')"
    ).fetchall()
    check(step, "public tables without SELECT", [], [r[0] for r in rows])


def _settings(ctx: DrillContext, prod: psycopg.Connection[Any], step: Step) -> None:
    rows = prod.execute(
        "SELECT name, setting FROM pg_settings WHERE name = ANY(%s)",
        (list(PRIMARY_SETTINGS),),
    ).fetchall()
    ctx.prod_settings = {str(n): str(v) for n, v in rows}
    check(step, "settings read", sorted(PRIMARY_SETTINGS), sorted(ctx.prod_settings))
    step.seen.append(f"production settings: {ctx.prod_settings}")


def step_hold(ctx: DrillContext, step: Step) -> None:
    ctx.prod = connect_production(ctx)
    _take_locks(ctx.prod, step)
    _privileges(ctx.prod, step)
    _settings(ctx, ctx.prod, step)
    excludes = load_excludes()
    ctx.live_set = backed_up_set(ctx.archive, excludes)
    step.seen.append(f"live archive backed-up set: {ctx.live_set.describe()}")
    listing = restic(
        ctx.env_file,
        ["ls", "--json", "--recursive", "latest", str(ctx.archive)],
        RESTIC_TIMEOUT.total_seconds(),
    )
    ctx.evidence["restic ls --json (first lines)"] = listing.splitlines()[
        :_EVIDENCE_LINES
    ]
    snapshot = parse_restic_ls(listing, excludes)
    step.seen.append(
        f"snapshot {snapshot.time.isoformat()}: {snapshot.files.count} files, "
        f"{snapshot.files.bytes} bytes"
    )
    check_archive_against_snapshot(ctx.live_set, snapshot)


# --- Step 2 ---------------------------------------------------------------------


def step_switch(ctx: DrillContext, step: Step) -> None:
    sql = "SELECT pg_walfile_name(pg_switch_wal())"
    ctx.needed_segment = postgres_value(ctx.tick.cluster, sql)
    step.seen.append(f"segment the restore must reach: {ctx.needed_segment}")
    shapes = [
        ctx.wal_dir / f"{ctx.needed_segment}.zst",
        ctx.wal_dir / ctx.needed_segment,
    ]
    deadline = time.monotonic() + WAL_WAIT.total_seconds()
    while not any(p.exists() for p in shapes):
        if time.monotonic() > deadline:
            raise StepFailed(f"{ctx.needed_segment} not archived within {WAL_WAIT}")
        time.sleep(_WAL_POLL_SECONDS)
    step.seen.append(f"archived: {next(p for p in shapes if p.exists())}")


# --- Step 3 ---------------------------------------------------------------------


def step_restore_archive(ctx: DrillContext, step: Step) -> None:
    target = ctx.need_drill() / RESTORE_NAME
    target.mkdir()
    started = time.monotonic()
    restic(
        ctx.env_file,
        ["restore", "latest", "--target", str(target), "--include", str(ctx.archive)],
        RESTIC_TIMEOUT.total_seconds(),
    )
    ctx.timings["restic restore (s)"] = time.monotonic() - started
    restored = assert_drill_path(ctx.restored_archive)
    owner = f"{os.getuid()}:{os.getgid()}"
    result = sudo_n(["chown", "-R", owner, str(restored)])
    check(step, f"chown -R {owner} {restored} exit", 0, result.returncode)
    seen = backed_up_set(restored, load_excludes(), logical_root=ctx.archive)
    assert ctx.live_set is not None  # step 1 sets it
    check(step, "restored files", ctx.live_set.count, seen.count)
    check(step, "restored bytes", ctx.live_set.bytes, seen.bytes)


# --- Step 4 ---------------------------------------------------------------------


def _extract(ctx: DrillContext, step: Step) -> None:
    assert ctx.base is not None  # step 0 sets it
    ctx.pgdata.mkdir(mode=0o700)
    (ctx.pgdata / "pg_wal").mkdir(exist_ok=True)
    started = time.monotonic()
    timeout = BASE_EXTRACT_TIMEOUT.total_seconds()
    for archive, into in [
        ("base.tar.gz", ctx.pgdata),
        ("pg_wal.tar.gz", ctx.pgdata / "pg_wal"),
    ]:
        result = run(["tar", "-xzf", str(ctx.base / archive), "-C", str(into)], timeout)
        check(step, f"tar -xzf {archive} exit", 0, result.returncode)
    ctx.pgdata.chmod(0o700)  # the server refuses a group- or world-readable dir
    ctx.timings["base extraction (s)"] = time.monotonic() - started
    verify = [str(PG_BIN / "pg_verifybackup"), "-m", str(ctx.base / "backup_manifest")]
    result = run([*verify, str(ctx.pgdata)], VERIFYBACKUP_TIMEOUT.total_seconds())
    check(step, "pg_verifybackup exit", 0, result.returncode)
    step.seen.append(result.stdout.strip())


def _in_recovery(ctx: DrillContext) -> bool | None:
    try:
        with psycopg.connect(scratch_url(ctx, "postgres"), connect_timeout=2) as conn:
            row = conn.execute("SELECT pg_is_in_recovery()").fetchone()
    except psycopg.OperationalError:
        return None  # not accepting connections yet: keep waiting
    return None if row is None else bool(row[0])


def step_restore_database(ctx: DrillContext, step: Step) -> None:
    _extract(ctx, step)
    empty_auto_conf(ctx.pgdata)
    step.seen.append("postgresql.auto.conf emptied; no archive setting left")
    write_scratch_conf(ctx.pgdata, ctx.sockdir, ctx.prod_settings, ctx.wal_dir)
    ctx.timings["recovery (s)"] = start_scratch(
        ctx.pgdata, ctx.server_log, run, lambda: _in_recovery(ctx)
    )
    log = ctx.server_log.read_text()
    ctx.evidence["server log (restored lines, last)"] = [
        line for line in log.splitlines() if "restored log file" in line
    ][-_EVIDENCE_LINES:]
    seen = last_restored_segment(log)
    step.seen.append(f"last restored segment: {seen}; needed {ctx.needed_segment}")
    check_reached(seen, ctx.needed_segment)


# --- Step 5 ---------------------------------------------------------------------


def _row_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    tables = conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
    ).fetchall()
    counts = {}
    for (table,) in tables:
        query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
        row = conn.execute(query).fetchone()
        counts[str(table)] = int(row[0]) if row else -1
    return counts


def _locks_still_held(prod: psycopg.Connection[Any]) -> int:
    row = prod.execute(
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted"
        " AND pid = pg_backend_pid() AND classid = 0 AND objsubid = 1"
        " AND objid = ANY(%s)",
        (list(TICK_LOCK_KEYS),),
    ).fetchone()
    return int(row[0]) if row else 0


def step_compare_production(ctx: DrillContext, step: Step) -> None:
    if ctx.prod is None:
        raise StepFailed("no production lock connection (step 1 did not run)")
    started = time.monotonic()
    with connect_scratch(ctx) as restored:
        check(step, "row counts", _row_counts(ctx.prod), _row_counts(restored))
        diffs = diff_fingerprints(fingerprint(ctx.prod), fingerprint(restored))
        check(step, "tick_trade fingerprint differences", [], diffs[:5])
        for table in BOOKKEEPING_TABLES:
            check(
                step,
                f"{table} md5",
                table_md5(ctx.prod, table),
                table_md5(restored, table),
            )
    ctx.timings["compare with production (s)"] = time.monotonic() - started
    check(
        step,
        "tick advisory locks still held",
        len(TICK_LOCK_KEYS),
        _locks_still_held(ctx.prod),
    )
    ctx.prod.execute("SELECT pg_advisory_unlock_all()")
    ctx.prod.close()
    ctx.prod = None
    step.seen.append(
        f"locks released {datetime.now(UTC).isoformat(timespec='seconds')}"
    )
