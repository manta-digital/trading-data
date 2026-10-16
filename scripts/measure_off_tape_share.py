"""Measure the off-tape detector over one universe week (slice 171).

Runs the detector over every symbol's ET days in ``[--week-start,
--week-start + 7 days)`` and reports, per ET day and in total:

- bars and marked bars (mask != 0), and the marked share;
- marked bars inside regular hours (09:30-16:00 ET);
- the prior-close match rate: the fraction of marked values equal to one of
  the symbol's previous 30 official daily closes, against the same fraction
  for unmarked values;
- detector throughput (bars/s, detector time only).

The per-day shares set ``OFF_TAPE_SHARE_FLOOR`` (half the lowest) and
``OFF_TAPE_SHARE_CEILING`` (twice the highest) in ``constants.py``.

Manual, developer-run, read-only. Takes the production URL as
``--db-url``. Usage::

    uv run python scripts/measure_off_tape_share.py \\
        --db-url "$MT_TIMESCALE_DB_URL" --week-start 2026-10-05
"""

from __future__ import annotations

import argparse
import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd
import psycopg

from manta_trading.constants import OFF_TAPE_DAY_TZ, OFF_TAPE_REGULAR_MINUTES_ET
from manta_trading.data.quality.off_tape import (
    PRICE_BITS,
    PRODUCTION,
    Calibration,
    mark_symbol_days,
    split_et_days,
)
from manta_trading.data.quality.off_tape_store import et_day_start, iter_symbol_frames

PRIOR_CLOSES = 30
MATCH_TOLERANCE = 5e-5
RTH_START, RTH_END = OFF_TAPE_REGULAR_MINUTES_ET

_CLOSES = """
SELECT symbol, (time AT TIME ZONE 'UTC')::date AS day, close::float8
FROM daily_ohlcv
WHERE time >= %(since)s AND time < %(end)s
ORDER BY symbol, time
"""


class Tally:
    """Running counts per ET day and for the prior-close match."""

    def __init__(self) -> None:
        self.bars: dict[date, int] = defaultdict(int)
        self.marked: dict[date, int] = defaultdict(int)
        self.regular: dict[date, int] = defaultdict(int)
        self.match = {True: [0, 0], False: [0, 0]}  # marked? -> [matches, values]
        self.detector_seconds = 0.0


def _prior_closes(closes: pd.DataFrame, symbol: str, day: date) -> np.ndarray:
    rows = closes[(closes["symbol"] == symbol) & (closes["day"] < day)]
    return rows["close"].to_numpy()[-PRIOR_CLOSES:]


def _tally_day(
    tally: Tally, day: date, bars: pd.DataFrame, mask: pd.Series, prior: np.ndarray
) -> None:
    et = bars["time"].dt.tz_convert(OFF_TAPE_DAY_TZ)
    minute = et.dt.hour * 60 + et.dt.minute
    tally.bars[day] += len(bars)
    tally.marked[day] += int((mask != 0).sum())
    regular = (minute >= RTH_START) & (minute < RTH_END)
    tally.regular[day] += int(((mask != 0) & regular).sum())
    for column, bit in PRICE_BITS:
        values = bars[column].to_numpy()
        hit = (mask.to_numpy() & int(bit)) != 0
        if len(prior):
            equal = np.abs(values[:, None] - prior[None, :]) < MATCH_TOLERANCE
            matches = equal.any(axis=1)
        else:
            matches = np.zeros(len(values), dtype=bool)
        for is_marked in (True, False):
            chosen = hit if is_marked else ~hit
            tally.match[is_marked][0] += int(matches[chosen].sum())
            tally.match[is_marked][1] += int(chosen.sum())


def measure(
    conn: psycopg.Connection[Any], start: date, calibration: Calibration
) -> Tally:
    end = start + timedelta(days=7)
    closes = pd.DataFrame(
        conn.execute(
            _CLOSES, {"since": start - timedelta(days=60), "end": end}
        ).fetchall(),
        columns=["symbol", "day", "close"],
    )
    by_symbol = dict(tuple(closes.groupby("symbol")))
    empty = closes.iloc[0:0]
    tally = Tally()
    for bars in iter_symbol_frames(conn, et_day_start(start), et_day_start(end)):
        symbol = bars["symbol"].iloc[0]
        own = by_symbol.get(symbol, empty)
        began = time.perf_counter()
        masks = mark_symbol_days(bars, calibration)
        tally.detector_seconds += time.perf_counter() - began
        for day, frame in split_et_days(bars):
            prior = _prior_closes(own, symbol, day)
            _tally_day(tally, day, frame, masks.loc[frame.index], prior)
    return tally


def report(tally: Tally) -> None:
    print("day         bars       marked   share     regular")
    for day in sorted(tally.bars):
        share = tally.marked[day] / tally.bars[day]
        print(
            f"{day}  {tally.bars[day]:>9}  {tally.marked[day]:>7}  "
            f"{share:.5f}  {tally.regular[day]:>7}"
        )
    bars, marked = sum(tally.bars.values()), sum(tally.marked.values())
    shares = [tally.marked[d] / tally.bars[d] for d in tally.bars]
    print(
        f"week: bars={bars} marked={marked} share={marked / bars:.5f} "
        f"regular={sum(tally.regular.values())}"
    )
    print(
        f"daily share min={min(shares):.5f} max={max(shares):.5f} -> "
        f"floor={min(shares) / 2:.5f} ceiling={max(shares) * 2:.5f}"
    )
    for is_marked, (hits, values) in tally.match.items():
        label = "marked" if is_marked else "unmarked"
        print(
            f"prior-close match, {label} values: {hits}/{values} = {hits / values:.4f}"
        )
    print(
        f"detector: {bars / tally.detector_seconds:,.0f} bars/s "
        f"({tally.detector_seconds:.1f} s)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--db-url", required=True, help="production URL, read-only")
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--multiple", type=float, default=PRODUCTION.multiple)
    parser.add_argument("--min-fraction", type=float, default=PRODUCTION.min_fraction)
    parser.add_argument("--min-absolute", type=float, default=PRODUCTION.min_absolute)
    args = parser.parse_args()
    calibration = Calibration(
        multiple=args.multiple,
        min_fraction=args.min_fraction,
        min_absolute=args.min_absolute,
    )
    print(f"{calibration} week={args.week_start}")
    with psycopg.connect(args.db_url) as conn:
        conn.read_only = True
        report(measure(conn, args.week_start, calibration))


if __name__ == "__main__":
    main()
