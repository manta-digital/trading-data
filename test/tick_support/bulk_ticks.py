"""Bulk tick seeds for the load tier (slice 231, task 9.1).

``seed_minute_roll`` lays a roll across many real CME sessions: the roll
seed's definitions, an ingested unit for every day the sessions touch, ledger
volumes that rank ESU4 then ESZ4, and one tick and one provider ``ohlcv-1m``
bar a minute through each session for both contracts, loaded with ``COPY``
(306: the bars routes read ``tick_ohlcv_1m``, so the bars carry the read). A
product ``1m`` bars read then returns one bar per minute of each served
session: about 1,380 a session.

The busiest real session is seeded elsewhere, from the archive, through the
real ingest pass (``tier_units.seed_tier_unit``), as slice 226's load test does.
Synchronous psycopg: fixtures, not the code under test.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
from tick_support.roll_seed import (
    ESH5,
    ESU4,
    ESZ4,
    seed_definitions,
    seed_ledger,
    seed_tier_days,
    touched_days,
)
from tick_support.rows import insert_bar_ledger_row

from manta_trading.data.base.session_index import Session
from manta_trading.data.tick.bar_constants import BAR_1M_COLUMNS, BAR_1M_TABLE
from manta_trading.data.tick.constants import TickSchema, calendar_for_product
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.status_reads import calendar_sessions

#: ``tick_trade``'s columns, in the order :func:`_rows` yields them.
_COLUMNS = (
    "ts_event",
    "ts_recv",
    "instrument_id",
    "publisher_id",
    "sequence",
    "sequence_ordinal",
    "price",
    "size",
    "action",
    "side",
    "flags",
    "depth",
    "ts_in_delta",
    "unit_id",
)
_MINUTE_NS = 60 * 10**9
_PRICE = 5_650_250_000_000
#: ESZ4 out-trades ESU4 from this session on, so ``v.0`` rolls two sessions
#: later (its volume rank lags by a session).
CROSS = date(2024, 9, 16)


@dataclass(frozen=True)
class MinuteRoll:
    sessions: tuple[Session, ...]
    ticks: int
    bars: int


def _rows(
    session: Session, instrument_id: int, unit_id: int
) -> Iterator[tuple[Any, ...]]:
    open_ns, close_ns = utc_ns(session.open_utc), utc_ns(session.close_utc)
    for n, ts in enumerate(range(open_ns + 10**9, close_ns, _MINUTE_NS)):
        yield (
            ts,
            ts + 1,
            instrument_id,
            1,
            n,
            0,
            _PRICE + n,
            1,
            "T",
            "A",
            0,
            0,
            0,
            unit_id,
        )


def _bar_rows(
    session: Session, instrument_id: int, unit_id: int
) -> Iterator[tuple[Any, ...]]:
    """One provider bar a minute, stamped as the ticks are, in ``BAR_1M_COLUMNS``
    order."""
    open_ns, close_ns = utc_ns(session.open_utc), utc_ns(session.close_utc)
    for n, ts in enumerate(range(open_ns + 10**9, close_ns, _MINUTE_NS)):
        price = _PRICE + n
        yield (
            ts,
            instrument_id,
            1,
            price,
            price + 100,
            price - 100,
            price + 50,
            1,
            unit_id,
        )


def _copy(
    url: str, table: str, columns: Sequence[str], rows: Iterator[tuple[Any, ...]]
) -> int:
    count = 0
    with psycopg.connect(url, autocommit=True) as conn, conn.cursor() as cur:
        with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
                count += 1
    return count


def _volumes(sessions: Sequence[Session]) -> dict[tuple[date, int], int]:
    volumes: dict[tuple[date, int], int] = {}
    for s in sessions:
        crossed = s.session_date >= CROSS
        volumes[s.session_date, ESU4.instrument_id] = 400 if crossed else 600
        volumes[s.session_date, ESZ4.instrument_id] = 700 if crossed else 300
        volumes[s.session_date, ESH5.instrument_id] = 5
    return volumes


def seed_minute_roll(
    tick_url: str, calendar_url: str, first: date, end: date
) -> MinuteRoll:
    """The roll over every CME session dated ``[first, end)``."""
    sessions = tuple(
        s
        for s in calendar_sessions(
            calendar_url,
            calendar_for_product("ES"),
            datetime.combine(first - timedelta(days=1), datetime.min.time(), UTC),
            datetime.combine(end, datetime.min.time(), UTC),
        )
        if first <= s.session_date < end
    )
    seed_definitions(tick_url, [ESU4, ESZ4, ESH5], first - timedelta(days=3))
    units = seed_tier_days(tick_url, touched_days(s.session_date for s in sessions))
    seed_ledger(tick_url, units, _volumes(sessions))
    ticks = _copy(
        tick_url,
        "tick_trade",
        _COLUMNS,
        (
            row
            for s in sessions
            for contract in (ESU4, ESZ4)
            for row in _rows(s, contract.instrument_id, units[s.session_date])
        ),
    )
    bars = _seed_bars(tick_url, sessions)
    return MinuteRoll(sessions, ticks, bars)


def _seed_bars(tick_url: str, sessions: Sequence[Session]) -> int:
    """Provider bars for both contracts over ``sessions``: ``ohlcv-1m`` units
    for every day they touch, a ledger row per (session, contract), and the
    minute rows."""
    units = seed_tier_days(
        tick_url, touched_days(s.session_date for s in sessions), TickSchema.OHLCV_1M
    )
    with psycopg.connect(tick_url, autocommit=True) as conn:
        for s in sessions:
            count = sum(1 for _ in _bar_rows(s, ESU4.instrument_id, 0))
            for contract in (ESU4, ESZ4):
                insert_bar_ledger_row(
                    conn,
                    units[s.session_date],
                    instrument_id=contract.instrument_id,
                    session_date=s.session_date,
                    record_count=count,
                    volume=count,
                )
    return _copy(
        tick_url,
        BAR_1M_TABLE,
        BAR_1M_COLUMNS,
        (
            row
            for s in sessions
            for contract in (ESU4, ESZ4)
            for row in _bar_rows(s, contract.instrument_id, units[s.session_date])
        ),
    )
