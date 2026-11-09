"""``read_provider_bars`` on the derived 2024-12-03 bar day (slice 306, D2, D5).

The bar day is the real ``ohlcv-1m`` slice, ingested through the worker. Rollup
expectations are computed here, in Python, from the same stored one-minute rows;
the bucket formula has no Python copy in ``src``.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import databento
import psycopg
import pytest
from tick_support.dbn_files import FIXTURES
from tick_support.ingest import plan_unit, run_worker
from tick_support.reads import bar_dicts, read_rows, session_window
from tick_support.tick_bar_helpers import seed_bar_units
from tick_support.tier_units import TBBO_DAY

from manta_trading.constants import GRANULARITY_BAR_MINUTES, Granularity
from manta_trading.data.tick.bar_constants import BAR_1M_TABLE
from manta_trading.data.tick.bar_reads import NS_PER_MINUTE, read_provider_bars
from manta_trading.data.tick.ingest_plan import Calendars
from manta_trading.data.tick.ingest_worker import UnitResult
from manta_trading.data.tick.tick_reads import TickWindow

COLUMNS = "ts_event, open, high, low, close, volume"
SIZES = (
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
    Granularity.D1,
)


@dataclass(frozen=True)
class BarDay:
    url: str
    window: TickWindow
    minutes: list[tuple[int, ...]]  # (ts_event, open, high, low, close, volume)


def stored_minutes(url: str, window: TickWindow) -> list[tuple[int, ...]]:
    with psycopg.connect(url) as conn:
        return [
            tuple(row)
            for row in conn.execute(
                f"SELECT {COLUMNS} FROM {BAR_1M_TABLE}"
                " WHERE instrument_id = %s AND ts_event BETWEEN %s AND %s"
                " ORDER BY ts_event",
                (window.instrument_id, window.open_ns, window.close_ns),
            )
        ]


@pytest.fixture
def calendars(session_migrated_db: str) -> Iterator[Calendars]:
    cache = Calendars(session_migrated_db)
    yield cache
    cache.close()


@pytest.fixture
async def bar_day(
    migrated_tick_db: str,
    session_migrated_db: str,
    tmp_path: Path,
    calendars: Calendars,
) -> BarDay:
    url = migrated_tick_db
    seeded = await seed_bar_units(url, tmp_path, job_id="BARS")
    plan = await plan_unit(url, calendars, seeded.unit_id)
    outcome = run_worker(plan, tmp_path, url)
    assert outcome.result is UnitResult.INGESTED, outcome.reason
    with psycopg.connect(url) as conn:
        row = conn.execute(
            f"SELECT instrument_id FROM {BAR_1M_TABLE}"
            " GROUP BY instrument_id ORDER BY count(*) DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    window = session_window(session_migrated_db, TBBO_DAY, int(row[0]))
    minutes = stored_minutes(url, window)
    assert minutes, "the derived day holds no bars for its busiest instrument"
    return BarDay(url, window, minutes)


def rolled(day: BarDay, granularity: Granularity) -> list[tuple[int, ...]]:
    """The expected bars, from the stored minutes: session-anchored buckets."""
    open_ns = day.window.open_ns
    if granularity is Granularity.D1:
        width = None
    else:
        width = GRANULARITY_BAR_MINUTES[granularity] * NS_PER_MINUTE
    buckets: dict[int, list[tuple[int, ...]]] = {}
    for minute in day.minutes:
        start = (
            open_ns
            if width is None
            else open_ns + (minute[0] - open_ns) // width * width
        )
        buckets.setdefault(start, []).append(minute)
    return [
        (
            start,
            group[0][1],
            max(m[2] for m in group),
            min(m[3] for m in group),
            group[-1][4],
            sum(m[5] for m in group),
        )
        for start, group in sorted(buckets.items())
    ]


def served(rows: list[Any]) -> list[tuple[int, ...]]:
    """(time, open, high, low, close, volume) of served bar rows."""
    return [
        tuple(b[k] for k in ("time", "open", "high", "low", "close", "volume"))
        for b in bar_dicts(rows)
    ]


async def test_one_minute_bars_equal_the_stored_rows(bar_day: BarDay) -> None:
    rows = await read_rows(
        bar_day.url, read_provider_bars, bar_day.window, Granularity.M1
    )

    assert served(rows) == bar_day.minutes  # count, prices and volume, exactly
    first = bar_dicts(rows)[0]
    assert first["session_date"] == TBBO_DAY
    assert first["instrument_id"] == bar_day.window.instrument_id
    assert first["raw_symbol"] == "ESZ4"


@pytest.mark.parametrize("granularity", SIZES)
async def test_coarser_bars_equal_the_rollup_of_the_stored_minutes(
    bar_day: BarDay, granularity: Granularity
) -> None:
    rows = await read_rows(bar_day.url, read_provider_bars, bar_day.window, granularity)

    assert served(rows) == rolled(bar_day, granularity)


async def test_a_day_bar_is_one_bucket_stamped_at_the_open(bar_day: BarDay) -> None:
    rows = await read_rows(
        bar_day.url, read_provider_bars, bar_day.window, Granularity.D1
    )

    assert [b["time"] for b in bar_dicts(rows)] == [bar_day.window.open_ns]


async def test_4h_buckets_start_at_the_session_open_plus_whole_widths(
    bar_day: BarDay,
) -> None:
    """2024-12-03 is standard time: 17:00 CT is 23:00Z (criterion 3)."""
    rows = await read_rows(
        bar_day.url, read_provider_bars, bar_day.window, Granularity.H4
    )

    starts = [
        datetime.fromtimestamp(b["time"] / 1e9, UTC).strftime("%H:%M")
        for b in bar_dicts(rows)
    ]
    assert starts == ["23:00", "03:00", "07:00", "11:00", "15:00", "19:00"]


@pytest.mark.parametrize(
    "granularity", [Granularity.M5, Granularity.M15, Granularity.H1]
)
async def test_short_bars_start_on_whole_multiples_of_the_width(
    bar_day: BarDay, granularity: Granularity
) -> None:
    """The pre-306 boundaries: the open is on the hour in both DST states."""
    width = GRANULARITY_BAR_MINUTES[granularity] * NS_PER_MINUTE
    rows = await read_rows(bar_day.url, read_provider_bars, bar_day.window, granularity)

    for bar in bar_dicts(rows):
        assert (bar["time"] - bar_day.window.open_ns) % width == 0
        assert bar["time"] % width == 0  # also on the UTC-epoch grid


async def test_a_window_with_no_bars_returns_nothing(bar_day: BarDay) -> None:
    empty = TickWindow(
        date(2024, 12, 9),
        bar_day.window.instrument_id,
        "ESZ4",
        bar_day.window.open_ns + 5 * 86_400 * 10**9,
        bar_day.window.close_ns + 5 * 86_400 * 10**9,
    )
    for granularity in (Granularity.M1, Granularity.H4, Granularity.D1):
        assert (
            await read_rows(bar_day.url, read_provider_bars, empty, granularity) == []
        )


async def test_the_dbn_sample_bars_come_back_unscaled_and_unshifted(
    migrated_tick_db: str,
) -> None:
    """Two real bars (ESH1, 2020-12-28) decoded from the DBN sample."""
    records = databento.DBNStore.from_file(
        FIXTURES / "test_data.ohlcv-1m.v1.dbn.zst"
    ).to_ndarray()
    expected = [
        tuple(
            int(r[name])
            for name in ("ts_event", "open", "high", "low", "close", "volume")
        )
        for r in records
    ]
    instrument = int(records[0]["instrument_id"])
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        for r in records:
            conn.execute(
                f"INSERT INTO {BAR_1M_TABLE} (ts_event, instrument_id, publisher_id,"
                " open, high, low, close, volume, unit_id)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)",
                (
                    int(r["ts_event"]),
                    instrument,
                    int(r["publisher_id"]),
                    *(int(r[n]) for n in ("open", "high", "low", "close", "volume")),
                ),
            )
    window = TickWindow(
        date(2020, 12, 28), instrument, "ESH1", expected[0][0], expected[-1][0]
    )

    rows = await read_rows(migrated_tick_db, read_provider_bars, window, Granularity.M1)

    assert served(rows) == expected
    assert len(expected) == 2
