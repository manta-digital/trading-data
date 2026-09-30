"""Availability capture on a migrated tick database (slice 224, FR6 reopen).

The fake provider answers ``dataset_range`` and ``dataset_condition``; the
capture writes ``tick_dataset_edge`` and ``tick_day_condition`` and reopens a
changed day's holed units in the same transaction (TD8).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import FakeClock, FakeTickProvider
from tick_support.rows import FILE_COLUMNS, insert_request, insert_unit
from tick_support.runs import connect, tick_run

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick.availability import capture_dataset
from manta_trading.data.tick.constants import (
    CME_DATASET,
    DatasetCondition,
    SType,
    TickSchema,
    UnitState,
)
from manta_trading.data.tick.manifest_reads import holed_days, owned_tier_days
from manta_trading.data.tick.planner import DayKey
from manta_trading.data.tick.universe import TICK_UNIVERSE

START = datetime(2026, 9, 29, 12, tzinfo=UTC)
DAY = date(2024, 9, 3)
HOLE_DAY = date(2024, 9, 4)
CondRows = list[tuple[date, str, date | None]]


@pytest.fixture
async def aconn(migrated_tick_db: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    async with connect(migrated_tick_db) as conn:
        yield conn


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(START)


@pytest.fixture
def provider(clock: FakeClock) -> FakeTickProvider:
    return FakeTickProvider(clock=clock)


@pytest.fixture
def seeded(migrated_tick_db: str) -> dict[str, int]:
    """One verified trades unit on DAY and one holed unit on HOLE_DAY."""
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        request = insert_request(conn, range_end=date(2024, 9, 5))
        owned = insert_unit(
            conn,
            request,
            state=UnitState.VERIFIED.value,
            unit_date=DAY,
            **FILE_COLUMNS,
        )
        hole = insert_unit(
            conn,
            request,
            state=UnitState.DELIVERED.value,
            unit_date=HOLE_DAY,
            fetch_status=FetchStatus.PROVIDER_HOLE.value,
            failure_reason="job delivered no file for a session day",
        )
    return {"owned": owned, "hole": hole}


async def _capture(
    conn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
    window: tuple[date | None, date | None] = (None, None),
) -> Any:
    run = tick_run(conn, provider, tmp_path, clock)
    return await capture_dataset(
        run,
        CME_DATASET,
        TICK_UNIVERSE,
        await owned_tier_days(conn),
        await holed_days(conn),
        window,
    )


async def _condition_rows(conn: psycopg.AsyncConnection[Any]) -> CondRows:
    cursor = await conn.execute(
        "SELECT condition_date, condition, last_modified_date"
        " FROM tick_day_condition ORDER BY condition_date"
    )
    return [(r[0], r[1], r[2]) for r in await cursor.fetchall()]


async def _reopened(conn: psycopg.AsyncConnection[Any], unit_id: int) -> bool:
    cursor = await conn.execute(
        "SELECT reopened_at IS NOT NULL FROM tick_archive_unit WHERE unit_id = %s",
        (unit_id,),
    )
    row = await cursor.fetchone()
    assert row is not None
    return bool(row[0])


async def test_owned_and_holed_reads(
    aconn: psycopg.AsyncConnection[Any], seeded: dict[str, int]
) -> None:
    assert await owned_tier_days(aconn) == [
        DayKey(CME_DATASET, TickSchema.TRADES, ("ES.FUT",), SType.PARENT, DAY)
    ]
    assert await holed_days(aconn) == [(CME_DATASET, HOLE_DAY)]


async def test_first_run_inserts_edge_and_condition_rows(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    seeded: dict[str, int],
    tmp_path: Path,
) -> None:
    result = await _capture(aconn, provider, clock, tmp_path)
    assert (result.span.start, result.span.end) == (DAY, HOLE_DAY + timedelta(days=1))
    assert result.written == 2
    assert result.conditions == {DatasetCondition.AVAILABLE: 2}
    assert [r[0] for r in await _condition_rows(aconn)] == [DAY, HOLE_DAY]
    cursor = await aconn.execute(
        "SELECT dataset, available_end, observed_at FROM tick_dataset_edge"
    )
    assert await cursor.fetchall() == [(CME_DATASET, provider.edge.end, START)]
    assert len(provider.calls_to("dataset_range")) == 1
    assert len(provider.calls_to("dataset_condition")) == 1


async def test_a_second_identical_run_changes_no_condition_row_or_unit(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    seeded: dict[str, int],
    tmp_path: Path,
) -> None:
    await _capture(aconn, provider, clock, tmp_path)
    before = await _condition_rows(aconn)
    observed = await aconn.execute("SELECT observed_at FROM tick_day_condition")
    first_observed = await observed.fetchall()
    clock.advance(timedelta(hours=1))
    second = await _capture(aconn, provider, clock, tmp_path)
    assert second.written == 0 and second.reopened == 0
    assert await _condition_rows(aconn) == before
    again = await aconn.execute("SELECT observed_at FROM tick_day_condition")
    assert await again.fetchall() == first_observed
    assert not await _reopened(aconn, seeded["hole"])


async def test_a_changed_condition_reopens_that_days_hole_only(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    seeded: dict[str, int],
    tmp_path: Path,
) -> None:
    await _capture(aconn, provider, clock, tmp_path)
    provider.condition_of = lambda day: (
        DatasetCondition.DEGRADED if day == HOLE_DAY else DatasetCondition.AVAILABLE
    )
    result = await _capture(aconn, provider, clock, tmp_path)
    assert (result.written, result.reopened) == (1, 1)
    assert await _reopened(aconn, seeded["hole"])
    assert not await _reopened(aconn, seeded["owned"])
    rows = dict((r[0], r[1]) for r in await _condition_rows(aconn))
    assert rows[HOLE_DAY] == DatasetCondition.DEGRADED.value
    assert rows[DAY] == DatasetCondition.AVAILABLE.value


async def test_a_change_on_a_day_without_a_hole_reopens_nothing(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    seeded: dict[str, int],
    tmp_path: Path,
) -> None:
    await _capture(aconn, provider, clock, tmp_path)
    provider.condition_of = lambda day: (
        DatasetCondition.DEGRADED if day == DAY else DatasetCondition.AVAILABLE
    )
    result = await _capture(aconn, provider, clock, tmp_path)
    assert (result.written, result.reopened) == (1, 0)
    assert not await _reopened(aconn, seeded["hole"])


async def test_the_window_narrows_the_span_and_an_empty_span_asks_nothing(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    seeded: dict[str, int],
    tmp_path: Path,
) -> None:
    narrowed = await _capture(
        aconn, provider, clock, tmp_path, (HOLE_DAY, date(2030, 1, 1))
    )
    assert narrowed.span is not None and narrowed.span.start == HOLE_DAY
    provider.calls.clear()
    outside = await _capture(
        aconn, provider, clock, tmp_path, (date(2025, 1, 1), date(2025, 2, 1))
    )
    assert outside.span is None
    assert provider.calls_to("dataset_condition") == []
    assert len(provider.calls_to("dataset_range")) == 1


async def test_a_bare_manifest_still_records_the_edge(
    aconn: psycopg.AsyncConnection[Any],
    provider: FakeTickProvider,
    clock: FakeClock,
    tmp_path: Path,
) -> None:
    result = await _capture(aconn, provider, clock, tmp_path)
    assert result.span is None and result.written == 0
    cursor = await aconn.execute("SELECT count(*) FROM tick_dataset_edge")
    assert await cursor.fetchone() == (1,)
