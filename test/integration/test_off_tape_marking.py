"""Both minute insert paths mark bars in the insert transaction (slice 171).

Real fixtures loaded through ``_insert_minute_bars`` (acquisition) and
``TimescaleMinuteDataDB.write_minute_data_bulk`` into a migrated throwaway
database. The agreement test is the slice's key invariant: a day's masks do
not depend on how its bars arrived.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from manta_trading.data.acquisition.daemon.minute import _insert_minute_bars
from manta_trading.data.quality import off_tape_store
from manta_trading.data.quality.off_tape import et_days_of, mark_day
from manta_trading.data.quality.off_tape_store import mark_days
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "off_tape"
_RAW = (
    "SELECT md5(string_agg(concat_ws(',', time, open, high, low, close, volume),"
    " '|' ORDER BY time)) FROM minute_ohlcv WHERE symbol = %s"
)

Writer = Callable[[str, pd.DataFrame], None]


def load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(
        FIXTURES / f"{name}.csv",
        dtype={"open": str, "high": str, "low": str, "close": str},
    )
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


@pytest.fixture()
def db(migrated_db: str) -> Iterator[TimescaleMinuteDataDB]:
    minute_db = TimescaleMinuteDataDB(migrated_db)
    yield minute_db
    minute_db.close()


def _acquisition(db: TimescaleMinuteDataDB) -> Writer:
    def write(symbol: str, frame: pd.DataFrame) -> None:
        bars = [
            {
                "timestamp": int(row.time.timestamp()),
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": int(row.volume),
            }
            for row in frame.itertuples()
        ]
        with psycopg.connect(db.conninfo) as conn:
            _insert_minute_bars(conn, symbol, bars)

    return write


def _bulk(db: TimescaleMinuteDataDB) -> Writer:
    def write(symbol: str, frame: pd.DataFrame) -> None:
        data = frame.set_index("time")[["open", "high", "low", "close", "volume"]]
        assert db.write_minute_data_bulk(
            symbol,
            data.astype({"open": float, "high": float, "low": float, "close": float}),
        )

    return write


PATHS = {"acquisition": _acquisition, "bulk": _bulk}


def stored(db: TimescaleMinuteDataDB, symbol: str) -> pd.Series:
    with psycopg.connect(db.conninfo) as conn:
        rows = conn.execute(
            "SELECT time, off_tape FROM minute_ohlcv WHERE symbol = %s ORDER BY time",
            (symbol,),
        ).fetchall()
    frame = pd.DataFrame(rows, columns=["time", "off_tape"])
    return frame.set_index(pd.to_datetime(frame["time"], utc=True))["off_tape"]


def expected(bars: pd.DataFrame) -> pd.Series:
    return mark_day(bars).set_axis(bars["time"].to_numpy())


def raw_checksum(db: TimescaleMinuteDataDB, symbol: str) -> str:
    with psycopg.connect(db.conninfo) as conn:
        row = conn.execute(_RAW, (symbol,)).fetchone()
    assert row is not None
    return str(row[0])


@pytest.mark.parametrize("path", list(PATHS))
def test_inserted_rows_are_marked(db: TimescaleMinuteDataDB, path: str) -> None:
    bars = load("SPY_2026-09-08")
    PATHS[path](db)("SPY", bars)
    marks = stored(db, "SPY")
    assert marks.notna().all()
    assert len(marks) == len(bars)
    assert (marks.astype(int).to_numpy() == expected(bars).to_numpy()).all()
    assert (marks != 0).any()


@pytest.mark.parametrize("path", list(PATHS))
def test_raw_values_unchanged(db: TimescaleMinuteDataDB, path: str) -> None:
    bars = load("SPY_2026-09-08")
    write = PATHS[path](db)
    write("SPY", bars)
    first = raw_checksum(db, "SPY")
    write("SPY", bars.iloc[100:400])  # overlapping re-insert: conflicts skipped
    assert raw_checksum(db, "SPY") == first
    with psycopg.connect(db.conninfo) as conn:
        rows = conn.execute(
            "SELECT open, high, low, close, volume FROM minute_ohlcv"
            " WHERE symbol = 'SPY' ORDER BY time"
        ).fetchall()
    want = [
        (Decimal(r.open), Decimal(r.high), Decimal(r.low), Decimal(r.close), r.volume)
        for r in bars.itertuples()
    ]
    assert rows == want


@pytest.mark.parametrize("path", list(PATHS))
def test_two_frames_one_frame_and_sweep_agree(
    db: TimescaleMinuteDataDB, path: str
) -> None:
    """Winter day split at 00:00 UTC == whole day == NULL then mark_days."""
    bars = load("SPY_2026-01-07")
    midnight = pd.Timestamp("2026-01-08", tz="UTC")
    write = PATHS[path](db)
    write("SPY", bars[bars["time"] < midnight])
    write("SPY", bars[bars["time"] >= midnight])
    two_frames = stored(db, "SPY")
    # The same day inserted whole under another symbol name.
    write("SPYX", bars.assign(symbol="SPYX"))
    one_frame = stored(db, "SPYX")
    with psycopg.connect(db.conninfo) as conn:
        conn.execute("UPDATE minute_ohlcv SET off_tape = NULL WHERE symbol = 'SPY'")
        mark_days(conn, "SPY", et_days_of(bars["time"]))
        conn.commit()
    swept = stored(db, "SPY")
    assert two_frames.equals(one_frame)
    assert swept.equals(two_frames)
    assert (two_frames.astype(int).to_numpy() == expected(bars).to_numpy()).all()


@pytest.mark.parametrize("path", list(PATHS))
def test_later_frame_remarks_whole_day(db: TimescaleMinuteDataDB, path: str) -> None:
    bars = load("SPY_2026-09-08")
    write = PATHS[path](db)
    write("SPY", bars.iloc[:-200])
    write("SPY", bars.iloc[-200:])
    marks = stored(db, "SPY")
    assert (marks.astype(int).to_numpy() == expected(bars).to_numpy()).all()


def _boom(*_args: object, **_kwargs: object) -> pd.Series:
    raise RuntimeError("detector exploded")


def test_detector_failure_rolls_back_acquisition_insert(
    db: TimescaleMinuteDataDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(off_tape_store, "mark_symbol_days", _boom)
    with pytest.raises(RuntimeError, match="detector exploded"):
        _acquisition(db)("SPY", load("SPY_2026-09-08"))
    assert stored(db, "SPY").empty


def test_detector_failure_is_a_failed_bulk_write(
    db: TimescaleMinuteDataDB, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(off_tape_store, "mark_symbol_days", _boom)
    data = load("SPY_2026-09-08").set_index("time")
    data = data[["open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float}
    )
    assert db.write_minute_data_bulk("SPY", data) is False
    assert stored(db, "SPY").empty
