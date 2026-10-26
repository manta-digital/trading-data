"""``get_minute_data(prints=...)`` against marked fixture days (slice 171).

Fixtures go in through ``write_minute_data_bulk``, so they carry the masks the
insert path computes. The known-value test covers every value in design
success criterion 1.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import psycopg
import pytest

from manta_trading.constants import (
    OFF_TAPE_DAY_TZ,
    PRINTS_ALL_1M_ONLY_MESSAGE,
    OffTape,
    PrintMode,
)
from manta_trading.data.quality.off_tape import eligible_bar, mark_day
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "off_tape"
PRICES = ["open", "high", "low", "close"]
KNOWN = [  # (fixture, ET minutes, value): design success criterion 1
    ("SPY_2026-09-08", ["07:35", "07:36", "07:37", "07:38"], 770.19),
    ("SPY_2026-09-08", ["16:01", "16:03", "16:04"], 770.3516),
    ("SPY_2026-09-08", ["16:52", "17:26"], 724.228),
    ("SPY_2026-09-11", ["17:33"], 787.5169),
    ("AVGO_2026-06-03", ["16:15"], 295.4665),
    ("MSFT_2026-08-05", ["16:13"], 384.28),
]
WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))


def _load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(FIXTURES / f"{name}.csv")
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


@pytest.fixture()
def db(migrated_db: str) -> Iterator[TimescaleMinuteDataDB]:
    minute_db = TimescaleMinuteDataDB(migrated_db)
    for name in {fixture for fixture, _, _ in KNOWN}:
        bars = _load(name)
        frame = bars.set_index("time")[[*PRICES, "volume"]]
        assert minute_db.write_minute_data_bulk(bars["symbol"].iloc[0], frame)
    yield minute_db
    minute_db.close()


def _read(
    db: TimescaleMinuteDataDB, symbol: str, prints: PrintMode, adjusted: bool = False
) -> pd.DataFrame:
    return db.get_minute_data(symbol, *WINDOW, adjusted=adjusted, prints=prints)


def _day(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    day = name.split("_")[1]
    et = frame.index.tz_convert(OFF_TAPE_DAY_TZ)
    return frame[et.strftime("%Y-%m-%d") == day]


def test_all_is_the_raw_bars(db: TimescaleMinuteDataDB) -> None:
    bars = _load("SPY_2026-09-08")
    served = _day(_read(db, "SPY", PrintMode.ALL), "SPY_2026-09-08")
    expected = bars.set_index("time")[[*PRICES, "volume"]]
    pd.testing.assert_frame_equal(served, expected, check_names=False, check_freq=False)


def test_eligible_equals_the_python_mirror(db: TimescaleMinuteDataDB) -> None:
    bars = _load("SPY_2026-09-08")
    bars["mask"] = mark_day(bars)
    served = _day(_read(db, "SPY", PrintMode.ELIGIBLE), "SPY_2026-09-08")
    expected = {
        row.time: eligible_bar(row.open, row.high, row.low, row.close, row.mask)
        for row in bars.itertuples()
    }
    expected = {when: value for when, value in expected.items() if value is not None}
    assert list(served.index) == list(expected)
    for when, row in served.iterrows():
        assert tuple(row[PRICES]) == pytest.approx(expected[when])


def test_all_marked_minute_is_absent(db: TimescaleMinuteDataDB) -> None:
    target = _load("SPY_2026-09-08")["time"].iloc[300].to_pydatetime()
    with psycopg.connect(db.conninfo) as conn:
        conn.execute(
            "UPDATE minute_ohlcv SET off_tape = %s WHERE symbol = 'SPY' AND time = %s",
            (int(OffTape.ALL), target),
        )
    assert target in _read(db, "SPY", PrintMode.ALL).index
    assert target not in _read(db, "SPY", PrintMode.ELIGIBLE).index


def test_adjustment_applies_after_the_clamp(db: TimescaleMinuteDataDB) -> None:
    with psycopg.connect(db.conninfo) as conn:
        conn.execute(
            "INSERT INTO splits (symbol, ex_date, ratio_to, ratio_from)"
            " VALUES ('SPY', '2026-12-01', 2, 1)"
        )
    raw = _read(db, "SPY", PrintMode.ELIGIBLE)
    adjusted = _read(db, "SPY", PrintMode.ELIGIBLE, adjusted=True)
    pd.testing.assert_frame_equal(adjusted[PRICES], raw[PRICES] * 0.5)


@pytest.mark.parametrize(("name", "minutes", "value"), KNOWN)
def test_known_values_absent_from_eligible(
    db: TimescaleMinuteDataDB, name: str, minutes: list[str], value: float
) -> None:
    symbol = name.split("_")[0]
    for prints, present in ((PrintMode.ALL, True), (PrintMode.ELIGIBLE, False)):
        frame = _day(_read(db, symbol, prints), name)
        at = frame[
            frame.index.tz_convert(OFF_TAPE_DAY_TZ).strftime("%H:%M").isin(minutes)
        ]
        hits = (at[PRICES] - value).abs().lt(5e-5).to_numpy().any()
        assert hits == present, f"{prints}: {value} at {minutes}"


def test_all_rejects_aggregation(db: TimescaleMinuteDataDB) -> None:
    """The rollups hold eligible values only (slice 173)."""
    with pytest.raises(ValueError, match=PRINTS_ALL_1M_ONLY_MESSAGE):
        db.get_minute_data("SPY", *WINDOW, "5m", prints=PrintMode.ALL)
