"""Off-tape print detector (slice 171).

A value is off-tape when neither the minutes before it nor the minutes after
it trade anywhere near it. The rule (slice 171 design, "The detector rule"),
per bar of one symbol's ET day, by row:

- ``before``/``after`` = median close of the previous/next ``K`` bars; at the
  day's edges only the available side is used;
- ``scale`` = the larger side's median absolute close-to-close change;
- ``threshold`` = ``max(M * scale, R * close)``;
- each of open/high/low/close is marked when it lies above
  ``max(before, after) + threshold`` or below ``min(before, after) - threshold``.

Pure functions, no I/O. A bar's mask depends on its symbol's whole ET day and
nothing else, so every caller must split frames with :func:`split_et_days`.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from datetime import date, datetime

import numpy as np
import pandas as pd

from manta_trading.constants import (
    OFF_TAPE_DAY_TZ,
    OFF_TAPE_MIN_FRACTION,
    OFF_TAPE_PRICE_BITS,
    OFF_TAPE_SCALE_MULTIPLE,
    OFF_TAPE_WINDOW_BARS,
)

PRICE_BITS = tuple(OFF_TAPE_PRICE_BITS.items())
"""Each price column and the bit that marks it, in OHLC order."""


def _et_dates(times: pd.Series) -> pd.Series:
    return times.dt.tz_convert(OFF_TAPE_DAY_TZ).dt.date


def et_days_of(times: Iterable[datetime] | pd.Series) -> set[date]:
    """ET calendar dates of tz-aware timestamps."""
    series = pd.Series(pd.to_datetime(list(times), utc=True))
    return set(_et_dates(series))


def split_et_days(bars: pd.DataFrame) -> Iterator[tuple[date, pd.DataFrame]]:
    """Yield ``(et_date, frame)`` per ET day, oldest first, index preserved.

    ``bars`` holds one symbol with a tz-aware ``time`` column.
    """
    for day, frame in bars.groupby(_et_dates(bars["time"]), sort=True):
        yield day, frame.sort_values("time")


def _side_median(values: pd.Series, k: int, *, after: bool) -> pd.Series:
    """Median of the ``k`` rows strictly before (or after) each row."""
    if not after:
        return values.shift(1).rolling(k, min_periods=1).median()
    reverse = values.iloc[::-1]
    return reverse.shift(1).rolling(k, min_periods=1).median().iloc[::-1]


def _validate(bars: pd.DataFrame) -> None:
    if "symbol" in bars.columns and bars["symbol"].nunique() > 1:
        raise ValueError("mark_day takes one symbol")
    if _et_dates(bars["time"]).nunique() > 1:
        raise ValueError("mark_day takes one ET day")
    if not bars["time"].is_monotonic_increasing:
        raise ValueError("mark_day takes bars ordered by time")


def mark_day(
    bars: pd.DataFrame,
    *,
    window: int = OFF_TAPE_WINDOW_BARS,
    multiple: float = OFF_TAPE_SCALE_MULTIPLE,
    min_fraction: float = OFF_TAPE_MIN_FRACTION,
) -> pd.Series:
    """Off-tape masks (``OffTape`` bits as ints) for one symbol's ET day.

    ``bars`` has a tz-aware ``time`` column ordered ascending and
    ``open/high/low/close``. The result shares ``bars``' index. The keyword
    arguments exist for calibration only; production uses the constants.
    """
    _validate(bars)
    close = bars["close"].astype(float)
    before = _side_median(close, window, after=False)
    after = _side_median(close, window, after=True)
    sides = pd.concat([before, after], axis=1)
    lower, upper = sides.min(axis=1), sides.max(axis=1)
    change = close.diff().abs()
    scale = pd.concat(
        [
            _side_median(change, window, after=False),
            _side_median(change, window, after=True),
        ],
        axis=1,
    ).max(axis=1)
    threshold = np.maximum(multiple * scale, min_fraction * close)
    mask = pd.Series(0, index=bars.index, dtype="int64")
    for column, bit in PRICE_BITS:
        value = bars[column].astype(float)
        off = (value > upper + threshold) | (value < lower - threshold)
        mask[off] |= int(bit)
    return mask


def eligible_bar(
    open_: float, high: float, low: float, close: float, mask: int | None
) -> tuple[float, float, float, float] | None:
    """Python mirror of ``market.schema.eligible_sql``, for tests and reasoning.

    Clamps each price into the range of the bar's unmarked prices; ``None``
    when every price is marked. ``mask=None`` (not evaluated) reads as clean.
    """
    flags = mask or 0
    prices = (open_, high, low, close)
    eligible = [
        price
        for price, (_, bit) in zip(prices, PRICE_BITS, strict=True)
        if not flags & bit
    ]
    if not eligible:
        return None
    lo, hi = min(eligible), max(eligible)
    clamped = [min(max(price, lo), hi) for price in prices]
    return clamped[0], clamped[1], clamped[2], clamped[3]
