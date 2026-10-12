"""``read_ticks`` and ``read_bars`` on a scratch tick database (slice 230, TD3).

Two synthetic sessions around a daily break: A closes 21:00 UTC, B opens
22:00 UTC. A 4h bucket (20:00 → 24:00 UTC) straddles the break. Ticks are
seeded directly (``tick_support.ticks``); no calendar is read.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from tick_support.rows import insert_request, insert_unit
from tick_support.ticks import Tick, seed_ticks

from manta_trading.constants import Granularity
from manta_trading.data.tick import tick_reads
from manta_trading.data.tick.tick_reads import (
    BAR_COLUMNS,
    TICK_COLUMNS,
    TickWindow,
    read_bars,
    read_ticks,
)

ES, OTHER = 118, 183748
HOUR = 3_600 * 1_000_000_000


def _ns(hour: int, minute: int = 0, day: int = 16) -> int:
    return int(datetime(2024, 9, day, hour, minute, tzinfo=UTC).timestamp()) * 10**9


A = TickWindow(date(2024, 9, 16), ES, "ESU4", _ns(22, day=15), _ns(21))
B = TickWindow(date(2024, 9, 17), ES, "ESU4", _ns(22), _ns(21, day=17))

#: Session A: three trades share 20:30:00 exactly, told apart by
#: (sequence, sequence_ordinal) and inserted out of key order.
TIES = [
    Tick(_ns(20, 30), price=5_003, size=3, sequence=7, sequence_ordinal=1),
    Tick(_ns(20, 30), price=5_001, size=1, sequence=6, sequence_ordinal=0),
    Tick(_ns(20, 30), price=5_002, size=2, sequence=7, sequence_ordinal=0),
]
A_TICKS = [
    Tick(_ns(22, 0, day=15), price=5_000, size=4, sequence=1),  # at open: in
    Tick(_ns(9), price=5_010, size=5, sequence=2),
    *TIES,
    Tick(_ns(21), price=9_999, size=9, sequence=8),  # at close: out (B's gap)
]
B_TICKS = [
    Tick(_ns(22, 30), price=5_020, size=6, sequence=9, bbo=(5_019, 5_021, 1, 2, 1, 1)),
    Tick(_ns(23), price=5_015, size=7, sequence=10),
]


@pytest.fixture
def seeded(migrated_tick_db: str) -> str:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn))
    seed_ticks(migrated_tick_db, unit, ES, [*A_TICKS, *B_TICKS])
    seed_ticks(migrated_tick_db, unit, OTHER, [Tick(_ns(9), price=1, sequence=1)])
    return migrated_tick_db


async def _read(url: str, read: Any, *args: Any) -> list[list[Sequence[Any]]]:
    batches: list[list[Sequence[Any]]] = []
    async with await psycopg.AsyncConnection.connect(url) as conn:
        async with conn.transaction():
            await read(conn, *args, lambda batch: batches.append(list(batch)))
    return batches


def _rows(batches: list[list[Sequence[Any]]], columns: Sequence[str]) -> list[dict]:
    return [dict(zip(columns, row, strict=True)) for b in batches for row in b]


async def test_ticks_in_key_order_inside_the_window(seeded: str) -> None:
    rows = _rows(await _read(seeded, read_ticks, A), TICK_COLUMNS)

    assert [r["price"] for r in rows] == [5_000, 5_010, 5_001, 5_002, 5_003]
    assert {r["instrument_id"] for r in rows} == {ES}  # OTHER's 09:00 excluded
    assert rows[0]["session_date"] == A.session_date
    assert rows[0]["raw_symbol"] == "ESU4"
    assert rows[0]["bid_px"] is None and rows[0]["ask_ct"] is None


async def test_tbbo_row_carries_its_quote(seeded: str) -> None:
    rows = _rows(await _read(seeded, read_ticks, B), TICK_COLUMNS)

    assert [r["bid_px"] for r in rows] == [5_019, None]
    assert rows[0]["ask_sz"] == 2


async def test_4h_bucket_across_the_break_is_two_bars(seeded: str) -> None:
    a = _rows(await _read(seeded, read_bars, A, Granularity.H4), BAR_COLUMNS)
    b = _rows(await _read(seeded, read_bars, B, Granularity.H4), BAR_COLUMNS)

    assert a[-1]["time"] == _ns(20)  # the ties' bucket, A's side of the break
    assert b[0]["time"] == B.open_ns  # 22:00, not 20:00
    assert b[0]["volume"] == 13
    # A's first bar starts at A's open, not at the 20:00 bucket before it.
    assert a[0]["time"] == A.open_ns


async def test_session_bar_matches_the_ticks_with_ties_by_key(seeded: str) -> None:
    (bar,) = _rows(await _read(seeded, read_bars, A, Granularity.D1), BAR_COLUMNS)

    assert bar["time"] == A.open_ns
    assert (bar["open"], bar["high"], bar["low"], bar["close"]) == (
        5_000,
        5_010,
        5_000,
        5_003,  # the last tie in key order, not the first inserted
    )
    assert bar["volume"] == 4 + 5 + 1 + 2 + 3


async def test_1m_volume_sums_sizes_and_skips_empty_buckets(seeded: str) -> None:
    rows = _rows(await _read(seeded, read_bars, A, Granularity.M1), BAR_COLUMNS)

    assert [r["time"] for r in rows] == [A.open_ns, _ns(9), _ns(20, 30)]
    assert rows[-1]["volume"] == 6
    assert (rows[-1]["open"], rows[-1]["close"]) == (5_001, 5_003)


async def test_batches_stream_before_the_read_ends(
    seeded: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", 2)
    seen: list[int] = []

    async with await psycopg.AsyncConnection.connect(seeded) as conn:
        async with conn.transaction():
            total = await read_ticks(conn, A, lambda batch: seen.append(len(batch)))

    assert seen == [2, 2, 1]
    assert total == 5


async def test_empty_window_reads_nothing(seeded: str) -> None:
    empty = TickWindow(date(2024, 9, 18), ES, "ESU4", _ns(22, day=17), _ns(21, day=18))

    assert await _read(seeded, read_ticks, empty) == []
    assert await _read(seeded, read_bars, empty, Granularity.H1) == []
