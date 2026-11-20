"""Session-aligned bars from ``get_session_bars`` (slice 174 task 9.4).

One module database (full chain, so NYSE sessions carry extended bounds) with
every minute of six sessions seeded for two symbols, AAA clean and BBB with
off-tape marks. Each bar is checked against an independent Python oracle:
``session_phases.assign`` places each eligible minute, and the bar is the
aggregate of the minutes placed in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg
import pytest

from manta_trading.constants import (
    GRANULARITY_BAR_MINUTES,
    Granularity,
    PrintMode,
    SessionMode,
    SessionPhase,
)
from manta_trading.data.base.trading_calendar import OutOfHorizonError
from manta_trading.data.quality.off_tape import eligible_bar
from manta_trading.market.session_bars import (
    MissingExtendedBoundsError,
    read_session_bars,
    session_bounds,
)
from manta_trading.market.session_phases import SessionBounds, assign, estimate_bars
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FULL, EARLY = date(2026, 1, 14), date(2025, 11, 28)
DST = [date(2025, 10, 31), date(2025, 11, 3), date(2026, 3, 6), date(2026, 3, 9)]
SESSIONS = [FULL, EARLY, *DST]
INTRADAY = [
    Granularity.M1,
    Granularity.M5,
    Granularity.M15,
    Granularity.H1,
    Granularity.H4,
]
MODES = [SessionMode.REGULAR, SessionMode.EXTENDED]
REG, EXT = SessionMode.REGULAR, SessionMode.EXTENDED
ALL_MARKED_ET = "10:00"  # BBB: every minute of this 5m bucket is fully marked
_ET = ZoneInfo("America/New_York")


def _price(i: int, k: int) -> float:
    return round(100 + (i * 7 + k) % 41 * 0.25, 4)


def _minutes(bounds: SessionBounds, symbol: str) -> list[tuple[Any, ...]]:
    """Every minute from the extended open to the extended close inclusive
    (the close-stamp minute too), with deterministic prices and BBB's marks."""
    assert bounds.extended_open and bounds.extended_close
    rows, ts, i = [], bounds.extended_open, 0
    while ts <= bounds.extended_close:
        o, h, lo, c = _price(i, 0), _price(i, 3) + 1, _price(i, 5) - 1, _price(i, 9)
        mask = None
        if symbol == "BBB":
            et = ts.astimezone(_ET)
            if et.hour == 10 and et.minute < 5:
                mask = 15
            elif i % 17 == 0:
                mask = 2  # the high is off-tape: clamped, not dropped
        rows.append((ts, symbol, o, h, lo, c, 100 + i, mask))
        ts, i = ts + timedelta(minutes=1), i + 1
    return rows


@pytest.fixture(scope="module")
def seeded(module_migrated_db: str) -> dict[str, Any]:
    with psycopg.connect(module_migrated_db) as conn:
        bounds = {
            b.session_date: b
            for d in SESSIONS
            for b in session_bounds(conn, "NYSE", d, d, EXT)
        }
        rows: list[tuple[Any, ...]] = []
        for symbol in ("AAA", "BBB", "CCC"):
            conn.execute(
                "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
                "trading_calendar_id, eodhd_type, eodhd_exchange, first_data_date) "
                "VALUES (%s, %s, 'equity', 'US', 'NYSE', 'Common Stock', 'NYSE', "
                "'2020-01-02')",
                (f"{symbol}.US", symbol),
            )
        for symbol in ("AAA", "BBB"):
            for b in bounds.values():
                rows += _minutes(b, symbol)
        rows += _minutes(bounds[FULL], "CCC")
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, "
                "volume, off_tape) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
        # CCC: split with ex-date the day after FULL (criterion 7).
        conn.execute(
            "INSERT INTO splits (symbol, ex_date, ratio_to, ratio_from) "
            "VALUES ('CCC', '2026-01-15', 2, 1)"
        )
    return {"url": module_migrated_db, "bounds": bounds, "rows": rows}


@pytest.fixture(scope="module")
def db(seeded: dict[str, Any]) -> Iterator[TimescaleMinuteDataDB]:
    minute_db = TimescaleMinuteDataDB(seeded["url"])
    yield minute_db
    minute_db.close()


def _read(
    db: TimescaleMinuteDataDB,
    symbol: str,
    granularity: Granularity,
    mode: SessionMode,
    start: date,
    end: date | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    kwargs.setdefault("adjusted", False)
    kwargs.setdefault("prints", PrintMode.ELIGIBLE)
    return db.get_session_bars(symbol, granularity, mode, start, end or start, **kwargs)


def _oracle(
    seeded: dict[str, Any], symbol: str, granularity: Granularity, mode: SessionMode
) -> dict[tuple[datetime, date, str], tuple[float, ...]]:
    width = timedelta(minutes=GRANULARITY_BAR_MINUTES[granularity])
    groups: dict[tuple[datetime, date, str], list[tuple[Any, ...]]] = {}
    for ts, sym, o, h, lo, c, v, mask in seeded["rows"]:
        if sym != symbol:
            continue
        session = seeded["bounds"][ts.astimezone(_ET).date()]
        placed = assign(ts, session, mode, width)
        prices = eligible_bar(o, h, lo, c, mask)
        if placed is None or prices is None:
            continue
        phase, bucket = placed
        groups.setdefault((bucket, session.session_date, str(phase)), []).append(
            (ts, *prices, v)
        )
    result: dict[tuple[datetime, date, str], tuple[float, ...]] = {}
    for key, minutes in groups.items():
        minutes.sort()
        result[key] = (
            minutes[0][1],
            max(m[2] for m in minutes),
            min(m[3] for m in minutes),
            minutes[-1][4],
            sum(m[5] for m in minutes),
        )
    return result


def _as_dict(df: pd.DataFrame) -> dict[tuple[datetime, date, str], tuple[float, ...]]:
    return {
        (ts.to_pydatetime(), row.session_date, row.phase): (
            row.open, row.high, row.low, row.close, row.volume
        )
        for ts, row in df.iterrows()
    }  # fmt: skip


def test_regular_full_session_is_seven_bars_from_0930(
    db: TimescaleMinuteDataDB,
) -> None:
    df = _read(db, "AAA", Granularity.H1, REG, FULL)
    assert len(df) == 7
    assert df.index[0] == datetime(2026, 1, 14, 14, 30, tzinfo=UTC)
    assert set(df["phase"]) == {SessionPhase.REGULAR.value}


def test_early_close_is_four_bars_last_at_1230(db: TimescaleMinuteDataDB) -> None:
    df = _read(db, "AAA", Granularity.H1, REG, EARLY)
    assert len(df) == 4
    assert df.index[-1].tz_convert(_ET).strftime("%H:%M") == "12:30"


def test_extended_phases_six_seven_four(db: TimescaleMinuteDataDB, seeded: Any) -> None:
    df = _read(db, "AAA", Granularity.H1, EXT, FULL)
    assert df["phase"].value_counts().to_dict() == {"pre": 6, "regular": 7, "post": 4}
    b = seeded["bounds"][FULL]
    span = {
        "pre": (b.extended_open, b.open),
        "regular": (b.open, b.close),
        "post": (b.close, b.extended_close),
    }
    # Each bar starts inside its own phase; the last of a phase is short
    # (pre's 09:00 bar holds 30 minutes). Minute placement: the oracle test.
    for ts, row in df.iterrows():
        lo, hi = span[row.phase]
        assert lo <= ts < hi, (ts, row.phase)


@pytest.mark.parametrize("day", DST)
def test_dst_sessions_anchor_at_0930_et(db: TimescaleMinuteDataDB, day: date) -> None:
    df = _read(db, "AAA", Granularity.H1, REG, day)
    assert df.index[0].tz_convert(_ET).strftime("%H:%M") == "09:30"
    assert len(df) == 7


@pytest.mark.parametrize("symbol", ["AAA", "BBB"])
@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("granularity", INTRADAY)
def test_bars_equal_the_eligible_minutes_they_cover(
    db: TimescaleMinuteDataDB,
    seeded: Any,
    symbol: str,
    mode: SessionMode,
    granularity: Granularity,
) -> None:
    df = _read(db, symbol, granularity, mode, min(SESSIONS), max(SESSIONS))
    got, want = _as_dict(df), _oracle(seeded, symbol, granularity, mode)
    assert got.keys() == want.keys()
    for key, values in want.items():
        assert got[key] == pytest.approx(values), key


def test_all_marked_bucket_is_absent(db: TimescaleMinuteDataDB) -> None:
    df = _read(db, "BBB", Granularity.M5, REG, FULL)
    times = set(df.index.tz_convert(_ET).strftime("%H:%M"))
    assert ALL_MARKED_ET not in times and "10:05" in times


def test_close_stamp_minute_is_in_no_bar(
    db: TimescaleMinuteDataDB, seeded: Any
) -> None:
    df = _read(db, "AAA", Granularity.M1, EXT, FULL)
    assert seeded["bounds"][FULL].extended_close not in set(df.index)
    assert len(df) == 960


def test_session_date_independent_of_connection_timezone(seeded: Any) -> None:
    """Production's database timezone is America/Denver. The serving pool sets
    UTC, so this reads on plain connections in each zone: winter post-market
    minutes (00:00-01:00Z) must keep their session date."""
    frames = []
    for tz in ("UTC", "America/Denver"):
        with psycopg.connect(seeded["url"]) as conn:
            conn.execute(f"SET TIME ZONE '{tz}'")
            assert conn.execute("SHOW TimeZone").fetchone() == (tz,)
            frames.append(
                read_session_bars(
                    conn,
                    "AAA",
                    Granularity.M1,
                    EXT,
                    FULL,
                    FULL,
                    adjusted=False,
                    prints=PrintMode.ELIGIBLE,
                )  # fmt: skip
            )
    utc, denver = frames
    assert set(utc["session_date"]) == {FULL}
    assert (utc.index >= datetime(2026, 1, 15, tzinfo=UTC)).sum() == 60
    pd.testing.assert_frame_equal(utc, denver)


@pytest.mark.parametrize("symbol", ["AAA", "BBB"])
def test_every_minute_placed_as_assign_places_it(
    db: TimescaleMinuteDataDB, seeded: Any, symbol: str
) -> None:
    """At 1m each bar is one minute: its phase and bucket are assign's, and
    every eligible minute inside the phases appears (all-marked ones do not)."""
    df = _read(db, symbol, Granularity.M1, EXT, min(SESSIONS), max(SESSIONS))
    expected = {}
    for ts, sym, o, h, lo, c, _v, mask in seeded["rows"]:
        if sym != symbol:
            continue
        session = seeded["bounds"][ts.astimezone(_ET).date()]
        placed = assign(ts, session, EXT, timedelta(minutes=1))
        if placed is not None and eligible_bar(o, h, lo, c, mask) is not None:
            expected[ts] = (str(placed[0]), placed[1])
    got = {
        ts.to_pydatetime(): (row.phase, ts.to_pydatetime()) for ts, row in df.iterrows()
    }
    assert got == expected


def test_post_market_minute_gets_the_same_factor_both_ways(
    db: TimescaleMinuteDataDB,
) -> None:
    minute = datetime(2026, 1, 15, 0, 30, tzinfo=UTC)  # 19:30 EST on FULL
    one = db.get_minute_data("CCC", minute, minute)
    session = _read(db, "CCC", Granularity.M1, EXT, FULL, adjusted=True)
    raw = _read(db, "CCC", Granularity.M1, EXT, FULL)
    assert session.loc[minute, "close"] == pytest.approx(one["close"].iloc[0])
    assert session.loc[minute, "close"] == pytest.approx(raw.loc[minute, "close"] / 2)


@pytest.mark.parametrize("day", SESSIONS)
@pytest.mark.parametrize("mode", MODES)
def test_counts_equal_the_estimator(
    db: TimescaleMinuteDataDB, seeded: Any, day: date, mode: SessionMode
) -> None:
    for granularity in INTRADAY:
        df = _read(db, "AAA", granularity, mode, day)
        width = timedelta(minutes=GRANULARITY_BAR_MINUTES[granularity])
        assert len(df) == estimate_bars([seeded["bounds"][day]], mode, width)


def test_prints_all_returns_raw_values(db: TimescaleMinuteDataDB, seeded: Any) -> None:
    raw = _read(db, "BBB", Granularity.M1, REG, FULL, prints=PrintMode.ALL)
    rows = {r[0]: r for r in seeded["rows"] if r[1] == "BBB"}
    marked = next(ts for ts in raw.index if rows[ts.to_pydatetime()][7] == 2)
    assert raw.loc[marked, "high"] == pytest.approx(rows[marked.to_pydatetime()][3])
    assert len(raw) == 390


def test_past_the_horizon_raises(db: TimescaleMinuteDataDB) -> None:
    with pytest.raises(OutOfHorizonError):
        _read(db, "AAA", Granularity.H1, REG, date(2028, 12, 1), date(2029, 1, 2))


def test_null_extended_bound_raises(migrated_db: str) -> None:
    with psycopg.connect(migrated_db) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange) VALUES "
            "('AAA.US', 'AAA', 'equity', 'US', 'NYSE', 'Common Stock', 'NYSE')"
        )
        conn.execute(
            "UPDATE trading_sessions SET extended_open_utc = NULL "
            "WHERE calendar_id = 'NYSE' AND session_date = %s",
            (FULL,),
        )
    db = TimescaleMinuteDataDB(migrated_db)
    try:
        with pytest.raises(MissingExtendedBoundsError, match="AAA.*2026-01-14"):
            _read(db, "AAA", Granularity.H1, EXT, FULL)
        assert len(_read(db, "AAA", Granularity.H1, REG, FULL)) == 0
    finally:
        db.close()
