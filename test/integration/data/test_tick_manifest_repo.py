"""Compare-and-set manifest transitions on a migrated tick database (223).

LLD 224 Technical Decision 2: each transition succeeds from its expected
state and raises ``ManifestTransitionError`` from any other, leaving the row
unchanged. Rows are set up with ``tick_support.rows``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit

from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.constants import (
    CME_DATASET,
    UNIT_STATES_WITH_FILE,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import (
    UnitFile,
    request_id_for_job,
    units_by_id,
    units_of_request,
)
from manta_trading.data.tick.manifest_repo import (
    PROVIDER_HOLE_REASON,
    AdoptedRequest,
    ManifestTransitionError,
    NewUnit,
    insert_adopted_request,
    mark_downloaded,
    mark_provider_hole,
    mark_verified,
    record_failure,
    reopen_hole,
    reset_exhausted,
)

AConn = psycopg.AsyncConnection[Any]
Conn = psycopg.Connection[Any]
NOW = datetime(2026, 9, 28, 12, tzinfo=UTC)
FILE = UnitFile("JOB/glbx-mdp3-20240903.trades.dbn.zst", 1024, "a" * 64)

Transition = Callable[[AConn, int], Awaitable[None]]


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[AConn]:
    async with await psycopg.AsyncConnection.connect(
        migrated_tick_db, autocommit=True
    ) as conn:
        yield conn


def _unit(
    conn: Conn,
    state: UnitState,
    status: FetchStatus = FetchStatus.UNKNOWN,
    *,
    reopened: bool = False,
) -> int:
    """One unit at ``state``/``status``, valid under every 222/223 CHECK."""
    columns: dict[str, Any] = {"state": state.value, "fetch_status": status.value}
    if state in UNIT_STATES_WITH_FILE:
        columns |= FILE_COLUMNS
    if status is not FetchStatus.UNKNOWN:
        columns["failure_reason"] = "set up by the test"
    if reopened:
        columns["reopened_at"] = NOW
    return insert_unit(conn, insert_request(conn), **columns)


async def _to_downloaded(conn: AConn, unit_id: int) -> None:
    await mark_downloaded(conn, unit_id, FILE, NOW)


async def _to_verified(conn: AConn, unit_id: int) -> None:
    await mark_verified(conn, unit_id, 511_965, NOW)


async def _to_hole(conn: AConn, unit_id: int) -> None:
    await mark_provider_hole(conn, unit_id, NOW)


async def _reset(conn: AConn, unit_id: int) -> None:
    await reset_exhausted(conn, unit_id)


async def _reopen(conn: AConn, unit_id: int) -> None:
    await reopen_hole(conn, unit_id, NOW)


_S = UnitState
_F = FetchStatus
#: transition, the one (state, status) it moves from, wrong starting points.
CASES: list[tuple[Transition, tuple[UnitState, FetchStatus], list[Any]]] = [
    (
        _to_downloaded,
        (_S.DELIVERED, _F.UNKNOWN),
        [(_S.REQUESTED, _F.UNKNOWN), (_S.DELIVERED, _F.PROVIDER_HOLE),
         (_S.DELIVERED, _F.RETRY_EXHAUSTED), (_S.DOWNLOADED, _F.UNKNOWN),
         (_S.DELIVERED, _F.UNKNOWN, "reopened")],
    ),
    (
        _to_verified,
        (_S.DOWNLOADED, _F.FAILED_RETRYABLE),
        [(_S.DELIVERED, _F.UNKNOWN), (_S.VERIFIED, _F.UNKNOWN),
         (_S.DOWNLOADED, _F.RETRY_EXHAUSTED)],
    ),
    (
        _to_hole,
        (_S.DELIVERED, _F.UNKNOWN),
        [(_S.SUBMITTED, _F.UNKNOWN), (_S.DOWNLOADED, _F.UNKNOWN),
         (_S.DELIVERED, _F.PROVIDER_HOLE)],
    ),
    (
        _reset,
        (_S.DELIVERED, _F.RETRY_EXHAUSTED),
        [(_S.DELIVERED, _F.FAILED_RETRYABLE), (_S.DELIVERED, _F.PROVIDER_HOLE),
         (_S.DELIVERED, _F.RETRY_EXHAUSTED, "reopened")],
    ),
    (
        _reopen,
        (_S.DELIVERED, _F.PROVIDER_HOLE),
        [(_S.DELIVERED, _F.RETRY_EXHAUSTED), (_S.DELIVERED, _F.UNKNOWN),
         (_S.DELIVERED, _F.PROVIDER_HOLE, "reopened")],
    ),
]  # fmt: skip


@pytest.mark.parametrize(("transition", "start", "_"), CASES)
async def test_transition_succeeds_from_its_state(
    tick_conn: Conn, aconn: AConn, transition: Transition, start: Any, _: Any
) -> None:
    unit_id = _unit(tick_conn, *start)
    await transition(aconn, unit_id)


@pytest.mark.parametrize(
    ("transition", "wrong"),
    [(case[0], wrong) for case in CASES for wrong in case[2]],
)
async def test_transition_refuses_any_other_state(
    tick_conn: Conn, aconn: AConn, transition: Transition, wrong: tuple[Any, ...]
) -> None:
    state, status, *flags = wrong
    unit_id = _unit(tick_conn, state, status, reopened="reopened" in flags)
    before = await units_by_id(aconn, [unit_id])
    with pytest.raises(ManifestTransitionError, match=f"unit {unit_id} is not"):
        await transition(aconn, unit_id)
    assert await units_by_id(aconn, [unit_id]) == before


async def test_advancing_clears_the_failure(tick_conn: Conn, aconn: AConn) -> None:
    unit_id = _unit(tick_conn, UnitState.DOWNLOADED, FetchStatus.FAILED_RETRYABLE)
    await mark_verified(aconn, unit_id, 511_965, NOW)
    [unit] = await units_by_id(aconn, [unit_id])
    assert unit.state is UnitState.VERIFIED
    assert unit.fetch_status is FetchStatus.UNKNOWN
    assert unit.failure_reason is None
    assert unit.provider_record_count == 511_965


async def test_transient_failures_exhaust_at_the_limit(
    tick_conn: Conn, aconn: AConn
) -> None:
    unit_id = _unit(tick_conn, UnitState.DELIVERED)
    for _ in range(MAX_RETRY_COUNT):
        await record_failure(
            aconn, unit_id, UnitState.DELIVERED, "timeout", NOW, deterministic=False
        )
    [unit] = await units_by_id(aconn, [unit_id])
    assert unit.fetch_status is FetchStatus.RETRY_EXHAUSTED
    assert unit.attempt_count == MAX_RETRY_COUNT
    with pytest.raises(ManifestTransitionError):
        await record_failure(
            aconn, unit_id, UnitState.DELIVERED, "timeout", NOW, deterministic=False
        )


async def test_one_transient_failure_is_retryable(
    tick_conn: Conn, aconn: AConn
) -> None:
    unit_id = _unit(tick_conn, UnitState.DELIVERED)
    await record_failure(
        aconn, unit_id, UnitState.DELIVERED, "timeout", NOW, deterministic=False
    )
    [unit] = await units_by_id(aconn, [unit_id])
    assert (unit.fetch_status, unit.attempt_count) == (FetchStatus.FAILED_RETRYABLE, 1)
    assert unit.failure_reason == "timeout"


async def test_deterministic_failure_exhausts_at_once(
    tick_conn: Conn, aconn: AConn
) -> None:
    unit_id = _unit(tick_conn, UnitState.DOWNLOADED)
    await record_failure(
        aconn, unit_id, UnitState.DOWNLOADED, "header day", NOW, deterministic=True
    )
    [unit] = await units_by_id(aconn, [unit_id])
    assert (unit.fetch_status, unit.attempt_count) == (FetchStatus.RETRY_EXHAUSTED, 1)


async def test_hole_and_reset_write_their_columns(
    tick_conn: Conn, aconn: AConn
) -> None:
    hole = _unit(tick_conn, UnitState.DELIVERED)
    exhausted = _unit(tick_conn, UnitState.DELIVERED, FetchStatus.RETRY_EXHAUSTED)
    await mark_provider_hole(aconn, hole, NOW)
    await reopen_hole(aconn, hole, NOW)
    await reset_exhausted(aconn, exhausted)
    by_id = {u.unit_id: u for u in await units_by_id(aconn, [hole, exhausted])}
    assert by_id[hole].failure_reason == PROVIDER_HOLE_REASON
    assert by_id[hole].reopened_at == NOW
    assert by_id[exhausted].fetch_status is FetchStatus.UNKNOWN
    assert by_id[exhausted].attempt_count == 0
    assert by_id[exhausted].failure_reason is None


async def test_adopted_request_and_units_round_trip(aconn: AConn) -> None:
    request = AdoptedRequest(
        job_id="GLBX-TEST-JOB",
        dataset=CME_DATASET,
        schema=TickSchema.TRADES,
        symbols=("ES.FUT",),
        stype_in=SType.PARENT,
        range_start=date(2024, 9, 1),
        range_end=date(2024, 9, 4),
        cost_usd=Decimal("12.5785"),
        record_count=10,
        billed_size=480,
        ts_received=NOW,
    )
    units = [
        NewUnit(date(2024, 9, 1), UnitState.DOWNLOADED, file=FILE),
        NewUnit(
            date(2024, 9, 3),
            UnitState.DELIVERED,
            FetchStatus.PROVIDER_HOLE,
            PROVIDER_HOLE_REASON,
        ),
    ]
    ids = await insert_adopted_request(aconn, request, units, NOW)
    request_id = await request_id_for_job(aconn, "GLBX-TEST-JOB")
    assert request_id is not None
    rows = await units_of_request(aconn, request_id)
    assert [(u.unit_id, u.state, u.file) for u in rows] == [
        (ids[date(2024, 9, 1)], UnitState.DOWNLOADED, FILE),
        (ids[date(2024, 9, 3)], UnitState.DELIVERED, None),
    ]
    assert rows[0].symbols == ("ES.FUT",)
    assert await request_id_for_job(aconn, "GLBX-NO-SUCH-JOB") is None
