"""The tick-minute SQL of ``mt data tick parity`` on synthetic ticks (slice 306).

Ported from the read-time bar tests of slice 230: ties broken by the full key,
volume as an integer, empty minutes omitted, other instruments excluded, an empty
window. Buckets are the session-anchored ones the provider reader uses.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import psycopg
import pytest
from tick_support.reads import bar_dicts, read_rows
from tick_support.rows import insert_request, insert_unit
from tick_support.ticks import Tick, seed_ticks

from manta_trading.data.tick.bar_parity import read_tick_minute_bars
from manta_trading.data.tick.tick_reads import TickWindow

ES, OTHER = 118, 183748


def _ns(hour: int, minute: int = 0, day: int = 16) -> int:
    return int(datetime(2024, 9, day, hour, minute, tzinfo=UTC).timestamp()) * 10**9


A = TickWindow(date(2024, 9, 16), ES, "ESU4", _ns(22, day=15), _ns(21))
#: Three trades share session A's close instant: told apart by (sequence,
#: sequence_ordinal) and inserted out of key order.
TIES = [
    Tick(_ns(21), price=5_003, size=3, sequence=7, sequence_ordinal=1),
    Tick(_ns(21), price=5_001, size=1, sequence=6, sequence_ordinal=0),
    Tick(_ns(21), price=5_002, size=2, sequence=7, sequence_ordinal=0),
]
A_TICKS = [
    Tick(_ns(22, 0, day=15), price=5_000, size=4, sequence=1),  # at the open
    Tick(_ns(9), price=5_010, size=5, sequence=2),
    *TIES,
    Tick(_ns(21, 30), price=9_999, size=9, sequence=11),  # the break: in no window
]


@pytest.fixture
def seeded(migrated_tick_db: str) -> str:
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn))
    seed_ticks(migrated_tick_db, unit, ES, A_TICKS)
    seed_ticks(migrated_tick_db, unit, OTHER, [Tick(_ns(9), price=1, sequence=1)])
    return migrated_tick_db


async def _minutes(url: str, window: TickWindow) -> list[dict[str, Any]]:
    return bar_dicts(await read_rows(url, read_tick_minute_bars, window))


async def test_volume_sums_sizes_and_empty_minutes_are_omitted(seeded: str) -> None:
    rows = await _minutes(seeded, A)

    assert [r["time"] for r in rows] == [A.open_ns, _ns(9), _ns(21)]
    assert rows[0]["volume"] == 4 and rows[1]["volume"] == 5
    assert rows[-1]["volume"] == 6
    assert type(rows[-1]["volume"]) is int  # not numeric: JSON must serialize it


async def test_ties_at_the_close_open_and_close_by_the_full_key(seeded: str) -> None:
    close_minute = (await _minutes(seeded, A))[-1]

    assert close_minute["time"] == A.close_ns  # the close-stamped minute
    assert (close_minute["open"], close_minute["close"]) == (5_001, 5_003)
    assert (close_minute["high"], close_minute["low"]) == (5_003, 5_001)


async def test_only_the_windows_instrument_and_session_count(seeded: str) -> None:
    rows = await _minutes(seeded, A)

    assert {r["instrument_id"] for r in rows} == {ES}  # OTHER's 09:00 excluded
    assert 9_999 not in {r["close"] for r in rows}  # the break's tick is outside
    assert rows[0]["session_date"] == A.session_date and rows[0]["raw_symbol"] == "ESU4"


async def test_an_empty_window_reads_nothing(seeded: str) -> None:
    empty = TickWindow(date(2024, 9, 18), ES, "ESU4", _ns(22, day=17), _ns(21, day=18))

    assert await _minutes(seeded, empty) == []
