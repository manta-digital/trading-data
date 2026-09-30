"""Every write to ``tick_request`` and ``tick_archive_unit`` (223).

Reads and the row types are in ``manifest_reads.py``.

LLD 224 Technical Decision 2 (compare-and-set): each unit ``UPDATE`` names,
in its ``WHERE``, the ``state`` and ``fetch_status`` it moves from (and
``reopened_at IS NULL`` where TD8 requires). An update matching zero rows
raises :class:`ManifestTransitionError`; nothing is skipped quietly, so the
advisory lock is not what keeps concurrent writers correct.

These are per-unit primitives only. Deciding which one applies to a unit is
the caller's (``reset.py`` for the reset classification). ``fetch_status`` is
the failure lifecycle of the *next* step (222): advancing a unit resets it to
``UNKNOWN`` with no reason and no attempts. 224 adds its functions here
(submit, reconcile, sweep, trailing spend, supersession).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import LiteralString

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.quality.fetch_status import OPEN_FETCH_STATUSES, FetchStatus
from manta_trading.data.tick.constants import DeliveryMode, SType, TickSchema, UnitState
from manta_trading.data.tick.manifest_reads import Conn, UnitFile

#: The reason a delivered job's missing day is recorded with (TD8).
PROVIDER_HOLE_REASON = "job delivered no file for a session day"

_OPEN = [status.value for status in OPEN_FETCH_STATUSES]


class ManifestTransitionError(Exception):
    """A compare-and-set update matched no row: the unit is not where expected."""

    def __init__(self, unit_id: int, expected: str) -> None:
        self.unit_id = unit_id
        super().__init__(f"unit {unit_id} is not {expected}; manifest unchanged")


@dataclass(frozen=True)
class AdoptedRequest:
    """An adopted job's request row: every fact from the provider's job record."""

    job_id: str
    dataset: str
    schema: TickSchema
    symbols: tuple[str, ...]
    stype_in: SType
    range_start: date
    range_end: date
    cost_usd: Decimal
    record_count: int | None
    billed_size: int | None
    ts_received: datetime


@dataclass(frozen=True)
class NewUnit:
    """One unit to insert with its request; ``file`` is set from *downloaded*."""

    unit_date: date
    state: UnitState
    fetch_status: FetchStatus = FetchStatus.UNKNOWN
    failure_reason: str | None = None
    file: UnitFile | None = None


# -- requests -------------------------------------------------------------------


async def insert_adopted_request(
    conn: Conn, request: AdoptedRequest, units: Sequence[NewUnit], now: datetime
) -> dict[date, int]:
    """One transaction: the adopted request and its units (TD10). Unit ids by day.

    ``requested_at`` and ``committed_at`` are the provider's ``ts_received``,
    so rebuilding the manifest from the archive yields identical rows.
    """
    async with conn.transaction():
        cursor = await conn.execute(
            "INSERT INTO tick_request (dataset, schema, symbols, stype_in,"
            " range_start, range_end, delivery_mode, is_adopted, provider_job_id,"
            " estimated_cost_usd, actual_cost_usd, provider_record_count,"
            " billed_size_bytes, requested_at, committed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING request_id",
            (
                request.dataset,
                request.schema.value,
                list(request.symbols),
                request.stype_in.value,
                request.range_start,
                request.range_end,
                DeliveryMode.BATCH_JOB.value,
                request.job_id,
                request.cost_usd,
                request.cost_usd,
                request.record_count,
                request.billed_size,
                request.ts_received,
                request.ts_received,
            ),
        )
        row = await cursor.fetchone()
        assert row is not None  # RETURNING on a successful INSERT
        request_id = int(row[0])
        return {
            unit.unit_date: await _insert_unit(conn, request_id, unit, now)
            for unit in units
        }


async def _insert_unit(
    conn: Conn, request_id: int, unit: NewUnit, now: datetime
) -> int:
    file = unit.file
    cursor = await conn.execute(
        "INSERT INTO tick_archive_unit (request_id, unit_date, state,"
        " state_changed_at, fetch_status, failure_reason, attempt_count,"
        " file_path, file_size_bytes, file_sha256)"
        " VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s) RETURNING unit_id",
        (
            request_id,
            unit.unit_date,
            unit.state.value,
            now,
            unit.fetch_status.value,
            unit.failure_reason,
            None if file is None else file.path,
            None if file is None else file.size,
            None if file is None else file.sha256,
        ),
    )
    row = await cursor.fetchone()
    assert row is not None  # RETURNING on a successful INSERT
    return int(row[0])


# -- compare-and-set transitions --------------------------------------------------


async def _transition(
    conn: Conn,
    unit_id: int,
    set_sql: LiteralString,
    where_sql: LiteralString,
    params: tuple[object, ...],
    expected: str,
) -> None:
    """One compare-and-set ``UPDATE`` in its own transaction; zero rows raises."""
    async with conn.transaction():
        cursor = await conn.execute(
            f"UPDATE tick_archive_unit SET {set_sql}"
            f" WHERE {where_sql} AND unit_id = %s",
            (*params, unit_id),
        )
        if cursor.rowcount != 1:
            raise ManifestTransitionError(unit_id, expected)


ADVANCE_SET: LiteralString = (
    "state = %s, state_changed_at = %s, fetch_status = %s,"
    " failure_reason = NULL, attempt_count = 0"
)


def advance_params(state: UnitState, now: datetime) -> tuple[object, ...]:
    """Parameters for ``ADVANCE_SET``: the new state, no failure outstanding."""
    return (state.value, now, FetchStatus.UNKNOWN.value)


_OPEN_FROM: LiteralString = (
    "state = %s AND fetch_status = ANY(%s) AND reopened_at IS NULL"
)


async def mark_downloaded(
    conn: Conn, unit_id: int, file: UnitFile, now: datetime
) -> None:
    """*delivered* → *downloaded*, recording the file."""
    await _transition(
        conn,
        unit_id,
        f"{ADVANCE_SET}, file_path = %s, file_size_bytes = %s, file_sha256 = %s",
        _OPEN_FROM,
        advance_params(UnitState.DOWNLOADED, now)
        + (file.path, file.size, file.sha256, UnitState.DELIVERED.value, _OPEN),
        f"{UnitState.DELIVERED} and open",
    )


async def mark_verified(
    conn: Conn, unit_id: int, provider_record_count: int, now: datetime
) -> None:
    """*downloaded* → *verified*, storing the provider's count for the day."""
    await _transition(
        conn,
        unit_id,
        f"{ADVANCE_SET}, provider_record_count = %s",
        _OPEN_FROM,
        advance_params(UnitState.VERIFIED, now)
        + (provider_record_count, UnitState.DOWNLOADED.value, _OPEN),
        f"{UnitState.DOWNLOADED} and open",
    )


async def mark_ingested(
    conn: Conn, unit_id: int, decoded_record_count: int, now: datetime
) -> None:
    """*verified* → *ingested*, storing the decoded count. For a definition
    unit this means "projected into ``tick_definition``" (TD5)."""
    await _transition(
        conn,
        unit_id,
        f"{ADVANCE_SET}, decoded_record_count = %s",
        _OPEN_FROM,
        advance_params(UnitState.INGESTED, now)
        + (decoded_record_count, UnitState.VERIFIED.value, _OPEN),
        f"{UnitState.VERIFIED} and open",
    )


async def record_failure(
    conn: Conn,
    unit_id: int,
    state: UnitState,
    reason: str,
    now: datetime,
    *,
    deterministic: bool,
) -> None:
    """A failed next step. Transient: attempt + 1, exhausted at
    ``MAX_RETRY_COUNT``. Deterministic: straight to ``RETRY_EXHAUSTED``."""
    exhausted = FetchStatus.RETRY_EXHAUSTED.value
    await _transition(
        conn,
        unit_id,
        "attempt_count = attempt_count + 1, last_attempt_at = %s,"
        " failure_reason = %s, fetch_status = CASE"
        " WHEN %s OR attempt_count + 1 >= %s THEN %s ELSE %s END",
        _OPEN_FROM,
        (now, reason, deterministic, MAX_RETRY_COUNT, exhausted)
        + (FetchStatus.FAILED_RETRYABLE.value, state.value, _OPEN),
        f"{state} and open",
    )


async def mark_provider_hole(conn: Conn, unit_id: int, now: datetime) -> None:
    """A *delivered* unit whose job has no file for its day (TD8)."""
    await _transition(
        conn,
        unit_id,
        "fetch_status = %s, failure_reason = %s, last_attempt_at = %s",
        _OPEN_FROM,
        (FetchStatus.PROVIDER_HOLE.value, PROVIDER_HOLE_REASON, now)
        + (UnitState.DELIVERED.value, _OPEN),
        f"{UnitState.DELIVERED} and open",
    )


async def reset_exhausted(conn: Conn, unit_id: int) -> None:
    """``RETRY_EXHAUSTED``, not reopened → ``UNKNOWN``, no attempts (TD8, reset)."""
    await _transition(
        conn,
        unit_id,
        "fetch_status = %s, failure_reason = NULL, attempt_count = 0",
        "fetch_status = %s AND reopened_at IS NULL",
        (FetchStatus.UNKNOWN.value, FetchStatus.RETRY_EXHAUSTED.value),
        f"{FetchStatus.RETRY_EXHAUSTED} and not reopened",
    )


async def reopen_hole(conn: Conn, unit_id: int, now: datetime) -> None:
    """``PROVIDER_HOLE``, not reopened → ``reopened_at = now`` (TD8)."""
    await _transition(
        conn,
        unit_id,
        "reopened_at = %s",
        "fetch_status = %s AND reopened_at IS NULL",
        (now, FetchStatus.PROVIDER_HOLE.value),
        f"{FetchStatus.PROVIDER_HOLE} and not reopened",
    )
