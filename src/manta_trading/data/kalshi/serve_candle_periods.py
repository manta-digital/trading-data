"""Coarse candles (1h, 1d) derived from the stored minute candles (issue 61).

Only the minute period is collected (``COLLECTED_CANDLE_PERIOD``); a coarser
candle is the minute rows grouped by period end and aggregated here, on read.
Same discipline as ``serve_timeseries``: a caller-supplied connection, frozen
rows out, no ``LIMIT``, and a count over the identical predicate so the
admission ceiling counts the rows *served* (periods), not the minutes read.

Periods are UTC-aligned: ``date_bin`` is anchored at the epoch, a multiple of
every ``CandlePeriod``, so hours start on the hour and days at 00:00 UTC.
A minute candle ending at ``t`` covers ``[t - 1m, t)`` and belongs to the period
containing its *open*, so the 23:59-00:00 minute is in the day that ends at
the next midnight.

Aggregation per column, keyed by the field name ``CANDLE_COLUMNS`` already
carries (an unmapped new column fails at import, not silently):

* ``open`` / ``previous`` — the first minute's; ``close`` / open interest — the
  last minute's; ``high`` / ``low`` — max / min; volume — the sum.
* Traded price (``price_*`` OHLC) skips minutes with no trade, because those
  carry nulls; bid and ask use the first and last minute as stored.
* ``mean`` — volume-weighted mean of the minute means. Exact because Kalshi's
  minute mean is itself the volume-weighted price: verified against the trades
  tape on production 2026-11-06 (876 of 876 multi-price minutes matched, none
  matched a simple average instead).
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from psycopg import sql

from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS
from manta_trading.data.kalshi.serve_timeseries import (
    CandleRow,
    candle_period_ends,
)

if TYPE_CHECKING:
    from datetime import datetime

    import psycopg

_MINUTE = timedelta(minutes=1)
#: Decimal places kept on the weighted mean. A weighted mean is a ratio, so
#: some rounding is unavoidable; this is far finer than any stored price.
_MEAN_SCALE = 8
_EPOCH = "TIMESTAMPTZ '1970-01-01 00:00:00+00'"

_FIRST = "first"
_LAST = "last"


def _ordered(column: str, which: str, *, skip_null: bool) -> str:
    """First or last value of ``column`` in minute order."""
    nulls = f" FILTER (WHERE {column} IS NOT NULL)" if skip_null else ""
    direction = "" if which == _FIRST else " DESC"
    return f"(array_agg({column} ORDER BY end_period_ts{direction}){nulls})[1]"


def _aggregate(column: str, obj: str | None, field: str) -> str:
    """The aggregate expression for one stored candle column."""
    traded = obj == "price"
    match field:
        case "open_dollars":
            return _ordered(column, _FIRST, skip_null=traded)
        case "close_dollars":
            return _ordered(column, _LAST, skip_null=traded)
        case "previous_dollars":
            return _ordered(column, _FIRST, skip_null=False)
        case "high_dollars":
            return f"max({column})"
        case "low_dollars":
            return f"min({column})"
        case "mean_dollars":
            weight = "volume_fp"
            return (
                f"round(sum({column} * {weight}) / "
                f"NULLIF(sum({weight}) FILTER (WHERE {column} IS NOT NULL), 0), "
                f"{_MEAN_SCALE})"
            )
        case "volume_fp":
            return f"sum({column})"
        case "open_interest_fp":
            return _ordered(column, _LAST, skip_null=False)
        case _:
            msg = f"no coarse-candle aggregation for candle column {column!r}"
            raise KeyError(msg)


def _select_list() -> str:
    """The aggregate select list, in ``CANDLE_COLUMNS`` (storage) order."""
    parts = []
    for column, path in CANDLE_COLUMNS:
        obj = path[0] if len(path) > 1 else None
        parts.append(f"{_aggregate(column, obj, path[-1])} AS {column}")
    return ", ".join(parts)


# Built once at import, so an unmapped column fails the import.
_SELECT_LIST = _select_list()

_MINUTES = (
    "SELECT date_bin(%(span)s, end_period_ts - %(minute)s, "
    f"{_EPOCH}) + %(span)s AS bucket_end, * "
    "FROM kalshi.candlesticks "
    "WHERE market_ticker = %(ticker)s AND period = %(stored)s{minute_bounds}"
)


def _statement(body: str, *, start: bool, end: bool) -> sql.Composed:
    """Wrap ``body`` over the minute rows read for the window.

    The minute prefilter is deliberately loose (it only bounds the scan); the
    exact window test is the ``HAVING`` on the period end.
    """
    bounds = ""
    if start:
        bounds += " AND end_period_ts >= %(minute_from)s"
    if end:
        bounds += " AND end_period_ts < %(minute_to)s"
    having = ""
    if start:
        having += " HAVING bucket_end >= %(first_end)s"
    if end:
        having += f"{' AND' if start else ' HAVING'} bucket_end < %(end_bound)s"
    minutes = _MINUTES.format(minute_bounds=bounds)
    return sql.SQL(body).format(  # type: ignore[arg-type]
        minutes=sql.SQL(minutes), having=sql.SQL(having)
    )


def _params(
    ticker: str,
    stored: int,
    served: int,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, object]:
    span = timedelta(minutes=served)
    first_end, end_bound = candle_period_ends(served, start, end)
    return {
        "ticker": ticker,
        "stored": stored,
        "span": span,
        "minute": _MINUTE,
        "first_end": first_end,
        "end_bound": end_bound,
        "minute_from": None if start is None else start + _MINUTE,
        "minute_to": None if end is None else end + span + _MINUTE,
    }


def count_period_candles(
    conn: psycopg.Connection,
    ticker: str,
    *,
    stored_period: int,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> int:
    """How many ``period`` candles the window ``[start, end)`` holds."""
    statement = _statement(
        "SELECT count(*) FROM (SELECT bucket_end FROM ({minutes}) AS minute "
        "GROUP BY bucket_end{having}) AS served",
        start=start is not None,
        end=end is not None,
    )
    row = conn.execute(
        statement, _params(ticker, stored_period, period, start, end)
    ).fetchone()
    if row is None:  # pragma: no cover - count(*) always returns one row
        msg = "count(*) over derived candles returned no row"
        raise RuntimeError(msg)
    return int(row[0])


def fetch_period_candles(
    conn: psycopg.Connection,
    ticker: str,
    *,
    stored_period: int,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> list[CandleRow]:
    """Every ``period`` candle opening in ``[start, end)``, oldest first."""
    statement = _statement(
        f"SELECT bucket_end, {_SELECT_LIST} FROM ({{minutes}}) AS minute "
        "GROUP BY bucket_end{having} ORDER BY bucket_end",
        start=start is not None,
        end=end is not None,
    )
    return [
        CandleRow(end_period_ts=row[0], values=tuple(row[1:]))
        for row in conn.execute(
            statement, _params(ticker, stored_period, period, start, end)
        ).fetchall()
    ]
