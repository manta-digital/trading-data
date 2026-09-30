"""Ingest one unit on a worker thread, in one transaction (slice 225, TD2).

:func:`ingest_unit` is plain synchronous code a thread runs. On its own
connection, in one transaction: supersede the lower-tier units the plan names
(TD5), ``COPY`` the file's records batch by batch, check the counts, insert the
ledger rows, and ``mark_ingested`` — then ``COMMIT``. A failed check rolls it
all back and is returned as a :class:`UnitOutcome`; the loop records it.

Caught here, and only these: a check failure (``UnitCheckFailed``);
``UniqueViolation`` on ``COPY`` (``overlap``: another current unit holds the
key); a decode or file error (``decode``); ``ManifestTransitionError`` (the
unit was reset or reopened mid-run: rolled back, reported "changed during
ingest"). Everything else — a lost or hung database included — propagates.

**Thread state review** (python rules; 220 TD6). The worker owns its
``ITickFile``, its ``psycopg.Connection``, its ``OrdinalCarry`` and its
``LedgerAccumulator``, all created and dropped inside one call. It receives
an immutable ``UnitIngestPlan`` and ``WorkerConnectionSettings`` and shares
nothing mutable with the loop or the other worker. The ``ITickFileReader`` is
stateless and shared; the clock is a pure function. The run's async
connection is never touched here. Only the returned ``UnitOutcome`` crosses
back to the loop; records never do.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import psycopg
from psycopg import errors

from manta_trading.data.tick.constants import STORED_TIERS, UnitState
from manta_trading.data.tick.ingest_checks import (
    IngestCheck,
    counts_reason,
    decode_reason,
    overlap_reason,
)
from manta_trading.data.tick.ingest_plan import UnitIngestPlan
from manta_trading.data.tick.ingest_records import (
    LedgerAccumulator,
    LedgerRow,
    OrdinalCarry,
    UnitCheckFailed,
    locate_sessions,
    resolve,
)
from manta_trading.data.tick.ingest_rows import COPY_SQL, COPY_TYPES, copy_rows
from manta_trading.data.tick.manifest_reads import COVERAGE_PREDICATE
from manta_trading.data.tick.manifest_repo import mark_ingested_statement
from manta_trading.data.tick.manifest_transitions import (
    ManifestTransitionError,
    execute_transition_sync,
    mark_superseded_statement,
)
from manta_trading.data.tick.provider import ITickFileReader, TickFileDecodeError

Cursor = psycopg.Cursor[Any]


@dataclass(frozen=True)
class WorkerConnectionSettings:
    """How a worker connects (TD8). Built by the pass from ``constants``."""

    url: str
    connect_timeout_seconds: int
    keepalives_idle_seconds: int
    keepalives_interval_seconds: int
    keepalives_count: int
    lock_timeout_seconds: float


class UnitResult(StrEnum):
    INGESTED = "ingested"
    FAILED = "failed"
    CHANGED_DURING_INGEST = "changed_during_ingest"


@dataclass(frozen=True)
class UnitOutcome:
    unit_id: int
    result: UnitResult
    decoded: int
    check: IngestCheck | None
    reason: str | None
    duration_seconds: float
    decode_seconds: float
    write_seconds: float


@dataclass
class _Loaded:
    """What the COPY pass counted, for the checks after it."""

    decoded: int = 0
    first_ns: int | None = None
    last_ns: int | None = None
    decode_seconds: float = 0.0


def _connect(settings: WorkerConnectionSettings) -> psycopg.Connection[Any]:
    lock_ms = int(settings.lock_timeout_seconds * 1000)
    return psycopg.connect(
        settings.url,
        connect_timeout=settings.connect_timeout_seconds,
        keepalives=1,
        keepalives_idle=settings.keepalives_idle_seconds,
        keepalives_interval=settings.keepalives_interval_seconds,
        keepalives_count=settings.keepalives_count,
        options=f"-c lock_timeout={lock_ms}",
    )


def supersede(cur: Cursor, plan: UnitIngestPlan) -> None:
    """Link each superseded unit to this one; delete an ingested one's rows.

    The delete is bounded by that unit's own ledger times, so chunk exclusion
    limits it to the chunks the unit touched. Its ledger rows stay (TD5).
    """
    for old in plan.superseded:
        execute_transition_sync(
            cur, mark_superseded_statement(old.unit_id, plan.unit.unit_id)
        )
        if old.state is UnitState.INGESTED and old.first_event_ns is not None:
            cur.execute(
                "DELETE FROM tick_trade WHERE unit_id = %s"
                " AND ts_event BETWEEN %s AND %s",
                (old.unit_id, old.first_event_ns, old.last_event_ns),
            )


def _copy(
    cur: Cursor, plan: UnitIngestPlan, path: Path, reader: ITickFileReader
) -> tuple[_Loaded, list[LedgerRow]]:
    loaded, carry, ledger = _Loaded(), OrdinalCarry(), LedgerAccumulator()
    batches = reader.open_file(path).iter_batches()
    with cur.copy(COPY_SQL) as copy:
        copy.set_types(list(COPY_TYPES))
        while True:
            started = time.monotonic()
            batch = next(batches, None)
            loaded.decode_seconds += time.monotonic() - started
            if batch is None:
                break
            records = batch.records
            ids, ts = records["instrument_id"], records["ts_event"]
            resolve(plan.definitions, ids, ts, plan.product)
            positions = locate_sessions(plan.frame, ts)
            ordinals = carry.ordinals(ids, ts, records["sequence"])
            ledger.add(ids, positions, records["size"], ts)
            for row in copy_rows(records, ordinals, plan.unit.unit_id):
                copy.write_row(row)
            if batch.count:
                low, high = int(ts.min()), int(ts.max())
                loaded.first_ns = (
                    low if loaded.first_ns is None else min(loaded.first_ns, low)
                )
                loaded.last_ns = (
                    high if loaded.last_ns is None else max(loaded.last_ns, high)
                )
            loaded.decoded += batch.count
    return loaded, ledger.rows(plan.definitions, plan.frame, plan.calendar_id)


def _check_counts(cur: Cursor, plan: UnitIngestPlan, loaded: _Loaded) -> None:
    """provider = decoded = stored rows of this unit, bounded by its time range."""
    stored = 0
    if loaded.first_ns is not None:
        cur.execute(
            "SELECT count(*) FROM tick_trade WHERE unit_id = %s"
            " AND ts_event BETWEEN %s AND %s",
            (plan.unit.unit_id, loaded.first_ns, loaded.last_ns),
        )
        stored = int(cur.fetchone()[0])  # type: ignore[index]
    provider = plan.unit.provider_record_count
    if not provider == loaded.decoded == stored:
        reason = counts_reason(provider, loaded.decoded, stored)
        raise UnitCheckFailed(IngestCheck.COUNTS, reason)


def _insert_ledger(cur: Cursor, unit_id: int, rows: list[LedgerRow]) -> None:
    cur.executemany(
        "INSERT INTO tick_ingest_ledger (unit_id, instrument_id, calendar_id,"
        " session_date, record_count, volume, first_event_ns, last_event_ns)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                unit_id,
                row.instrument_id,
                row.calendar_id,
                row.session_date,
                row.record_count,
                row.volume,
                row.first_event_ns,
                row.last_event_ns,
            )
            for row in rows
        ],
    )


def _other_current_units(
    conn: psycopg.Connection[Any], plan: UnitIngestPlan
) -> list[int]:
    """Current tier units of the same dataset and day, other than this one."""
    rows = conn.execute(
        "SELECT u.unit_id FROM tick_archive_unit u JOIN tick_request r"
        " USING (request_id) WHERE r.dataset = %s AND u.unit_date = %s"
        f" AND u.unit_id <> %s AND r.schema = ANY(%s) AND {COVERAGE_PREDICATE}"
        " ORDER BY u.unit_id",
        (
            plan.unit.dataset,
            plan.unit.unit_date,
            plan.unit.unit_id,
            sorted(tier.value for tier in STORED_TIERS),
        ),
    ).fetchall()
    return [int(row[0]) for row in rows]


def ingest_unit(
    plan: UnitIngestPlan,
    archive_root: Path,
    settings: WorkerConnectionSettings,
    reader: ITickFileReader,
    clock: Callable[[], datetime],
) -> UnitOutcome:
    """Load one unit in one transaction; see the module docstring."""
    started = time.monotonic()
    unit = plan.unit
    assert unit.file is not None, f"unit {unit.unit_id} is verified with no file"
    path = archive_root / unit.file.path
    loaded = _Loaded()

    def outcome(
        result: UnitResult, failure: UnitCheckFailed | None = None
    ) -> UnitOutcome:
        duration = time.monotonic() - started
        return UnitOutcome(
            unit_id=unit.unit_id,
            result=result,
            decoded=loaded.decoded,
            check=None if failure is None else failure.check,
            reason=None if failure is None else failure.reason,
            duration_seconds=duration,
            decode_seconds=loaded.decode_seconds,
            write_seconds=duration - loaded.decode_seconds,
        )

    with _connect(settings) as conn:
        try:
            with conn.transaction(), conn.cursor() as cur:
                supersede(cur, plan)
                loaded, ledger = _copy(cur, plan, path, reader)
                _check_counts(cur, plan, loaded)
                _insert_ledger(cur, unit.unit_id, ledger)
                statement = mark_ingested_statement(
                    unit.unit_id, loaded.decoded, clock()
                )
                execute_transition_sync(cur, statement)
        except UnitCheckFailed as failure:
            return outcome(UnitResult.FAILED, failure)
        except errors.UniqueViolation as exc:
            reason = overlap_reason(
                exc.diag.message_detail,
                unit.unit_date,
                _other_current_units(conn, plan),
            )
            return outcome(
                UnitResult.FAILED, UnitCheckFailed(IngestCheck.OVERLAP, reason)
            )
        except (TickFileDecodeError, OSError) as exc:
            decode = UnitCheckFailed(IngestCheck.DECODE, decode_reason(path, exc))
            return outcome(UnitResult.FAILED, decode)
        except ManifestTransitionError:
            # Reset or reopened by an operator mid-run: rolled back, not a failure.
            return outcome(UnitResult.CHANGED_DURING_INGEST)
    return outcome(UnitResult.INGESTED)
