"""Off-tape detector tests against production fixtures (slice 171).

Fixtures are whole ET days cut by ``scripts/cut_off_tape_fixtures.py``; see
``test/fixtures/off_tape/README.md``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from manta_trading.constants import OFF_TAPE_DAY_TZ, OffTape
from manta_trading.data.quality.off_tape import (
    PRICE_BITS,
    Calibration,
    et_days_of,
    mark_day,
    mark_symbol_days,
    split_et_days,
)

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "off_tape"

EARNINGS_DAYS = (
    "MSFT_2026-07-29",
    "AAPL_2026-07-30",
    "SMCI_2026-07-21",
    "PLTR_2026-08-03",
    "TSLA_2026-07-22",
    "MU_2026-06-24",
    "LULU_2026-09-03",
    "AMZN_2026-07-30",
    "CRM_2026-08-26",
    "AMD_2026-08-04",
    "NFLX_2026-07-16",
    "UPST_2026-08-04",
    "META_2026-07-29",
    "AVGO_2026-06-03",
)
#: Regular hours in ET minutes of the day, and the earnings-jump test's terms:
#: the jump is the first bar from 16:00 ET whose close moves more than
#: JUMP_FRACTION from the last regular-hours close; JUMP_ROWS bars each side.
RTH_START, RTH_END = 9 * 60 + 30, 16 * 60
JUMP_FRACTION, JUMP_ROWS = 0.02, 3


def load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(FIXTURES / f"{name}.csv")
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


def marked(name: str) -> pd.DataFrame:
    bars = load(name)
    bars["mask"] = mark_day(bars)
    et = bars["time"].dt.tz_convert(OFF_TAPE_DAY_TZ)
    bars["et"] = et.dt.strftime("%H:%M")
    bars["minute"] = et.dt.hour * 60 + et.dt.minute
    return bars


def assert_value_marked(name: str, minutes: list[str], value: float) -> None:
    """Every o/h/l/c equal to ``value`` in ``minutes`` carries its bit."""
    bars = marked(name)
    window = bars[bars["et"].isin(minutes)]
    hits = 0
    for column, bit in PRICE_BITS:
        rows = window[(window[column] - value).abs() < 5e-5]
        hits += len(rows)
        assert (rows["mask"] & int(bit)).all(), rows[["et", column, "mask"]]
    assert hits, f"{name}: {value} not found in {minutes}"


def test_spy_0908_prior_reference_prints() -> None:
    minutes = ["07:35", "07:36", "07:37", "07:38"]
    assert_value_marked("SPY_2026-09-08", minutes, 770.19)


def test_spy_0908_after_hours_highs_and_lows() -> None:
    assert_value_marked("SPY_2026-09-08", ["16:01", "16:03", "16:04"], 770.3516)
    assert_value_marked("SPY_2026-09-08", ["16:52", "17:26"], 724.228)


@pytest.mark.parametrize(
    ("name", "minute", "value"),
    [
        ("SPY_2026-09-11", "17:33", 787.5169),
        ("AVGO_2026-06-03", "16:15", 295.4665),
        ("MSFT_2026-08-05", "16:13", 384.28),
    ],
)
def test_single_known_values(name: str, minute: str, value: float) -> None:
    assert_value_marked(name, [minute], value)


@pytest.mark.parametrize("name", EARNINGS_DAYS)
def test_earnings_jump_not_marked(name: str) -> None:
    bars = marked(name)
    regular = bars[(bars["minute"] >= RTH_START) & (bars["minute"] < RTH_END)]
    last_close = regular["close"].iloc[-1]
    after = bars[bars["minute"] >= RTH_END]
    jumps = after.index[(after["close"] / last_close - 1).abs() > JUMP_FRACTION]
    assert len(jumps), f"{name}: no earnings jump found"
    near = bars.loc[jumps[0] - JUMP_ROWS : jumps[0] + JUMP_ROWS]
    flagged = near[near["mask"] != 0]
    if name == "AVGO_2026-06-03":
        # The one known exception: a low 38% below the market.
        assert flagged[["et", "mask"]].values.tolist() == [["16:15", OffTape.LOW]]
        return
    assert flagged.empty, flagged[["et", "open", "high", "low", "close", "mask"]]


def test_quiet_day_has_no_regular_hours_marks() -> None:
    bars = marked("SPY_2026-09-22")
    regular = bars[(bars["minute"] >= RTH_START) & (bars["minute"] < RTH_END)]
    assert (regular["mask"] == 0).all()


def test_dense_session_still_marks() -> None:
    bars = marked("MSFT_2026-06-01")
    dense = bars[(bars["et"] >= "16:40") & (bars["et"] <= "17:12")]
    assert (dense["mask"] != 0).sum() > 0


def test_winter_tail_belongs_to_previous_et_day() -> None:
    bars = load("SPY_2026-01-07")
    tail = bars[bars["time"] >= pd.Timestamp("2026-01-08", tz="UTC")]
    assert len(tail) > 0
    days = list(split_et_days(bars))
    assert [day.isoformat() for day, _ in days] == ["2026-01-07"]
    assert len(days[0][1]) == len(bars)
    assert {d.isoformat() for d in et_days_of(tail["time"])} == {"2026-01-07"}


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.csv")), ids=lambda p: p.stem)
def test_marked_open_or_close_implies_marked_high_or_low(path: Path) -> None:
    mask = marked(path.stem)["mask"]
    body = (mask & int(OffTape.OPEN | OffTape.CLOSE)) != 0
    wick = (mask & int(OffTape.HIGH | OffTape.LOW)) != 0
    assert (wick[body]).all()


def test_rejects_two_symbols() -> None:
    bars = load("SPY_2026-09-22")
    bars.loc[bars.index[-1], "symbol"] = "QQQ"
    with pytest.raises(ValueError, match="one symbol"):
        mark_day(bars)


def test_rejects_two_et_days() -> None:
    bars = pd.concat([load("SPY_2026-09-22"), load("SPY_2026-09-08")])
    bars = bars.sort_values("time").reset_index(drop=True)
    with pytest.raises(ValueError, match="one ET day"):
        mark_day(bars)


def test_mask_index_matches_input() -> None:
    bars = load("SPY_2026-09-08").set_index(pd.RangeIndex(100, 1059))
    assert mark_day(bars).index.equals(bars.index)


def test_symbol_days_equal_per_day_marks() -> None:
    days = ["SPY_2026-01-07", "SPY_2026-09-08", "SPY_2026-09-11", "SPY_2026-09-22"]
    bars = pd.concat([load(name) for name in days], ignore_index=True)
    bars = bars.sample(frac=1, random_state=3)
    per_day = pd.concat([mark_day(frame) for _, frame in split_et_days(bars)])
    assert mark_symbol_days(bars).equals(per_day.reindex(bars.index))


def test_threshold_floor_spares_tick_bound_dip() -> None:
    """ARRY 2026-10-08 15:34: a one-tick dip on a $3.75 stock is not marked."""
    bars = marked("ARRY_2026-10-08")
    dip = bars["et"] == "15:34"
    assert bars.loc[dip, "mask"].tolist() == [0]
    unfloored = mark_day(load("ARRY_2026-10-08"), Calibration(min_absolute=0.0))
    assert unfloored[dip].tolist() == [int(OffTape.LOW)]
