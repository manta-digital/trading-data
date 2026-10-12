"""``debug_reads`` filters on a scratch tick database (slice 230, TD6, FR11).

Two requests of different shapes and ranges, units across states and fetch
statuses, and definitions with a spread and an expired outright, seeded with
``tick_support.rows``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import psycopg
import pytest
from tick_support.rows import (
    ACTIVATION_NS,
    FILE_COLUMNS,
    WINDOW_NS,
    insert_definition,
    insert_request,
    insert_unit,
)

from manta_trading.data.quality.fetch_status import FetchStatus
from manta_trading.data.tick import debug_reads
from manta_trading.data.tick.constants import (
    OUTRIGHT_CLASS,
    SPREAD_CLASS,
    TickSchema,
    UnitState,
)

ES = ["ES.FUT"]
GC = ["GC.FUT"]


@dataclass(frozen=True)
class Seeded:
    es_request: int
    gc_request: int
    units: dict[str, int]


@pytest.fixture
def seeded(migrated_tick_db: str) -> Seeded:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        es = insert_request(
            conn, range_start=date(2024, 9, 3), range_end=date(2024, 9, 6)
        )
        gc = insert_request(
            conn,
            symbols=GC,
            schema=TickSchema.TBBO.value,
            range_start=date(2024, 11, 1),
            range_end=date(2024, 11, 2),
        )
        units = {
            "es_0903": insert_unit(
                conn, es, unit_date=date(2024, 9, 3),
                state=UnitState.INGESTED.value, **FILE_COLUMNS,
            ),
            "es_0904": insert_unit(
                conn, es, unit_date=date(2024, 9, 4),
                fetch_status=FetchStatus.FAILED_RETRYABLE.value,
                failure_reason="submit refused", attempt_count=1,
            ),
            "es_0905": insert_unit(conn, es, unit_date=date(2024, 9, 5)),
            "gc_1101": insert_unit(conn, gc, unit_date=date(2024, 11, 1)),
        }  # fmt: skip
        definition_unit = units["es_0903"]
        insert_definition(conn, definition_unit, instrument_id=1, raw_symbol="ESZ4")
        insert_definition(
            conn,
            definition_unit,
            instrument_id=2,
            raw_symbol="ESU4-ESZ4",
            instrument_class=SPREAD_CLASS,
        )
        insert_definition(
            conn,
            definition_unit,
            instrument_id=3,
            raw_symbol="ESU4",
            activation_ns=ACTIVATION_NS - 2 * WINDOW_NS,
            expiration_ns=ACTIVATION_NS - WINDOW_NS,
        )
        insert_definition(
            conn, definition_unit, instrument_id=4, raw_symbol="GCZ4", asset="GC"
        )
        conn.execute(
            "UPDATE tick_definition SET instrument_class = %s"
            " WHERE instrument_class IS NULL",
            (OUTRIGHT_CLASS,),
        )
    return Seeded(es, gc, units)


async def _call(url: str, read: Any, *args: Any) -> list[dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(url) as conn:
        rows: list[dict[str, Any]] = await read(conn, *args)
        return rows


async def test_requests_filter_by_shape_and_range_overlap(
    seeded: Seeded, migrated_tick_db: str
) -> None:
    url = migrated_tick_db

    def ids(rows: list[dict[str, Any]]) -> list[int]:
        return [r["request_id"] for r in rows]

    everything = await _call(url, debug_reads.requests, None, None, None)
    assert ids(everything) == [seeded.es_request, seeded.gc_request]
    assert ids(await _call(url, debug_reads.requests, ES, None, None)) == [
        seeded.es_request
    ]
    # [09-05, 09-07) meets ES's [09-03, 09-06); [09-06, 11-01) meets neither.
    span = (date(2024, 9, 5), date(2024, 9, 7))
    assert ids(await _call(url, debug_reads.requests, None, *span)) == [
        seeded.es_request
    ]
    gap = (date(2024, 9, 6), date(2024, 11, 1))
    assert await _call(url, debug_reads.requests, None, *gap) == []
    assert set(everything[0]) >= {
        "provider_job_id",
        "estimated_cost_usd",
        "download_deadline",
    }


async def test_units_filter_by_window_state_and_fetch_status(
    seeded: Seeded, migrated_tick_db: str
) -> None:
    url, u = migrated_tick_db, seeded.units

    async def ids(*args: Any) -> list[int]:
        return [r["unit_id"] for r in await _call(url, debug_reads.units, *args)]

    assert await ids(None, (None, None), None, None) == list(u.values())
    assert await ids(ES, (None, None), None, None) == [
        u["es_0903"],
        u["es_0904"],
        u["es_0905"],
    ]
    # The window's end is exclusive.
    assert await ids(None, (date(2024, 9, 4), date(2024, 9, 5)), None, None) == [
        u["es_0904"]
    ]
    assert await ids(None, (None, None), UnitState.INGESTED, None) == [u["es_0903"]]
    assert await ids(None, (None, None), None, FetchStatus.FAILED_RETRYABLE) == [
        u["es_0904"]
    ]
    (row,) = await _call(url, debug_reads.units, GC, (None, None), None, None)
    assert row["schema"] == TickSchema.TBBO.value
    assert row["state"] is UnitState.REQUESTED
    assert row["fetch_status"] is FetchStatus.UNKNOWN


async def test_definitions_hide_spreads_unless_asked(
    seeded: Seeded, migrated_tick_db: str
) -> None:
    url = migrated_tick_db

    async def ids(*args: Any) -> list[int]:
        return [
            r["instrument_id"] for r in await _call(url, debug_reads.definitions, *args)
        ]

    assert await ids("ES", None, None, False) == [3, 1]  # by expiration
    assert await ids("ES", None, None, True) == [3, 1, 2]
    assert await ids(None, None, None, False) == [3, 1, 4]
    assert await ids("ES", "ESZ4", None, False) == [1]
    # As of an instant inside the current window, the expired ESU4 drops out.
    assert await ids("ES", None, ACTIVATION_NS + 1, False) == [1]
    assert await ids("ZZ", None, None, True) == []
