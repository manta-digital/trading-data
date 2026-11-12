"""Seed ESZ4 trades for the tick-bar reader and route tests (slice 308).

On top of ``seed_september_roll``: ``FIRST_COUNT`` records in session
2024-09-18 (one a second from 14:00 UTC, every ``REPEAT_EVERY``th trade
repeated as an ordinal-1 record sharing its ``(ts_event, sequence)``, sides
cycling ``B``/``A``/``N``, a quote on even trades) and ``SECOND_COUNT`` in
2024-09-19, plus ``ROLL_COUNT`` ESU4 trades in 2024-09-17, the session before
``v.0`` rolls to ESZ4. The ledger's record counts are set to match, so
admission sums equal what the reads return.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import psycopg
from tick_support.roll_seed import ESU4, ESZ4, SeededRoll, seed_september_roll
from tick_support.ticks import Tick, seed_ticks

from manta_trading.data.tick.ingest_records import utc_ns

FIRST, SECOND = date(2024, 9, 18), date(2024, 9, 19)
#: The last ESU4 session under ``v.0``; ESZ4 serves from ``FIRST``.
BEFORE_ROLL = date(2024, 9, 17)
FIRST_TRADES, SECOND_TRADES = 230, 40
REPEAT_EVERY = 20
#: Records per session: the repeats are separate trades.
FIRST_COUNT = FIRST_TRADES + len(range(0, FIRST_TRADES, REPEAT_EVERY))
SECOND_COUNT = SECOND_TRADES
ROLL_COUNT = 25
BASE_PRICE = 5_650_000_000_000
TICK = 250_000_000
_SIDES = ("B", "A", "N")


def session_start_ns(session: date) -> int:
    return utc_ns(datetime(session.year, session.month, session.day, 14, tzinfo=UTC))


def _ticks(session: date, trades: int, repeat_every: int | None) -> list[Tick]:
    start = session_start_ns(session)
    out = []
    for n in range(trades):
        ts = start + n * 1_000_000_000
        price = BASE_PRICE + ((n * 7) % 13) * TICK
        bbo = (price - TICK, price, 3, 4, 1, 1) if n % 2 == 0 else None
        tick = Tick(ts, price, size=1 + n % 5, sequence=n, side=_SIDES[n % 3], bbo=bbo)
        out.append(tick)
        if repeat_every and n % repeat_every == 0:
            out.append(
                Tick(ts, price + TICK, size=2, sequence=n, sequence_ordinal=1, side="B")
            )
    return out


def seed_tick_bar_sessions(url: str) -> SeededRoll:
    roll = seed_september_roll(url)
    plan = (
        (BEFORE_ROLL, ESU4.instrument_id, _ticks(BEFORE_ROLL, ROLL_COUNT, None)),
        (FIRST, ESZ4.instrument_id, _ticks(FIRST, FIRST_TRADES, REPEAT_EVERY)),
        (SECOND, ESZ4.instrument_id, _ticks(SECOND, SECOND_TRADES, None)),
    )
    for session, instrument_id, ticks in plan:
        seed_ticks(url, roll.units[session], instrument_id, ticks)
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(
                "UPDATE tick_ingest_ledger SET record_count = %s"
                " WHERE unit_id = %s AND instrument_id = %s AND session_date = %s",
                (len(ticks), roll.units[session], instrument_id, session),
            )
    return roll
