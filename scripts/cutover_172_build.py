"""Database side of the slice 172 cutover: the ``_next`` build's state.

Everything here takes a connection with dict rows (``cagg_repair``'s job
resolver reads rows by column name) and reads or flips catalog state; the
cutover script owns the order and the host. Jobs are resolved from the catalog
by view name at use time, never by id (ids change when a cagg is recreated).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psycopg
from psycopg import sql

from manta_trading.constants import Granularity
from manta_trading.market.maintenance.cagg_repair import (
    PROC_REFRESH,
    REPAIR_RUN_ORDER,
    CaggJob,
    resolve_cagg_jobs,
)
from manta_trading.market.maintenance.off_tape_sweep import unevaluated_chunks
from manta_trading.market.schema.eligible_rollups_sql import (
    NEXT_COVERAGE_VIEW,
    NEXT_ROLLUP_VIEWS,
    next_rollup_view,
)

#: The rollups in build order: 4h (the coverage parent) first, as repair wants.
BUILD_ORDER: tuple[tuple[Granularity, str], ...] = tuple(
    (g, next_rollup_view(g)) for g in REPAIR_RUN_ORDER
)
NEXT_VIEWS: tuple[str, ...] = (*NEXT_ROLLUP_VIEWS, NEXT_COVERAGE_VIEW)

_BYTES_PER_GB = 1024**3


class BuildRefused(RuntimeError):
    """The database is not in a state the build will start from."""


def require_sweep_complete(conn: psycopg.Connection[Any]) -> None:
    """Refuse while any minute chunk still holds an unevaluated mark.

    A sweep ``UPDATE`` after a window is built only invalidates it and the
    refresh policies look back 10 days; repair checks counts, not values, so
    an older window built from unswept rows would stay wrong (design, Build
    order). The whole table is checked, not one session: the sweep runs oldest
    first, so the newest session is marked long before the history is.
    """
    pending = unevaluated_chunks(conn)
    if pending:
        raise BuildRefused(
            f"{len(pending)} minute chunk(s) still hold unevaluated off-tape "
            f"marks (oldest {pending[0].name}). The history sweep must finish "
            "first: mt data off-tape sweep --dry-run shows what remains."
        )


def _pg_lsclusters() -> str:
    return subprocess.run(
        ["pg_lsclusters", "--no-header"], check=True, text=True, capture_output=True
    ).stdout


def _require_local(host: str) -> None:
    """The volume is measured on this host, so the database must be here."""
    with socket.socket() as probe:
        try:
            probe.bind((host, 0))
        except OSError as exc:
            raise BuildRefused(
                f"the database host {host} is not this machine; free space on "
                "its volume cannot be measured here"
            ) from exc


def data_directory(port: int, clusters: str) -> str:
    """The data directory of the local cluster on ``port``, from
    ``pg_lsclusters`` (version, name, port, status, owner, directory, log)."""
    for line in clusters.splitlines():
        fields = line.split()
        if len(fields) >= 6 and fields[2] == str(port):
            return fields[5]
    raise BuildRefused(f"pg_lsclusters lists no cluster on port {port}")


def headroom_gb(
    url: str,
    read_clusters: Callable[[], str] = _pg_lsclusters,
    disk_usage: Callable[[str], Any] = shutil.disk_usage,
) -> float:
    """Free GB on the database volume, measured, for repair's attestation.

    Not ``SHOW data_directory``: the migration role lacks
    ``pg_read_all_settings`` (checked on production 2026-10-16), and
    ``pg_lsclusters`` needs no privilege.
    """
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise BuildRefused("the maintenance URL names no host")
    _require_local(parsed.hostname)
    try:
        directory = data_directory(parsed.port or 5432, read_clusters())
        free = disk_usage(directory).free
    except (OSError, subprocess.SubprocessError) as exc:
        raise BuildRefused(f"could not measure the database volume: {exc}") from exc
    return round(free / _BYTES_PER_GB, 1)


def jobs(conn: psycopg.Connection[Any], view: str) -> list[CaggJob]:
    return resolve_cagg_jobs(conn, view)


def set_scheduled(
    conn: psycopg.Connection[Any], view_jobs: list[CaggJob], scheduled: bool
) -> list[int]:
    """Pause or resume the given jobs; returns their ids."""
    for job in view_jobs:
        conn.execute("SELECT alter_job(%s, scheduled => %s)", (job.job_id, scheduled))
    return [job.job_id for job in view_jobs]


def paused_next_jobs(conn: psycopg.Connection[Any]) -> list[CaggJob]:
    """``_next`` jobs left paused, e.g. by a killed run."""
    return [job for view in NEXT_VIEWS for job in jobs(conn, view) if not job.scheduled]


def row_counts(conn: psycopg.Connection[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for view in NEXT_VIEWS:
        query = sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(view))
        row = conn.execute(query).fetchone()
        if row is None:
            raise BuildRefused(f"count(*) on {view} returned no row")
        counts[view] = int(row["n"])
    return counts


@dataclass(frozen=True)
class RefreshLoad:
    view: str
    last_run_duration: timedelta | None
    schedule_interval: timedelta


def refresh_load(conn: psycopg.Connection[Any]) -> list[RefreshLoad]:
    """Each ``_next`` refresh job's last run duration against its schedule."""
    rows = conn.execute(
        "SELECT j.hypertable_name AS view, s.last_run_duration AS duration,"
        " j.schedule_interval AS schedule"
        " FROM timescaledb_information.jobs j"
        " LEFT JOIN timescaledb_information.job_stats s ON s.job_id = j.job_id"
        " WHERE j.hypertable_name = ANY(%s) AND j.proc_name = %s"
        " ORDER BY j.hypertable_name",
        (list(NEXT_VIEWS), PROC_REFRESH),
    ).fetchall()
    return [RefreshLoad(r["view"], r["duration"], r["schedule"]) for r in rows]


def report_path(notes_dir: Path, started: str) -> Path:
    return notes_dir / f"{started[:10]}-172-cutover.md"


def json_list(output: str) -> list[dict[str, Any]]:
    """The JSON array in a command's output; log lines may surround it."""
    decoder = json.JSONDecoder()
    for index, char in enumerate(output):
        if char != "[":
            continue
        try:
            value, _ = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue  # a "[" inside a log line, not the payload
        if isinstance(value, list):
            return value
    raise BuildRefused(f"no JSON report in the output: {output[-500:]}")


def render_report(
    started: str,
    ref: str,
    ok: bool,
    steps: list[tuple[str, str, list[str]]],
    figures: tuple[dict[str, int], list[RefreshLoad]] | None,
    detail: str,
) -> str:
    """The cutover report. ``figures`` is None when the database could not be
    read, and ``detail`` then says why; otherwise it is the verify output."""
    lines = [f"# Slice 172 cutover — {started}", "", f"Ref: {ref}", ""]
    lines += [f"Outcome: {'PASS' if ok else 'FAIL'}", "", "## Steps", ""]
    for title, outcome, seen in steps:
        lines.append(f"- **{title}**: {outcome}")
        lines += [f"  - {text}" for text in seen]
    if figures is None:
        lines += ["", f"Database figures unavailable: {detail}"]
        return "\n".join(lines) + "\n"
    counts, load = figures
    lines += ["", "## Rows per _next view", ""]
    lines += [f"- {view}: {count:,}" for view, count in counts.items()]
    lines += ["", "## _next refresh jobs: last run vs schedule", ""]
    lines += [
        f"- {r.view}: {r.last_run_duration or 'never run'} every {r.schedule_interval}"
        for r in load
    ]
    if detail:
        lines += ["", "## verify --target next", "", "```", detail.rstrip(), "```"]
    return "\n".join(lines) + "\n"
