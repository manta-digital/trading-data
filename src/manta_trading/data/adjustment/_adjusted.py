"""Adjusted-on-read price computation."""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal

import pandas as pd
import psycopg

from manta_trading.data.adjustment.k_factor import (
    CaSnapshot,
    Dividend,
    Split,
    compute_k_factor,
    compute_snapshot_id,
)


def adjusted(
    df: pd.DataFrame,
    symbol: str,
    conn: psycopg.Connection,
    *,
    ca_snapshot: CaSnapshot | None = None,
) -> pd.DataFrame:
    """Return df with OHLC adjusted for splits/dividends; volume unchanged.

    Returns same object when empty or no corporate actions exist.
    Raises KeyError if prev_close is missing for a dividend ex-date.
    """
    if df.empty:
        return df

    bar_dates: list[date] = sorted({ts.date() for ts in df.index})

    if ca_snapshot is None:
        ca_snapshot = _load_snapshot(symbol, bar_dates[0], conn)

    if not ca_snapshot.splits and not ca_snapshot.dividends:
        return df

    k_by_date = {
        d: compute_k_factor(symbol, d, ca_snapshot=ca_snapshot) for d in bar_dates
    }

    result = df.copy()
    k_series = pd.Series(
        [k_by_date[ts.date()] for ts in result.index],
        index=result.index,
        dtype="float64",
    )
    for col in ("open", "high", "low", "close"):
        if col in result.columns:
            result[col] = result[col] * k_series

    return result


_DAILY_CLOSES_SQL = """
    SELECT time, close
    FROM daily_ohlcv
    WHERE symbol = %s AND time < %s
    ORDER BY time
"""
"""Every daily close before the latest ex-date, in one statement.

One statement, not one per dividend, because the cost of this lookup is
**planning**, not execution: ``daily_ohlcv`` spans thousands of chunks, so the
planner builds a MergeAppend across all of them every time. Measured on prod
2026-08-03: 1,846 ms planning against 110 ms execution for a single
``ORDER BY time DESC LIMIT 1`` seek. The per-dividend loop this replaces paid
that planning cost once per dividend — ~92 s for AAPL's ~94 dividends, of which
~99 % was re-planning the same query.

The "close on the most recent trading day before X" pick is then a bisect over
the returned series. That is deliberately done in Python rather than as a
``LATERAL`` over the ex-dates: the lateral form re-plans into a generic
multi-row join that was *slower* than the loop for a symbol with many
dividends (measured: cancelled at the 20 s budget for AAPL over a 2014 window).
Row volume here is daily-grain and bounded by a symbol's listed history —
~11 k rows for AAPL since 1980.
"""


def _prev_closes_by_ex_date(
    closes: Sequence[tuple[datetime, object]],
    dividends: Sequence[Dividend],
) -> dict[date, Decimal]:
    """Map each ex-date to the close on the most recent trading day before it.

    ``closes`` must be ordered by time ascending. A dividend whose ex-date
    precedes every available close is omitted, matching the previous
    per-dividend query's behavior of skipping a ``None`` row — ``adjusted``
    then raises ``KeyError`` rather than silently adjusting by a wrong factor.
    """
    times = [row[0] for row in closes]
    result: dict[date, Decimal] = {}
    for div in dividends:
        cutoff = datetime.combine(
            div.ex_date, time.min, tzinfo=times[0].tzinfo if times else None
        )
        index = bisect_left(times, cutoff)
        if index > 0:
            result[div.ex_date] = Decimal(str(closes[index - 1][1]))
    return result


def _load_snapshot(
    symbol: str,
    start_date: date,
    conn: psycopg.Connection,
) -> CaSnapshot:
    """Fetch the corporate actions that can affect bars from ``start_date`` on.

    Only actions with ``ex_date`` **strictly after** a bar's date contribute to
    that bar's k-factor, so an action at or before the earliest bar date in the
    frame cannot change any value in it. Filtering there is what keeps a
    three-month request from loading a symbol's entire dividend history —
    AAPL's is ~94 rows going back to the 1980s.

    There is deliberately **no upper bound**: actions *after* the last bar date
    are exactly what rebases old prices onto the current basis, so excluding
    them would silently return unadjusted prices.

    The resulting ``snapshot_id`` is therefore scoped to the window rather than
    to the symbol. That is safe here: this loader is private to the read path,
    and no caller persists or compares the id (slice 152 dropped the
    ``last_adjusted_ca_snapshot_id`` column that once did).
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, ex_date, ratio_to, ratio_from FROM splits"
            " WHERE symbol = %s AND ex_date > %s ORDER BY ex_date",
            (symbol, start_date),
        )
        splits = tuple(
            Split(row[0], row[1], Decimal(str(row[2])), Decimal(str(row[3])))
            for row in cur.fetchall()
        )
        cur.execute(
            "SELECT symbol, ex_date, amount, currency FROM dividends"
            " WHERE symbol = %s AND ex_date > %s ORDER BY ex_date",
            (symbol, start_date),
        )
        dividends = tuple(
            Dividend(row[0], row[1], Decimal(str(row[2])), row[3])
            for row in cur.fetchall()
        )

    prev_closes: dict[date, Decimal] = {}
    if dividends:
        latest_ex_date = max(div.ex_date for div in dividends)
        with conn.cursor() as cur:
            cur.execute(_DAILY_CLOSES_SQL, (symbol, latest_ex_date))
            closes = cur.fetchall()
        prev_closes = _prev_closes_by_ex_date(closes, dividends)

    return CaSnapshot(
        symbol=symbol,
        splits=splits,
        dividends=dividends,
        prev_closes=prev_closes,
        snapshot_id=compute_snapshot_id(splits, dividends),
    )
