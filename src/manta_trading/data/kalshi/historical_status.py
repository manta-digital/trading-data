"""The historical line of ``mt data kalshi status`` (slice 267).

Read-only, synchronous psycopg — two short reads of ``kalshi.sync_state``,
the ``trade_status.py`` pattern. Every figure is a persisted fact: the
``historical`` row (the tape's bottom, the archive walk's cursor, the
target floor) and the live ``trades`` row's ``coverage_from_ts`` (the
tape's top). Neither the client nor the transport is imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.constants import HISTORICAL_TRADES_FLOOR, Surface
from manta_trading.data.kalshi.sync_types import iso_utc


@dataclass(frozen=True)
class HistoricalStatus:
    """The historical line (design *Implementation Details*).

    ``tape_from`` is the historical watermark — the oldest hour fully walked
    — and is ``None`` until the archive walk is done and the row is seeded;
    ``tape_to`` is the live floor the descent started from. ``floor`` is the
    target: the row's recorded ``coverage_from_ts`` once seeded, the
    constant before (they are the same value by construction).
    """

    last_phase_at: datetime | None
    archive_walked: bool
    archive_in_progress: bool
    tape_from: datetime | None
    tape_to: datetime | None
    floor: datetime
    floor_reached: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_phase_at": iso_utc(self.last_phase_at),
            "archive_walked": self.archive_walked,
            "archive_in_progress": self.archive_in_progress,
            "tape_from": iso_utc(self.tape_from),
            "tape_to": iso_utc(self.tape_to),
            "floor": iso_utc(self.floor),
            "floor_reached": self.floor_reached,
        }


#: One row of ``sync_state`` by surface — read once for ``historical`` and
#: once for ``trades`` (only ``coverage_from_ts`` of the latter is used).
STATE_QUERY = sql.SQL(
    "SELECT last_full_sync_at, watermark_ts, coverage_from_ts, cursor "
    "FROM kalshi.sync_state WHERE surface = %(surface)s"
)


def read_historical_status(conn: psycopg.Connection[Any]) -> HistoricalStatus | None:
    """``None`` until the historical phase has run once (no row)."""
    row = conn.execute(STATE_QUERY, {"surface": Surface.HISTORICAL.value}).fetchone()
    if row is None:
        return None
    last_phase_at, watermark, recorded_floor, cursor = row
    live = conn.execute(STATE_QUERY, {"surface": Surface.TRADES.value}).fetchone()
    tape_to = live[2] if live is not None else None
    floor = recorded_floor if recorded_floor is not None else HISTORICAL_TRADES_FLOOR
    return HistoricalStatus(
        last_phase_at=last_phase_at,
        # Design *State*: cursor set = walk in progress; cursor NULL with a
        # watermark = walked (the trades step seeds the watermark only after).
        archive_walked=cursor is None and watermark is not None,
        archive_in_progress=cursor is not None,
        tape_from=watermark,
        tape_to=tape_to,
        floor=floor,
        floor_reached=watermark is not None and watermark <= floor,
    )
