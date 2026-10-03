"""``rebuild``: the 225 pipeline from an empty proof database (LLD 226 TD2, TD3).

migrate (tick track) → adopt each archived job → ``pass --estimate-only`` →
``ingest`` → ``coverage`` over each tier's range. Each is timed; unit timing
comes from the ingest report. An estimate that plans anything stops the step:
nothing is bought here, ever.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import psycopg

from manta_trading.cli.commands.tick_exit import EXIT_OK, EXIT_PARTIAL
from manta_trading.data.tick.constants import (
    STORED_TIERS,
    TICK_PROOF_DB_NAME,
)
from proof_226.cli import CliRun, CliStepError, phase, run_mt
from proof_226.common import (
    ENV_FILE,
    EXPECTED_ROWS,
    ProofSetupError,
    ProofUrls,
    Report,
    archive_root,
    load_proof_urls,
    require_free_space,
)

#: The archive held at slice start (task 4.2's acceptance numbers).
EXPECTED_TIER_UNITS = 78
SLOWEST_UNIT_BOUND_SECONDS = 120
WHOLE_SET_BOUND_SECONDS = 2 * 3600
#: Coverage and ingest may end partial (a mismatch, a failed unit): reported
#: and judged in the report rather than aborting the step.
PARTIAL_ACCEPT = (EXIT_OK, EXIT_PARTIAL)


def archive_jobs(env_file: Path = ENV_FILE) -> list[Path]:
    """Every job directory under the archive root, sorted by name."""
    root = archive_root(env_file)
    jobs = sorted(p for p in root.iterdir() if p.is_dir())
    if not jobs:
        raise ProofSetupError(f"no job directories under {root}")
    return jobs


def _require_empty(urls: ProofUrls) -> float:
    """Connect (timed) and refuse a proof database that already holds ticks."""
    started = time.monotonic()
    with psycopg.connect(urls.maintenance_url) as conn:
        connect_seconds = time.monotonic() - started
        (name,) = conn.execute("SELECT current_database()").fetchone()  # type: ignore[misc]
        if name != TICK_PROOF_DB_NAME:
            raise ProofSetupError(f"proof URL names {name!r}, not {TICK_PROOF_DB_NAME}")
        held = conn.execute(
            "SELECT to_regclass('tick_trade') IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM tick_trade)"
        ).fetchone()
    if held and held[0]:
        raise ProofSetupError(
            "the proof database already holds ticks; rebuild starts from empty "
            "(drop and re-provision it, or use the reset in a later step)"
        )
    return connect_seconds


def _planned_requests(run: CliRun) -> list[dict[str, Any]]:
    purchase = phase(run.payload, "purchase")["summary"]
    return list(purchase.get("requests", []))


def _tier_ranges(urls: ProofUrls) -> list[tuple[str, str, str]]:
    """``(tier, first day, day after last)`` per stored tier, from the manifest."""
    with psycopg.connect(urls.db_url) as conn:
        rows = conn.execute(
            """
            SELECT request.schema
                 , min(unit.unit_date)
                 , max(unit.unit_date) + 1
              FROM tick_archive_unit AS unit
              JOIN tick_request AS request USING (request_id)
             WHERE request.schema = ANY(%s)
             GROUP BY request.schema
             ORDER BY request.schema
            """,
            (sorted(t.value for t in STORED_TIERS),),
        ).fetchall()
    return [(str(s), str(lo), str(hi)) for s, lo, hi in rows]


def _row_count(urls: ProofUrls) -> int:
    with psycopg.connect(urls.db_url) as conn:
        row = conn.execute("SELECT count(*) FROM tick_trade").fetchone()
    assert row is not None
    return int(row[0])


def _timed(report: Report, run: CliRun) -> CliRun:
    report.add(
        f"- `mt {' '.join(run.args)}`: exit {run.exit_code}, {run.seconds:.1f} s"
    )
    return run


def run() -> Path:
    urls = load_proof_urls()
    require_free_space()
    report = Report("rebuild", "Proof: rebuild from the archive (slice 226)")
    connect_seconds = _require_empty(urls)
    report.add(
        "## Steps", "", f"- connect to the proof database: {connect_seconds:.3f} s"
    )
    _timed(report, run_mt(urls, "data", "init", "--database", "tick"))
    for job in archive_jobs():
        _timed(
            report,
            run_mt(
                urls,
                "data",
                "tick",
                "adopt",
                "--job-id",
                job.name,
                "--source",
                str(job),
            ),
        )
    estimate = _timed(report, run_mt(urls, "data", "tick", "pass", "--estimate-only"))
    planned = _planned_requests(estimate)
    if planned:
        raise CliStepError(
            "the estimate plans a purchase; stopping (tell the PM, buy nothing): "
            f"{planned}"
        )
    report.add("- estimate plans nothing (no request)")
    ingest = _timed(
        report, run_mt(urls, "data", "tick", "ingest", accept=PARTIAL_ACCEPT)
    )
    _write_ingest(report, phase(ingest.payload, "ingest"), _row_count(urls))
    report.add("", "## Coverage", "")
    for tier, start, end in _tier_ranges(urls):
        cov = run_mt(
            urls,
            "data",
            "tick",
            "coverage",
            "--start",
            start,
            "--end",
            end,
            accept=PARTIAL_ACCEPT,
        )
        report.add(
            f"- {tier} {start} → {end}: exit {cov.exit_code} "
            f"({'ok' if cov.exit_code == EXIT_OK else 'MISMATCH'}), {cov.seconds:.1f} s"
        )
    return report.write()


def _write_ingest(report: Report, ingest: dict[str, Any], rows: int) -> None:
    summary = ingest["summary"]
    units = [u for u in summary["units"] if "duration_seconds" in u]
    slowest = max(units, key=lambda u: u["duration_seconds"])
    whole = ingest["duration_ms"] / 1000
    report.add("", "## Ingest", "")
    report.table(
        ("measure", "seen", "bound", "verdict"),
        [
            (
                "tier units ingested",
                summary["ingested"],
                EXPECTED_TIER_UNITS,
                _ok(summary["ingested"] == EXPECTED_TIER_UNITS),
            ),
            ("units failed", summary["failed"], 0, _ok(summary["failed"] == 0)),
            (
                "tick_trade rows (exact count)",
                rows,
                EXPECTED_ROWS,
                _ok(rows == EXPECTED_ROWS),
            ),
            (
                f"slowest unit (unit {slowest['unit_id']}, {slowest['schema']} "
                f"{slowest['unit_date']})",
                f"{slowest['duration_seconds']} s",
                f"≤ {SLOWEST_UNIT_BOUND_SECONDS} s",
                _ok(slowest["duration_seconds"] <= SLOWEST_UNIT_BOUND_SECONDS),
            ),
            (
                "whole set (ingest phase)",
                f"{whole:.1f} s",
                f"≤ {WHOLE_SET_BOUND_SECONDS} s",
                _ok(whole <= WHOLE_SET_BOUND_SECONDS),
            ),
        ],
    )
    report.table(
        ("unit", "day", "schema", "records", "seconds"),
        [
            (
                u["unit_id"],
                u["unit_date"],
                u["schema"],
                u["records"],
                u["duration_seconds"],
            )
            for u in sorted(units, key=lambda u: -u["duration_seconds"])
        ],
    )


def _ok(passed: bool) -> str:
    return "ok" if passed else "MISS"
