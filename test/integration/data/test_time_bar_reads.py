"""Slice 309: the time-bar reader and its admission, on the real parity day.

``seed_parity`` holds 2024-12-03 complete in ticks and in provider one-minute
bars; 12-02 and 12-04 have empty ingested units; 12-05 holds nothing.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.parity_seed import SESSION, ParityDb, seed_parity, tick_minutes
from tick_support.reads import read_rows

from manta_trading.api_server.futures_read import read_series
from manta_trading.data.tick import time_bar_reads
from manta_trading.data.tick.bar_constants import TICKS_SOURCE
from manta_trading.data.tick.bar_reads import read_provider_bars
from manta_trading.data.tick.constants import TickSchema, UnitState
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.schema_families import FAMILY_LEDGER, SchemaFamily
from manta_trading.data.tick.tick_bar_reads import ReadAbandoned
from manta_trading.data.tick.tick_reads import BAR_COLUMNS, Row
from manta_trading.data.tick.time_bar_reads import (
    LedgerCountExceeded,
    TimeBarRead,
    TimeBarScanExceeded,
    time_bar_trades,
)
from manta_trading.data.tick.time_bar_size import parse_bar_size
from manta_trading.data.tick.time_bar_source import choose_sources

END = date(2024, 12, 6)
NEXT = date(2024, 12, 4)
NEITHER = date(2024, 12, 5)
OPEN = (None, None)
CEILING = 1_000_000
BARS = TickSchema.OHLCV_1M.value
_LEDGER = FAMILY_LEDGER[SchemaFamily.TICK_TIER]


@pytest.fixture
async def db(
    migrated_tick_db: str, session_migrated_db: str, tmp_path: Path
) -> ParityDb:
    return await seed_parity(migrated_tick_db, session_migrated_db, tmp_path)


async def _plan(conn: psycopg.AsyncConnection, db: ParityDb) -> GetPlan:  # type: ignore[type-arg]
    return await contract_plan(
        conn, db.calendar, db.symbol, SESSION, END, SchemaFamily.TICK_TIER
    )


def ledger_count(db: ParityDb, first: date = SESSION, last: date = END) -> int:
    """The tick ledger's records for the contract over sessions ``[first, last)``.
    The 12-03 UTC tick day also opens the 12-04 session, so both hold trades."""
    with psycopg.connect(db.url) as conn:
        row = conn.execute(
            f"SELECT sum(record_count) FROM {FAMILY_LEDGER[SchemaFamily.TICK_TIER]}"
            " WHERE instrument_id = %s AND session_date >= %s AND session_date < %s",
            (db.window.instrument_id, first, last),
        ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0])


async def test_admission_counts_only_tick_cut_sessions(db: ParityDb) -> None:
    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        plan = await _plan(conn, db)
        # 5m: every planned session with trades is held complete in provider
        # bars, so nothing is cut from ticks.
        for token, expected in (("5m", 0), ("15s", ledger_count(db))):
            sources = await choose_sources(
                conn, db.calendar, plan, parse_bar_size(token)
            )
            assert await time_bar_trades(conn, plan, sources, OPEN) == expected


async def test_a_session_with_no_ledger_row_adds_nothing(db: ParityDb) -> None:
    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        plan = await _plan(conn, db)
        sources = await choose_sources(conn, db.calendar, plan, parse_bar_size("15s"))
        assert sources[NEITHER] is None
        forced = dict(sources) | {NEITHER: TICKS_SOURCE}
        assert await time_bar_trades(conn, plan, forced, OPEN) == ledger_count(db)


def _reader(db: ParityDb, token: str, **kwargs: Any) -> TimeBarRead:
    kwargs.setdefault("max_trades", None)
    return TimeBarRead(parse_bar_size(token), db.calendar, **kwargs)


async def _read(db: ParityDb, reader: TimeBarRead) -> list[Row]:
    async def plan_for(conn: Any) -> GetPlan:
        return await _plan(conn, db)

    async with await psycopg.AsyncConnection.connect(db.url) as conn:
        _, rows = await read_series(
            conn, plan_for, reader.read, CEILING, admit=reader.admit
        )
    return rows


def _by_session(rows: list[Row]) -> dict[date, int]:
    counts: dict[date, int] = {}
    for row in rows:
        assert len(row) == len(BAR_COLUMNS)  # the trade count is stripped
        counts[row[1]] = counts.get(row[1], 0) + 1
    return counts


async def test_one_session_from_bars_at_5m_and_from_ticks_at_15s(db: ParityDb) -> None:
    """Design criterion 3, at the reader."""
    five = _reader(db, "5m")
    five_rows = await _read(db, five)
    fifteen = _reader(db, "15s")
    fifteen_rows = await _read(db, fifteen)

    assert five.sources[SESSION] == BARS
    assert fifteen.sources[SESSION] == TICKS_SOURCE
    provider = await read_rows(
        db.url, read_provider_bars, db.window, parse_bar_size("5m")
    )
    assert [r for r in five_rows if r[1] == SESSION] == [tuple(r) for r in provider]
    seconds = tick_minutes(db.url, db.window, 15 * 10**9)
    assert _by_session(fifteen_rows)[SESSION] == len(seconds)
    assert SESSION in five.returned and SESSION in fifteen.returned


def _set_unit_state(db: ParityDb, schema: TickSchema, day: date) -> None:
    with psycopg.connect(db.url, autocommit=True) as conn:
        updated = conn.execute(
            "UPDATE tick_archive_unit u SET state = %s FROM tick_request r"
            " WHERE u.request_id = r.request_id AND r.schema = %s"
            " AND u.unit_date = %s",
            (UnitState.VERIFIED.value, schema.value, day),
        ).rowcount
    assert updated == 1


async def test_a_mixed_range_reads_each_session_from_its_source(db: ParityDb) -> None:
    """The 12-04 bar unit moved back a step makes session 12-04 partial in bars;
    session 12-03 stays complete in both."""
    _set_unit_state(db, TickSchema.OHLCV_1M, NEXT)
    reader = _reader(db, "5m")
    rows = await _read(db, reader)

    assert (reader.sources[SESSION], reader.sources[NEXT]) == (BARS, TICKS_SOURCE)
    assert set(_by_session(rows)) == {SESSION, NEXT}


class _Recorder:
    def __init__(self, real: Any) -> None:
        self.real = real
        self.windows: list[date] = []

    async def __call__(self, conn: Any, window: Any, *args: Any) -> int:
        self.windows.append(window.session_date)
        return await self.real(conn, window, *args)  # type: ignore[no-any-return]


async def test_an_over_limit_cut_is_refused_before_any_tick_is_read(
    db: ParityDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(time_bar_reads.cut_time_bars)
    monkeypatch.setattr(time_bar_reads, "cut_time_bars", recorder)
    reader = _reader(db, "15s", max_trades=ledger_count(db) - 1)

    with pytest.raises(TimeBarScanExceeded) as caught:
        await _read(db, reader)

    assert caught.value.scanned == ledger_count(db)
    assert recorder.windows == []


async def test_a_client_gone_before_the_second_window_stops_the_read(
    db: ParityDb, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder(time_bar_reads.cut_time_bars)
    monkeypatch.setattr(time_bar_reads, "cut_time_bars", recorder)
    calls = 0

    async def stop() -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(ReadAbandoned):
        await _read(db, _reader(db, "15s", stop=stop))

    assert recorder.windows == [SESSION]


async def test_more_trades_than_the_ledger_is_an_error(db: ParityDb) -> None:
    with psycopg.connect(db.url, autocommit=True) as conn:
        conn.execute(
            f"UPDATE {_LEDGER} SET record_count = record_count - 1 WHERE ctid ="
            f" (SELECT ctid FROM {_LEDGER} WHERE instrument_id = %s"
            " AND session_date = %s AND record_count > 0 LIMIT 1)",
            (db.window.instrument_id, SESSION),
        )

    with pytest.raises(LedgerCountExceeded) as caught:
        await _read(db, _reader(db, "15s"))

    assert caught.value.session_date == SESSION
    assert caught.value.read == caught.value.ledger + 1


async def test_a_ledger_row_gone_after_admission_is_an_error(db: ParityDb) -> None:
    """Admission never plans a session with no ledger row; this deletes the row
    between admission and the read (read committed, no snapshot), so the guard
    is what catches it."""
    reader = _reader(db, "15s")
    async with await psycopg.AsyncConnection.connect(db.url, autocommit=True) as conn:
        plan = await _plan(conn, db)
        await reader.admit(conn, plan)
        with psycopg.connect(db.url, autocommit=True) as other:
            other.execute(
                f"DELETE FROM {_LEDGER} WHERE instrument_id = %s AND session_date = %s",
                (db.window.instrument_id, SESSION),
            )
        with pytest.raises(LedgerCountExceeded) as caught:
            async with conn.transaction():  # the named cursor needs one
                await reader.read(conn, plan.segments[0], lambda batch: None)

    assert (caught.value.session_date, caught.value.ledger) == (SESSION, 0)
