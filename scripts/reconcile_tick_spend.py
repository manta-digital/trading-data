"""Reconcile tick spend: estimate, manifest and Databento's charge (slice 304 D5).

Usage::

    uv run python scripts/reconcile_tick_spend.py --since 2026-11-08T00:00:00Z \\
        --cap-usd 7.00

Reads every ``tick_request`` requested since ``--since`` and Databento's batch
job list over the same span, and prints one line per request: job id, schema,
product, days, estimate, actual, delta (actual - estimate) and the charge
Databento itself lists for the job. Then the totals and
every failure.

READ ONLY. The database session is opened ``READ ONLY`` before any other
statement, and the only provider call is ``batch_jobs_since`` (``batch.list_jobs``
and ``batch.get_job_details``, both free). Slice 307 reuses this script and must
not extend it to write or to call a paid method.

``--cap-usd`` is required and is never read from the environment: the dev
checkout's ``.env`` holds the dev ceilings, not production's.

Failures (exit 1): a provider job with no manifest row; a delivered (``done``)
request with no ``actual_cost_usd``; total actual above ``--cap-usd``. A
non-zero delta is reported, never a failure: it is the result. A provider list
that cannot be read stops the script (exit 2) with the provider's error, and is
never treated as an empty list.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from pydantic import ValidationError

from manta_trading.config import Settings
from manta_trading.data.tick.constants import BatchJobState
from manta_trading.data.tick.provider import BatchJob
from manta_trading.data.tick.store_context import TickPreflightError, database_url
from manta_trading.providers.errors import ProviderError

DESCRIPTION = "Reconcile tick spend: estimate, manifest and Databento's charge."
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_STOPPED = 2
READ_ONLY_SQL = "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"
_MANIFEST_SQL = (
    "SELECT request_id, provider_job_id, schema, symbols, range_start, range_end,"
    " estimated_cost_usd, actual_cost_usd FROM tick_request"
    " WHERE requested_at >= %s ORDER BY request_id"
)


@dataclass(frozen=True)
class ManifestRow:
    request_id: int
    job_id: str | None
    schema: str
    product: str  # the request's symbols, comma-joined
    start: date
    end: date
    estimate: Decimal
    actual: Decimal | None


@dataclass(frozen=True)
class Line:
    request_id: int
    job_id: str | None
    schema: str
    product: str
    days: int
    estimate: Decimal
    actual: Decimal | None
    delta: Decimal | None  # actual - estimate; None until a charge is recorded
    charge: Decimal | None  # Databento's own per-job figure; None if not listed


@dataclass(frozen=True)
class Report:
    lines: tuple[Line, ...]
    total_estimate: Decimal
    total_actual: Decimal
    total_delta: Decimal  # sum of line deltas: only requests with an actual
    failures: tuple[str, ...]


def reconcile(
    rows: Sequence[ManifestRow], jobs: Sequence[BatchJob], cap_usd: Decimal
) -> Report:
    """Pure: judge the manifest rows against the provider's jobs."""
    by_id = {job.job_id: job for job in jobs}
    states = {job_id: job.state for job_id, job in by_id.items()}
    held = {row.job_id for row in rows if row.job_id is not None}
    failures = [
        f"provider job {job.job_id} ({job.request.schema}) has no manifest row"
        for job in jobs
        if job.job_id not in held
    ]
    lines = []
    for row in rows:
        delta = None if row.actual is None else row.actual - row.estimate
        lines.append(
            Line(
                row.request_id,
                row.job_id,
                row.schema,
                row.product,
                (row.end - row.start).days,
                row.estimate,
                row.actual,
                delta,
                by_id[row.job_id].cost_usd if row.job_id in by_id else None,
            )
        )
        delivered = (
            row.job_id is not None and states.get(row.job_id) is BatchJobState.DONE
        )
        if delivered and row.actual is None:
            failures.append(
                f"request {row.request_id} (job {row.job_id}, {row.schema}) is done"
                " at the provider but has no actual_cost_usd"
            )
    total_estimate = sum((r.estimate for r in rows), Decimal(0))
    total_actual = sum((r.actual for r in rows if r.actual is not None), Decimal(0))
    if total_actual > cap_usd:
        failures.append(f"total actual {total_actual} exceeds the cap {cap_usd}")
    total_delta = sum((ln.delta for ln in lines if ln.delta is not None), Decimal(0))
    return Report(
        tuple(lines), total_estimate, total_actual, total_delta, tuple(failures)
    )


def format_report(report: Report) -> str:
    out = ["request job_id schema product days estimate actual delta charge"]
    for ln in report.lines:
        out.append(
            f"{ln.request_id} {ln.job_id or '-'} {ln.schema} {ln.product} {ln.days}"
            f" {ln.estimate} {'-' if ln.actual is None else ln.actual}"
            f" {'-' if ln.delta is None else ln.delta}"
            f" {'-' if ln.charge is None else ln.charge}"
        )
    out.append(
        f"requests={len(report.lines)} estimate={report.total_estimate}"
        f" actual={report.total_actual} delta={report.total_delta}"
        " (delta over requests with an actual)"
    )
    out.extend(f"FAIL: {failure}" for failure in report.failures)
    return "\n".join(out)


def _manifest_row(row: tuple[Any, ...]) -> ManifestRow:
    request_id, job_id, schema, symbols, start, end, estimate, actual = row
    return ManifestRow(
        request_id, job_id, schema, ",".join(symbols), start, end, estimate, actual
    )


async def read_manifest(
    conn: psycopg.AsyncConnection[Any], since: datetime
) -> list[ManifestRow]:
    """Read-only: the session is made read-only before the first query."""
    await conn.execute(READ_ONLY_SQL)
    cursor = await conn.execute(_MANIFEST_SQL, (since,))
    return [_manifest_row(row) for row in await cursor.fetchall()]


def read_provider_jobs(settings: Settings, since: datetime) -> tuple[BatchJob, ...]:
    # Imported here: ``databento`` adds startup time to every command.
    from manta_trading.data.tick.databento.adapter import DatabentoTickProvider

    with DatabentoTickProvider.from_settings(settings) as provider:
        return provider.batch_jobs_since(since)


def _parse_since(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(f"{text!r} has no UTC offset (use ...Z)")
    return parsed.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    parser.add_argument("--since", required=True, type=_parse_since, help="UTC instant")
    parser.add_argument("--cap-usd", required=True, type=Decimal, help="total cap, USD")
    return parser


async def _run(args: argparse.Namespace, settings: Settings) -> int:
    try:
        jobs = read_provider_jobs(settings, args.since)
    except ProviderError as exc:
        print(f"STOPPED: provider job list could not be read: {exc}", file=sys.stderr)
        return EXIT_STOPPED
    async with await psycopg.AsyncConnection.connect(
        database_url(settings), autocommit=True
    ) as conn:
        rows = await read_manifest(conn, args.since)
    report = reconcile(rows, jobs, args.cap_usd)
    print(format_report(report))
    return EXIT_FAILED if report.failures else EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return asyncio.run(_run(args, Settings()))
    except (TickPreflightError, psycopg.Error, ValidationError) as exc:
        # Process boundary: the database could not be reached or configured, so
        # nothing was reconciled. A stop (2), never a judgement failure (1).
        print(f"STOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_STOPPED


if __name__ == "__main__":
    sys.exit(main())
