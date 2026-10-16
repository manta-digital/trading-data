"""Measure the off-tape detector over one universe week (slice 171).

Runs ``mark_day`` over every symbol's ET days in ``[--week-start,
--week-start + 7 days)`` and reports, per ET day and in total:

- bars and marked bars (mask != 0), and the marked share;
- marked bars inside regular hours (09:30-16:00 ET);
- the prior-close match rate: the fraction of marked values equal to one of
  the symbol's previous 30 official daily closes, against the same fraction
  for unmarked values;
- detector throughput (bars/s, detector time only).

The per-day shares set ``OFF_TAPE_SHARE_FLOOR`` (half the lowest) and
``OFF_TAPE_SHARE_CEILING`` (twice the highest) in ``constants.py``.

Manual, developer-run, read-only. Reads the production URL from
``MT_TIMESCALE_DB_URL``. Usage::

    uv run python scripts/measure_off_tape_share.py --week-start 2026-10-05
"""

from __future__ import annotations

import argparse
import os
import time
from collections import defaultdict
from collections.abc import Iterator
from datetime import date, timedelta

import numpy as np
import pandas as pd
import psycopg

from manta_trading.constants import (
    OFF_TAPE_DAY_TZ,
    OFF_TAPE_MIN_FRACTION,
    OFF_TAPE_SCALE_MULTIPLE,
)
from manta_trading.data.quality.off_tape import PRICE_BITS, mark_day, split_et_days

PRIOR_CLOSES = 30
MATCH_TOLERANCE = 5e-5
RTH_START, RTH_END = 9 * 60 + 30, 16 * 60
BATCH_ROWS = 200_000

_BARS = """
SELECT symbol, time, open::float8, high::float8, low::float8, close::float8
FROM minute_ohlcv
WHERE time >= (%(start)s::date + time '00:00') AT TIME ZONE 'America/New_York'
  AND time <  (%(end)s::date + time '00:00') AT TIME ZONE 'America/New_York'
ORDER BY symbol, time
"""
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


def _symbols(
    conn: psycopg.Connection, start: date, end: date
) -> Iterator[pd.DataFrame]:
    """One frame per symbol, streamed from a server-side cursor."""
    columns = ["symbol", "time", "open", "high", "low", "close"]
    pending: list[tuple[object, ...]] = []
    with conn.cursor(name="off_tape_week") as cursor:
        cursor.execute(_BARS, {"start": start, "end": end})
        while rows := cursor.fetchmany(BATCH_ROWS):
            pending.extend(rows)
            last = pending[-1][0]
            done = [row for row in pending if row[0] != last]
            pending = [row for row in pending if row[0] == last]
            yield from _frames(done, columns)
    yield from _frames(pending, columns)


def _frames(
    rows: list[tuple[object, ...]], columns: list[str]
) -> Iterator[pd.DataFrame]:
    if not rows:
        return
    frame = pd.DataFrame(rows, columns=columns)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    for _, group in frame.groupby("symbol", sort=False):
        yield group.reset_index(drop=True)


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
    conn: psycopg.Connection, start: date, multiple: float, min_fraction: float
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
    for bars in _symbols(conn, start, end):
        symbol = bars["symbol"].iloc[0]
        own = by_symbol.get(symbol, empty)
        for day, frame in split_et_days(bars):
            began = time.perf_counter()
            mask = mark_day(frame, multiple=multiple, min_fraction=min_fraction)
            tally.detector_seconds += time.perf_counter() - began
            _tally_day(tally, day, frame, mask, _prior_closes(own, symbol, day))
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
    parser.add_argument("--week-start", type=date.fromisoformat, required=True)
    parser.add_argument("--multiple", type=float, default=OFF_TAPE_SCALE_MULTIPLE)
    parser.add_argument("--min-fraction", type=float, default=OFF_TAPE_MIN_FRACTION)
    args = parser.parse_args()
    print(f"K=default M={args.multiple} R={args.min_fraction} week={args.week_start}")
    with psycopg.connect(os.environ["MT_TIMESCALE_DB_URL"]) as conn:
        conn.read_only = True
        report(measure(conn, args.week_start, args.multiple, args.min_fraction))


if __name__ == "__main__":
    main()
