"""Off-tape print detector (slice 171).

A value is off-tape when neither the minutes before it nor the minutes after
it trade anywhere near it. The rule (slice 171 design, "The detector rule"),
per bar of one symbol's ET day, by row:

- ``before``/``after`` = median close of the previous/next ``K`` bars; at the
  day's edges only the available side is used;
- ``scale`` = the larger side's median absolute close-to-close change;
- ``threshold`` = ``max(M * scale, R * close, A)``;
- each of open/high/low/close is marked when it lies above
  ``max(before, after) + threshold`` or below ``min(before, after) - threshold``.

Pure functions, no I/O. A bar's mask depends on its symbol's whole ET day and
nothing else: :func:`mark_day` takes exactly one day, and
:func:`mark_symbol_days` cuts a one-symbol frame at ET day boundaries before
applying the same rule to each day.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from manta_trading.constants import (
    OFF_TAPE_DAY_TZ,
    OFF_TAPE_MIN_ABSOLUTE,
    OFF_TAPE_MIN_FRACTION,
    OFF_TAPE_PRICE_BITS,
    OFF_TAPE_SCALE_MULTIPLE,
    OFF_TAPE_WINDOW_BARS,
)

PRICE_BITS = tuple(OFF_TAPE_PRICE_BITS.items())
"""Each price column and the bit that marks it, in OHLC order."""


@dataclass(frozen=True)
class Calibration:
    """Detector constants; the default is production. Override to calibrate."""

    window: int = OFF_TAPE_WINDOW_BARS
    multiple: float = OFF_TAPE_SCALE_MULTIPLE
    min_fraction: float = OFF_TAPE_MIN_FRACTION
    min_absolute: float = OFF_TAPE_MIN_ABSOLUTE


PRODUCTION = Calibration()


def _day_keys(times: pd.Series) -> np.ndarray:
    """ET calendar day of each timestamp, as ``datetime64[D]``."""
    local = times.dt.tz_convert(OFF_TAPE_DAY_TZ).dt.tz_localize(None)
    return local.to_numpy().astype("datetime64[D]")


def et_days_of(times: Iterable[datetime] | pd.Series) -> set[date]:
    """ET calendar dates of tz-aware timestamps."""
    series = pd.Series(pd.to_datetime(list(times), utc=True))
    return {day.item() for day in np.unique(_day_keys(series))}


def split_et_days(bars: pd.DataFrame) -> Iterator[tuple[date, pd.DataFrame]]:
    """Yield ``(et_date, frame)`` per ET day, oldest first, index preserved.

    ``bars`` holds one symbol with a tz-aware ``time`` column.
    """
    ordered = bars.sort_values("time")
    keys = _day_keys(ordered["time"])
    for day in np.unique(keys):
        yield day.item(), ordered[keys == day]


def _side_median(values: np.ndarray, k: int, *, after: bool) -> np.ndarray:
    """Median of the ``k`` rows strictly before (or after) each row; NaN-aware."""
    series = values[::-1] if after else values
    padded = np.concatenate([np.full(k, np.nan), series[:-1]])
    with warnings.catch_warnings():
        # The first row has no prior rows: an all-NaN window is a NaN median.
        warnings.simplefilter("ignore", RuntimeWarning)
        medians = np.nanmedian(sliding_window_view(padded, k), axis=1)
    return medians[::-1] if after else medians


def _day_masks(prices: np.ndarray, calibration: Calibration) -> np.ndarray:
    """Masks for one day; ``prices`` is an ``(n, 4)`` OHLC array in time order."""
    k = calibration.window
    close = prices[:, 3]
    before = _side_median(close, k, after=False)
    after = _side_median(close, k, after=True)
    lower, upper = np.fmin(before, after), np.fmax(before, after)
    change = np.abs(np.diff(close, prepend=np.nan))
    scale = np.fmax(
        _side_median(change, k, after=False), _side_median(change, k, after=True)
    )
    # np.maximum keeps NaN: a bar with no close-change history marks nothing.
    threshold = np.maximum(
        calibration.multiple * scale,
        np.maximum(calibration.min_fraction * close, calibration.min_absolute),
    )
    with np.errstate(invalid="ignore"):  # NaN envelope (one-bar day) marks nothing
        above = prices > (upper + threshold)[:, None]
        below = prices < (lower - threshold)[:, None]
    bits = np.array([int(bit) for _, bit in PRICE_BITS], dtype=np.int64)
    return ((above | below) * bits).sum(axis=1)


def _prices(bars: pd.DataFrame) -> np.ndarray:
    return bars[[column for column, _ in PRICE_BITS]].to_numpy(dtype=float)


def mark_day(bars: pd.DataFrame, calibration: Calibration = PRODUCTION) -> pd.Series:
    """Off-tape masks (``OffTape`` bits as ints) for one symbol's ET day.

    ``bars`` has a tz-aware ``time`` column ordered ascending and
    ``open/high/low/close``. The result shares ``bars``' index.
    """
    if "symbol" in bars.columns and bars["symbol"].nunique() > 1:
        raise ValueError("mark_day takes one symbol")
    if len(np.unique(_day_keys(bars["time"]))) > 1:
        raise ValueError("mark_day takes one ET day")
    if not bars["time"].is_monotonic_increasing:
        raise ValueError("mark_day takes bars ordered by time")
    return pd.Series(_day_masks(_prices(bars), calibration), index=bars.index)


def mark_symbol_days(
    bars: pd.DataFrame, calibration: Calibration = PRODUCTION
) -> pd.Series:
    """Masks for every bar of a one-symbol frame spanning whole ET days.

    Equal to :func:`mark_day` applied to each ET day; the caller must pass
    whole days. The result shares ``bars``' index.
    """
    if "symbol" in bars.columns and bars["symbol"].nunique() > 1:
        raise ValueError("mark_symbol_days takes one symbol")
    order = np.argsort(bars["time"].to_numpy(), kind="stable")
    keys = _day_keys(bars["time"])[order]
    prices = _prices(bars)[order]
    cuts = np.flatnonzero(keys[1:] != keys[:-1]) + 1
    masks = np.empty(len(bars), dtype=np.int64)
    for start, end in zip(np.r_[0, cuts], np.r_[cuts, len(bars)], strict=True):
        masks[order[start:end]] = _day_masks(prices[start:end], calibration)
    return pd.Series(masks, index=bars.index)


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
