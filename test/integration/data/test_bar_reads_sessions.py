"""Session-anchored buckets across a DST change and an early close (306, D5).

Criterion 3. Minute bars are inserted straight into ``tick_ohlcv_1m`` (the
reader reads only that table); the windows come from the production calendar.
Ingest placement of real bars is 302's and has its own tests.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import psycopg
from tick_support.reads import bar_dicts, read_rows, session_window

from manta_trading.constants import Granularity
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.bar_reads import NS_PER_MINUTE, read_provider_bars
from manta_trading.data.tick.tick_reads import TickWindow

INSTRUMENT = 183_748
HOUR_NS = 60 * NS_PER_MINUTE
PRICE = 5_000_000_000_000


def _seed_session(
    url: str, window: TickWindow, *, step_minutes: int = 30, extra_after: int = 0
) -> None:
    """A bar every ``step_minutes`` from the open through the close, and
    optionally one ``extra_after`` minutes past the close (outside the window)."""
    stamps = list(
        range(window.open_ns, window.close_ns + 1, step_minutes * NS_PER_MINUTE)
    )
    if extra_after:
        stamps.append(window.close_ns + extra_after * NS_PER_MINUTE)
    with psycopg.connect(url, autocommit=True) as conn:
        for n, ts in enumerate(stamps):
            conn.execute(
                f"INSERT INTO {BAR_1M_TABLE} (ts_event, instrument_id, publisher_id,"
                " open, high, low, close, volume, unit_id)"
                " VALUES (%s, %s, 1, %s, %s, %s, %s, 1, 1)",
                (
                    ts,
                    INSTRUMENT,
                    PRICE + n,
                    PRICE + n + 2,
                    PRICE + n - 2,
                    PRICE + n + 1,
                ),
            )


def _hours(rows: Sequence[Sequence[Any]]) -> list[str]:
    return [
        datetime.fromtimestamp(b["time"] / 1e9, UTC).strftime("%H:%M")
        for b in bar_dicts(rows)
    ]


async def test_4h_buckets_follow_the_open_on_both_sides_of_the_dst_change(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    """2024-11-03 ends US daylight time: 17:00 CT is 22:00Z, then 23:00Z."""
    before = session_window(session_migrated_db, date(2024, 11, 1), INSTRUMENT)
    after = session_window(session_migrated_db, date(2024, 11, 4), INSTRUMENT)
    for window in (before, after):
        _seed_session(migrated_tick_db, window)

    starts = {}
    for name, window in (("before", before), ("after", after)):
        rows = await read_rows(
            migrated_tick_db, read_provider_bars, window, Granularity.H4
        )
        starts[name] = _hours(rows)

    assert starts["before"] == ["22:00", "02:00", "06:00", "10:00", "14:00", "18:00"]
    assert starts["after"] == ["23:00", "03:00", "07:00", "11:00", "15:00", "19:00"]


async def test_the_last_4h_bucket_of_an_early_close_holds_nothing_after_the_close(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    """Friday 2024-11-29, the day after Thanksgiving, closes early."""
    window = session_window(session_migrated_db, date(2024, 11, 29), INSTRUMENT)
    regular = 23 * HOUR_NS
    assert window.close_ns - window.open_ns < regular, "calendar has no early close"
    _seed_session(migrated_tick_db, window, step_minutes=15, extra_after=15)

    for granularity in (Granularity.M1, Granularity.H4):
        served = bar_dicts(
            await read_rows(migrated_tick_db, read_provider_bars, window, granularity)
        )
        assert max(b["time"] for b in served) <= window.close_ns

    rows = bar_dicts(
        await read_rows(migrated_tick_db, read_provider_bars, window, Granularity.H4)
    )
    last = rows[-1]
    assert window.open_ns <= last["time"] <= window.close_ns
    # The bucket is cut at the close: its last bar is the one stamped at it
    # (bar n closes at PRICE + n + 1).
    assert last["close"] == PRICE + _bars_in(window)


def _bars_in(window: TickWindow) -> int:
    """How many seeded bars fall in the window (every 15 minutes, closed)."""
    return (window.close_ns - window.open_ns) // (15 * NS_PER_MINUTE) + 1


async def test_a_bucket_never_crosses_the_daily_break(
    migrated_tick_db: str, session_migrated_db: str
) -> None:
    """Ported from the read-time 4h test: two adjacent sessions, each read apart.
    The next session's first bar starts at its own open, and no bar of one holds
    a minute of the other."""
    first = session_window(session_migrated_db, date(2024, 11, 4), INSTRUMENT)
    second = session_window(session_migrated_db, date(2024, 11, 5), INSTRUMENT)
    assert first.close_ns < second.open_ns  # the break between them
    for window in (first, second):
        _seed_session(migrated_tick_db, window)

    served = {}
    for name, window in (("first", first), ("second", second)):
        served[name] = bar_dicts(
            await read_rows(
                migrated_tick_db, read_provider_bars, window, Granularity.H4
            )
        )

    assert served["first"][0]["time"] == first.open_ns
    assert served["second"][0]["time"] == second.open_ns
    assert served["first"][-1]["time"] < first.close_ns
    per_session = (first.close_ns - first.open_ns) // (30 * 60 * 10**9) + 1
    assert sum(b["volume"] for b in served["first"]) == per_session
