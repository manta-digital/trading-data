"""Seed ``tick_trade`` rows with chosen keys and prices (slice 230 tests).

``rows.insert_trade`` inserts one tbbo-shaped row; this seeds many for one
instrument and unit, trades-shaped (BBO null) unless a row carries a quote.
Synchronous psycopg: fixtures, not the code under test.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import psycopg
from tick_support.rows import insert_trade

#: Every BBO column; null together on a trades-tier row (222's check).
BBO_COLUMNS = (
    "bid_px_00",
    "ask_px_00",
    "bid_sz_00",
    "ask_sz_00",
    "bid_ct_00",
    "ask_ct_00",
)


@dataclass(frozen=True)
class Tick:
    ts_event: int
    price: int
    size: int = 1
    sequence: int = 1
    sequence_ordinal: int = 0
    side: str = "A"
    #: ``(bid_px, ask_px, bid_sz, ask_sz, bid_ct, ask_ct)``; ``None`` = trades tier.
    bbo: tuple[int, int, int, int, int, int] | None = None


def seed_ticks(
    url: str, unit_id: int, instrument_id: int, ticks: Iterable[Tick]
) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for tick in ticks:
            insert_trade(conn, unit_id, **_row(instrument_id, tick))


def _row(instrument_id: int, tick: Tick) -> dict[str, Any]:
    bbo = tick.bbo or (None,) * len(BBO_COLUMNS)
    return {
        "instrument_id": instrument_id,
        "ts_event": tick.ts_event,
        "ts_recv": tick.ts_event + 1,
        "price": tick.price,
        "size": tick.size,
        "sequence": tick.sequence,
        "sequence_ordinal": tick.sequence_ordinal,
        "side": tick.side,
        **dict(zip(BBO_COLUMNS, bbo, strict=True)),
    }
