"""Seed provider bars beside the September roll (slice 306 tests).

An ingested ``ohlcv-1m`` unit per day the sessions touch, a bar ledger row per
(session, instrument), and optionally real minute rows in ``tick_ohlcv_1m``.
Call once per database: a second call over the same days would add a second
current unit for each of them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import psycopg
from tick_support.roll_seed import seed_tier_days, touched_days
from tick_support.rows import insert_bar_ledger_row

from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.constants import TickSchema

#: ``(session_date, instrument_id)`` → bar volume on the ledger.
BarVolumes = Mapping[tuple[date, int], int]
#: ``(session_date, instrument_id)`` → ``(ts_event, price)`` minute bars.
BarMinutes = Mapping[tuple[date, int], Sequence[tuple[int, int]]]


def seed_bar_sessions(
    url: str, volumes: BarVolumes, minutes: BarMinutes | None = None
) -> dict[date, int]:
    """Bar units, ledger rows and minute rows; returns the unit per UTC day."""
    units = seed_tier_days(
        url, touched_days({session for session, _ in volumes}), TickSchema.OHLCV_1M
    )
    with psycopg.connect(url, autocommit=True) as conn:
        for (session, instrument), volume in volumes.items():
            insert_bar_ledger_row(
                conn,
                units[session],
                instrument_id=instrument,
                session_date=session,
                record_count=volume,
                volume=volume,
            )
        for (session, instrument), bars in (minutes or {}).items():
            for ts_event, price in bars:
                conn.execute(
                    f"INSERT INTO {BAR_1M_TABLE} (ts_event, instrument_id,"
                    " publisher_id, open, high, low, close, volume, unit_id)"
                    " VALUES (%s, %s, 1, %s, %s, %s, %s, 1, %s)",
                    (
                        ts_event,
                        instrument,
                        price,
                        price + 100,
                        price - 100,
                        price + 50,
                        units[session],
                    ),
                )
    return units
