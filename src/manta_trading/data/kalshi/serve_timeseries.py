"""Read-only time-series queries behind the API's Kalshi routes (slice 188).

The ``status.py`` discipline, as in ``serve_catalog``: frozen dataclasses out,
a caller-supplied ``psycopg.Connection``, no client, transport or config (D9).

Every fetch is paired with a count over the identical predicate, and neither
carries a ``LIMIT`` (D4). The guard is the count: a row inserted between the
count and the fetch makes the response one row *over* the ceiling, never one
row short. A ``LIMIT`` would invert that — the client would receive a
truncated answer that looks complete, which is the failure this design
refuses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from psycopg import sql

from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS
from manta_trading.data.kalshi.constants import Surface
from manta_trading.data.kalshi.selection import trades_filter_sql
from manta_trading.data.kalshi.trade_repository import TRADE_COLUMNS
from manta_trading.data.kalshi.trade_status import STATE_QUERY, effective_tape_floor

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    import psycopg

#: The candle value columns, in storage order, taken from the repository's own
#: flattening map rather than retyped. Re-nesting on the way out (Section 5) is
#: driven by the same mapping, so the API row and the Kalshi row keep the same
#: shape.
CANDLE_VALUE_COLUMNS: tuple[str, ...] = tuple(name for name, _ in CANDLE_COLUMNS)

#: The stored trade columns minus ``market_ticker``, which the response carries
#: once at the top level rather than on every row.
TRADE_VALUE_COLUMNS: tuple[str, ...] = tuple(
    column for column, _ in TRADE_COLUMNS if column != "market_ticker"
)


@dataclass(frozen=True)
class MarketContext:
    """What a time-series route needs to know about a market before serving it.

    ``None`` from :func:`market_context` means the ticker is unknown (D6). A
    known market with no candle-state row is *not* an error: it reports
    ``candle_collected=False`` with both timestamps ``None`` (D5).
    """

    ticker: str
    series_category: str | None
    candle_collected: bool
    candle_coverage_from: datetime | None
    candle_complete_through: datetime | None


@dataclass(frozen=True)
class TapeFacts:
    """How far back the trades tape reaches and how current it is (D5)."""

    coverage_from: datetime
    tape_complete_through: datetime


@dataclass(frozen=True)
class CandleRow:
    """One stored candle: its period end plus the fourteen value columns."""

    end_period_ts: datetime
    values: tuple[Decimal | None, ...]


@dataclass(frozen=True)
class TradeRow:
    """One stored trade, without the market ticker the response carries once."""

    values: tuple[Any, ...]


_CONTEXT_QUERY = sql.SQL(
    "SELECT m.ticker, s.category, "
    "cs.market_ticker IS NOT NULL AS collected, "
    "cs.coverage_from_ts, cs.watermark_ts "
    "FROM kalshi.markets m "
    "JOIN kalshi.events e ON e.event_ticker = m.event_ticker "
    "JOIN kalshi.series s ON s.ticker = e.series_ticker "
    "LEFT JOIN kalshi.market_candle_state cs "
    "  ON cs.market_ticker = m.ticker AND cs.period = %(period)s "
    "WHERE m.ticker = %(ticker)s"
)


def market_context(
    conn: psycopg.Connection, ticker: str, *, period: int
) -> MarketContext | None:
    """Seek one market with its category and its candle-collection facts.

    One statement, one round trip: the catalog join supplies the category the
    trades filter is evaluated against, and the ``LEFT JOIN`` supplies the
    candle state when there is one.

    ``period`` is the caller's — routes pass ``COLLECTED_CANDLE_PERIOD``. The
    constant is deliberately not read here: which period the API serves is a
    serving decision, not a property of the query.
    """
    row = conn.execute(_CONTEXT_QUERY, {"ticker": ticker, "period": period}).fetchone()
    if row is None:
        return None
    return MarketContext(
        ticker=row[0],
        series_category=row[1],
        candle_collected=row[2],
        candle_coverage_from=row[3],
        candle_complete_through=row[4],
    )


def tape_facts(conn: psycopg.Connection) -> TapeFacts | None:
    """The trades tape's floor and its newest stored trade.

    ``None`` when the trades ``sync_state`` row is absent — a fresh install
    whose trades phase has never run, not an error (D10). The floor comes from
    :func:`effective_tape_floor`, the same function ``mt data kalshi status``
    reports, so the CLI and the API can never disagree about it (267 D8).
    """
    row = conn.execute(STATE_QUERY, {"surface": Surface.TRADES.value}).fetchone()
    if row is None:
        return None
    _, watermark, live_floor, _ = row
    if watermark is None or live_floor is None:
        msg = (
            "kalshi.sync_state['trades'] exists without a watermark or coverage "
            "floor; the row is written only by the trades phase's init_state"
        )
        raise RuntimeError(msg)
    return TapeFacts(
        coverage_from=effective_tape_floor(conn, live_floor),
        tape_complete_through=watermark,
    )


def tape_filtered(category: str | None, excluded: frozenset[str]) -> bool:
    """Whether this market's trades are excluded from the tape (268 D3).

    The membership test is not re-spelled here. ``trades_filter_sql`` renders
    the SQL the collection phase runs; this evaluates the same comparison over
    the same parameter set, so a change to one cannot leave the other behind.
    An empty configuration renders literal ``FALSE`` there and returns
    ``False`` here.
    """
    selection = trades_filter_sql(excluded)
    if not selection.params:
        return False
    bound = selection.params["trades_excluded_categories"]
    if not isinstance(bound, list):  # pragma: no cover - shape guard
        msg = f"trades_filter_sql bound an unexpected parameter type: {type(bound)}"
        raise TypeError(msg)
    # ``COALESCE(s.category, '')`` in the rendered SQL: an uncategorised series
    # is filtered only if an operator configured the empty string, which the
    # settings parser drops.
    return (category or "") in bound


@dataclass(frozen=True)
class _Window:
    """A rendered time-range predicate and its parameters."""

    clause: sql.Composed
    params: dict[str, Any]


def _window(
    column: str,
    ticker: str,
    start: datetime | None,
    end: datetime | None,
    extra: dict[str, Any] | None = None,
) -> _Window:
    """The shared predicate for both time series: ticker plus optional bounds.

    An omitted bound emits no predicate at all rather than a sentinel date, so
    an unbounded request scans no wider than it must. Bounds bind as
    ``timestamptz`` (D4).
    """
    clauses: list[sql.Composable] = [sql.SQL("market_ticker = %(ticker)s")]
    params: dict[str, Any] = {"ticker": ticker, **(extra or {})}
    if start is not None:
        clauses.append(sql.SQL("{} >= %(start)s").format(sql.Identifier(column)))
        params["start"] = start
    if end is not None:
        clauses.append(sql.SQL("{} <= %(end)s").format(sql.Identifier(column)))
        params["end"] = end
    if extra:
        clauses.append(sql.SQL("period = %(period)s"))
    return _Window(clause=sql.SQL(" AND ").join(clauses), params=params)


def _count(conn: psycopg.Connection, table: str, window: _Window) -> int:
    statement = sql.SQL("SELECT count(*) FROM {table} WHERE {clause}").format(
        table=sql.SQL(table),  # noqa: S608 — module-local literal, never caller input
        clause=window.clause,
    )
    row = conn.execute(statement, window.params).fetchone()
    if row is None:  # pragma: no cover - count(*) always returns one row
        msg = f"count(*) over {table} returned no row"
        raise RuntimeError(msg)
    return int(row[0])


def _candle_window(
    ticker: str, period: int, start: datetime | None, end: datetime | None
) -> _Window:
    return _window("end_period_ts", ticker, start, end, {"period": period})


def count_candles(
    conn: psycopg.Connection,
    ticker: str,
    *,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> int:
    """How many candles the window holds, for the admission guard (D8)."""
    window = _candle_window(ticker, period, start, end)
    return _count(conn, "kalshi.candlesticks", window)


def fetch_candles(
    conn: psycopg.Connection,
    ticker: str,
    *,
    period: int,
    start: datetime | None,
    end: datetime | None,
) -> list[CandleRow]:
    """Every candle in the window, oldest first."""
    window = _candle_window(ticker, period, start, end)
    statement = sql.SQL(
        "SELECT end_period_ts, {cols} FROM kalshi.candlesticks "
        "WHERE {clause} ORDER BY end_period_ts"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(name) for name in CANDLE_VALUE_COLUMNS),
        clause=window.clause,
    )
    return [
        CandleRow(end_period_ts=row[0], values=tuple(row[1:]))
        for row in conn.execute(statement, window.params).fetchall()
    ]


def _trade_window(ticker: str, start: datetime | None, end: datetime | None) -> _Window:
    return _window("created_time", ticker, start, end)


def count_trades(
    conn: psycopg.Connection,
    ticker: str,
    *,
    start: datetime | None,
    end: datetime | None,
) -> int:
    """How many trades the window holds, for the admission guard (D8)."""
    return _count(conn, "kalshi.trades", _trade_window(ticker, start, end))


def fetch_trades(
    conn: psycopg.Connection,
    ticker: str,
    *,
    start: datetime | None,
    end: datetime | None,
) -> list[TradeRow]:
    """Every trade in the window, oldest first.

    Ordered by ``(created_time, trade_id)`` so the order is total: two trades
    can share a microsecond, and a partial order would let successive reads of
    the same window disagree.
    """
    window = _trade_window(ticker, start, end)
    statement = sql.SQL(
        "SELECT {cols} FROM kalshi.trades WHERE {clause} "
        "ORDER BY created_time, trade_id"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(name) for name in TRADE_VALUE_COLUMNS),
        clause=window.clause,
    )
    return [
        TradeRow(values=tuple(row))
        for row in conn.execute(statement, window.params).fetchall()
    ]
