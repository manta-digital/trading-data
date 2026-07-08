"""Adjusted-on-read price computation."""

from __future__ import annotations

from datetime import date
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
    start_date, end_date = bar_dates[0], bar_dates[-1]

    if ca_snapshot is None:
        ca_snapshot = _load_snapshot(symbol, start_date, end_date, conn)

    if not ca_snapshot.splits and not ca_snapshot.dividends:
        return df

    k_by_date = {
        d: compute_k_factor(symbol, d, ca_snapshot=ca_snapshot)
        for d in bar_dates
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


def _load_snapshot(
    symbol: str,
    start_date: date,
    end_date: date,
    conn: psycopg.Connection,
) -> CaSnapshot:
    """Fetch splits, dividends, and prev_closes from TimescaleDB for symbol."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT symbol, ex_date, ratio_to, ratio_from FROM splits"
            " WHERE symbol = %s ORDER BY ex_date",
            (symbol,),
        )
        splits = tuple(
            Split(row[0], row[1], Decimal(str(row[2])), Decimal(str(row[3])))
            for row in cur.fetchall()
        )
        cur.execute(
            "SELECT symbol, ex_date, amount, currency FROM dividends"
            " WHERE symbol = %s ORDER BY ex_date",
            (symbol,),
        )
        dividends = tuple(
            Dividend(row[0], row[1], Decimal(str(row[2])), row[3])
            for row in cur.fetchall()
        )

    prev_closes: dict[date, Decimal] = {}
    with conn.cursor() as cur:
        for div in dividends:
            cur.execute(
                "SELECT close FROM daily_ohlcv"
                " WHERE symbol = %s AND time < %s"
                " ORDER BY time DESC LIMIT 1",
                (symbol, div.ex_date),
            )
            row = cur.fetchone()
            if row is not None:
                prev_closes[div.ex_date] = Decimal(str(row[0]))

    return CaSnapshot(
        symbol=symbol,
        splits=splits,
        dividends=dividends,
        prev_closes=prev_closes,
        snapshot_id=compute_snapshot_id(splits, dividends),
    )
