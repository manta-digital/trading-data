"""Time bars cut from the real held ``tbbo`` day (slice 309, design criterion 2).

The oracle is ``parity_seed.tick_minutes``: the window's trades bucketed in
Python, independently of the SQL, at the session open + n widths. That is the
design's "summed by hand".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.parity_seed import Minute, ParityDb, seed_parity, tick_minutes
from tick_support.reads import read_rows
from tick_support.rows import insert_request, insert_unit
from tick_support.ticks import Tick, seed_ticks

from manta_trading.data.tick.tick_reads import Bounds, TickWindow
from manta_trading.data.tick.time_bar_cut import TIME_BAR_CUT_COLUMNS, cut_time_bars
from manta_trading.data.tick.time_bar_size import (
    NS_PER_SECOND,
    BarSize,
    parse_bar_size,
)

_OPEN = TIME_BAR_CUT_COLUMNS.index("open")
_COUNT = TIME_BAR_CUT_COLUMNS.index("trade_count")


@pytest.fixture
async def db(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)


async def _cut(
    db: ParityDb, token: str, bounds: Bounds = (None, None)
) -> list[tuple[Any, ...]]:
    size = parse_bar_size(token)

    async def read(conn: Any, window: TickWindow, on_batch: Any) -> int:
        return await cut_time_bars(conn, window, size, on_batch, bounds)

    return [tuple(r) for r in await read_rows(db.url, read, db.window)]


def _width(db: ParityDb, size: BarSize) -> int:
    # The oracle's width; a width past the close gives the one session bucket.
    return size.width_ns or db.window.close_ns - db.window.open_ns + 1


def _by_bucket(rows: list[tuple[Any, ...]]) -> dict[int, Minute]:
    return {int(r[0]): tuple(int(v) for v in r[_OPEN:_COUNT]) for r in rows}  # type: ignore[misc]


def _trade_count(url: str, window: TickWindow) -> int:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT count(*) FROM tick_trade"
            " WHERE instrument_id = %s AND ts_event BETWEEN %s AND %s",
            (window.instrument_id, window.open_ns, window.close_ns),
        ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("token", ["1m", "4h", "1d", "90m"])
async def test_bars_equal_the_trades_bucketed_by_hand(db: ParityDb, token: str) -> None:
    """``1m`` is criterion 2; ``4h`` starts buckets at the 17:00 Chicago open,
    not on a UTC multiple; ``1d`` is the day bucket; ``90m`` does not divide
    the session evenly."""
    size = parse_bar_size(token)
    rows = await _cut(db, token)
    expected = tick_minutes(db.url, db.window, _width(db, size))
    assert expected
    assert _by_bucket(rows) == expected
    assert sum(r[_COUNT] for r in rows) == _trade_count(db.url, db.window)
    if size.session_day:
        assert [r[0] for r in rows] == [db.window.open_ns]


async def test_one_second_bars_inside_a_window(db: ParityDb) -> None:
    """Issue #40: one bar per second that has a trade, none for an empty one,
    selected by bar start in ``[from, until)``."""
    first_trade = min(db.tick_minutes)
    from_ns = first_trade + 60 * NS_PER_SECOND
    until_ns = from_ns + 300 * NS_PER_SECOND
    rows = await _cut(db, "1s", (from_ns, until_ns))
    seconds = tick_minutes(db.url, db.window, NS_PER_SECOND)
    expected = {b: m for b, m in seconds.items() if from_ns <= b < until_ns}
    assert expected
    assert _by_bucket(rows) == expected


async def test_a_bar_never_crosses_the_session(db: ParityDb) -> None:
    """Trades just outside the window change no bar; every ``90m`` bucket
    starts inside the session."""
    window = db.window
    with psycopg.connect(db.url, autocommit=True) as conn:
        unit = insert_unit(conn, insert_request(conn))
    outside = [
        Tick(window.open_ns - 1, price=1, size=7, sequence=1),
        Tick(window.close_ns + 1, price=1, size=7, sequence=2),
    ]
    seed_ticks(db.url, unit, window.instrument_id, outside)

    rows = await _cut(db, "90m")

    assert all(window.open_ns <= r[0] < window.close_ns for r in rows)
    expected = tick_minutes(db.url, window, 90 * 60 * NS_PER_SECOND)
    assert _by_bucket(rows) == expected
    assert sum(r[_COUNT] for r in rows) == _trade_count(db.url, window)
