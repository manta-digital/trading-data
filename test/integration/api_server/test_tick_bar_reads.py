"""``read_tick_bars`` and the trade-scan admission on the tick test cluster (308 D3,
D5, D10). Seeded by ``tick_bar_seed``: two ESZ4 sessions, ledger counts matching."""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from functools import partial
from typing import Any

import psycopg
import pytest
from psycopg_pool import AsyncConnectionPool
from tick_support.roll_seed import ESZ4, SeededRoll
from tick_support.tick_bar_seed import (
    FIRST,
    FIRST_COUNT,
    SECOND,
    SECOND_COUNT,
    seed_tick_bar_sessions,
    session_start_ns,
)
from tick_support.ticks import Tick, seed_ticks

from manta_trading.api_server.futures_read import Admit, ReadWindow, read_series
from manta_trading.data.tick import tick_reads
from manta_trading.data.tick.dbn_values import UNDEF_INT64
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.manifest_reads import Conn
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.tick_bar_reads import (
    ReadAbandoned,
    TickBarWindowError,
    TradeScanExceeded,
    read_tick_bars,
    trade_admission,
    trades_to_scan,
)
from manta_trading.data.tick.tick_bars import TickBarCutter, TickBarInputError, Trade
from manta_trading.data.tick.tick_reads import (
    Bounds,
    OnBatch,
    Row,
    TickWindow,
    read_ticks,
)

POOL_SIZE = 2
OPEN: Bounds = (None, None)
SEC = 1_000_000_000


async def _never() -> bool:
    return False


async def _reset(conn: Conn) -> None:
    await conn.set_isolation_level(None)
    await conn.set_read_only(None)


@pytest.fixture
def seeded(migrated_tick_db: str) -> SeededRoll:
    return seed_tick_bar_sessions(migrated_tick_db)


@pytest.fixture
async def pool(
    seeded: SeededRoll, migrated_tick_db: str
) -> AsyncIterator[AsyncConnectionPool[Conn]]:
    tick_pool: AsyncConnectionPool[Conn] = AsyncConnectionPool(
        migrated_tick_db,
        open=False,
        min_size=POOL_SIZE,
        max_size=POOL_SIZE,
        reset=_reset,
    )
    await tick_pool.open(wait=True)
    yield tick_pool
    await tick_pool.close()


def _plan_for(calendar_url: str) -> Any:
    async def plan_for(conn: Conn) -> GetPlan:
        return await contract_plan(
            conn,
            calendar_url,
            "ESZ4",
            FIRST,
            SECOND + timedelta(days=1),
            SchemaFamily.TICK_TIER,
        )

    return plan_for


async def _series(
    pool: AsyncConnectionPool[Conn],
    calendar_url: str,
    read: ReadWindow,
    admit: Admit | None = None,
) -> tuple[GetPlan, list[Row]]:
    async with pool.connection() as conn:
        return await read_series(
            conn, _plan_for(calendar_url), read, 10**6, OPEN, admit
        )


def _bars_reader(n: int, bounds: Bounds = OPEN, stop: Any = _never) -> ReadWindow:
    return partial(read_tick_bars, ticks_per_bar=n, bounds=bounds, stop=stop)


async def _tick_bars(
    pool: AsyncConnectionPool[Conn], calendar_url: str, n: int, bounds: Bounds = OPEN
) -> list[Row]:
    return (await _series(pool, calendar_url, _bars_reader(n, bounds)))[1]


def _trades(tick_rows: Sequence[Row]) -> list[Trade]:
    """``read_ticks`` rows as trades; ordinals counted as ingest assigns them."""
    seen: Counter[tuple[int, int]] = Counter()
    out = []
    for row in tick_rows:
        ts, session, price, size, side, bid, ask, seq = (
            row[0], row[1], row[4], row[5], row[6], row[7], row[8], row[13]
        )  # fmt: skip
        out.append(Trade(ts, seq, seen[ts, seq], session, price, size, side, bid, ask))
        seen[ts, seq] += 1
    return out


def _expected(tick_rows: Sequence[Row], n: int) -> list[Row]:
    """The cutter over the same stored trades, one cutter per session."""
    rows: list[Row] = []
    for session in (FIRST, SECOND):
        cutter = TickBarCutter(n)
        bars = [
            bar
            for t in _trades(tick_rows)
            if t.session_date == session
            for bar in cutter.push(t)
        ]
        last = cutter.close_session()
        for b in [*bars, *([last] if last else [])]:
            rows.append(
                (
                    b.session_date,
                    b.index,
                    b.open_time,
                    b.close_time,
                    "ESZ4",
                    ESZ4.instrument_id,
                    b.open,
                    b.high,
                    b.low,
                    b.close,
                    b.volume,
                    b.trade_count,
                    b.buy_volume,
                    b.sell_volume,
                    b.bid_px,
                    b.ask_px,
                    b.partial,
                )  # fmt: skip
            )
    return rows


async def _all_available(pool: AsyncConnectionPool[Conn]) -> bool:
    """Every connection can be checked out again, idle (no open transaction).
    The pool returns connections in a worker task, so a checkout with a timeout
    waits for that, where reading ``get_stats`` would race it."""
    conns = [await pool.getconn(timeout=5) for _ in range(POOL_SIZE)]
    try:
        return all(
            c.info.transaction_status is psycopg.pq.TransactionStatus.IDLE
            for c in conns
        )
    finally:
        for conn in conns:
            await pool.putconn(conn)


@pytest.mark.parametrize("n", [1, 7, 60, FIRST_COUNT, FIRST_COUNT + 5])
async def test_bars_equal_the_cutter_over_read_ticks(
    pool: AsyncConnectionPool[Conn], session_migrated_db: str, n: int
) -> None:
    _plan, ticks = await _series(pool, session_migrated_db, read_ticks)
    assert len(ticks) == FIRST_COUNT + SECOND_COUNT

    bars = await _tick_bars(pool, session_migrated_db, n)

    assert bars == _expected(ticks, n)


async def test_window_independence(
    pool: AsyncConnectionPool[Conn], session_migrated_db: str
) -> None:
    n = 9
    full = await _tick_bars(pool, session_migrated_db, n)
    lo = session_start_ns(FIRST) - 60 * SEC
    hi = session_start_ns(SECOND) + 60 * SEC
    rng = random.Random(308)
    for _ in range(8):
        a, b = sorted(rng.randrange(lo, hi) for _ in range(2))
        cut = await _tick_bars(pool, session_migrated_db, n, (a, b))
        assert cut == [r for r in full if a <= r[2] < b], (a, b)
    assert await _tick_bars(pool, session_migrated_db, n, (None, lo)) == []


def _count_fetches(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    original = psycopg.AsyncServerCursor.fetchmany

    async def counting(self: Any, size: int = 0) -> Any:
        calls.append(size)
        return await original(self, size)

    monkeypatch.setattr(psycopg.AsyncServerCursor, "fetchmany", counting)
    return calls


async def test_until_stops_the_scan_early(
    pool: AsyncConnectionPool[Conn],
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", 10)
    calls = _count_fetches(monkeypatch)
    await _tick_bars(pool, session_migrated_db, 5)
    full = len(calls)
    calls.clear()

    until = session_start_ns(FIRST) + 30 * SEC
    bars = await _tick_bars(pool, session_migrated_db, 5, (None, until))

    assert bars and all(r[2] < until for r in bars)
    assert len(calls) < full / 4
    assert await _all_available(pool)


async def test_stop_after_the_first_batch_abandons_the_read(
    pool: AsyncConnectionPool[Conn],
    session_migrated_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tick_reads, "TICK_GET_FETCH_ROWS", 10)
    calls = _count_fetches(monkeypatch)
    asked: list[int] = []

    async def stop() -> bool:
        asked.append(1)
        return len(asked) > 1

    pushed: list[int] = []

    async def spy(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        def counting(rows: Sequence[Row]) -> None:
            pushed.append(len(rows))
            on_batch(rows)

        return await _bars_reader(1, OPEN, stop)(conn, window, counting)

    with pytest.raises(ReadAbandoned):
        await _series(pool, session_migrated_db, spy)

    assert len(calls) == 1 and pushed == [10]
    assert await _all_available(pool)


async def test_sentinel_price_raises_window_error(
    pool: AsyncConnectionPool[Conn],
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
) -> None:
    ts = session_start_ns(SECOND) + 3600 * SEC
    bad = Tick(ts, UNDEF_INT64, sequence=10_000)
    seed_ticks(migrated_tick_db, seeded.units[SECOND], ESZ4.instrument_id, [bad])

    with pytest.raises(TickBarWindowError) as raised:
        await _tick_bars(pool, session_migrated_db, 3)

    error = raised.value
    assert isinstance(error.__cause__, TickBarInputError)
    assert (error.session_date, error.raw_symbol) == (SECOND, "ESZ4")
    assert error.instrument_id == ESZ4.instrument_id
    assert error.__cause__.keys[1] == (ts, 10_000, 0)
    assert await _all_available(pool)


# -- admission (D5) ------------------------------------------------------------


async def test_admission_sum_equals_the_rows_read(
    pool: AsyncConnectionPool[Conn], session_migrated_db: str
) -> None:
    plan, ticks = await _series(pool, session_migrated_db, read_ticks)
    async with pool.connection() as conn:
        assert await trades_to_scan(conn, plan, OPEN) == len(ticks)
        second_only = (session_start_ns(SECOND) - 60 * SEC, None)
        assert await trades_to_scan(conn, plan, second_only) == SECOND_COUNT
        first_only = (None, session_start_ns(FIRST) + 60 * SEC)
        assert await trades_to_scan(conn, plan, first_only) == FIRST_COUNT


async def test_admission_over_the_limit_refuses_before_any_read(
    pool: AsyncConnectionPool[Conn], session_migrated_db: str
) -> None:
    reads: list[TickWindow] = []

    async def recording(conn: Conn, window: TickWindow, on_batch: OnBatch) -> int:
        reads.append(window)
        return 0

    total = FIRST_COUNT + SECOND_COUNT
    with pytest.raises(TradeScanExceeded) as raised:
        await _series(
            pool, session_migrated_db, recording, trade_admission(OPEN, total - 1)
        )

    assert (raised.value.scanned, raised.value.limit) == (total, total - 1)
    assert f"{total:,} trades" in str(raised.value)
    assert reads == []
    await _series(pool, session_migrated_db, recording, trade_admission(OPEN, total))
    assert len(reads) == 2


async def test_admission_counts_no_ledger_row_as_zero(
    pool: AsyncConnectionPool[Conn],
    seeded: SeededRoll,
    migrated_tick_db: str,
    session_migrated_db: str,
) -> None:
    # The fixture's own throwaway database: dropping one ledger row is safe.
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        conn.execute(
            "DELETE FROM tick_ingest_ledger WHERE instrument_id = %s"
            " AND session_date = %s",
            (ESZ4.instrument_id, SECOND),
        )
    plan, _ = await _series(pool, session_migrated_db, read_ticks)
    async with pool.connection() as conn:
        assert await trades_to_scan(conn, plan, OPEN) == FIRST_COUNT
