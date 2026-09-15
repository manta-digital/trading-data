"""Read-only catalog queries behind the API's Kalshi catalog routes (188).

Synchronous psycopg over a caller-supplied connection — the ``status.py``
discipline: frozen dataclasses out, no client, no transport, no config
import (D9). Routes own HTTP; this module owns SQL.

Every list is paired with a count over the identical ``WHERE`` clause, so the
route can admit or refuse a request before it reads any rows (D8). There is no
``LIMIT`` anywhere in this module: a response is either complete or refused
(D4).
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from psycopg import sql

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    import psycopg


@dataclass(frozen=True)
class CategoryCount:
    """One distinct ``kalshi.series.category`` and how many series carry it."""

    category: str | None
    series_count: int


@dataclass(frozen=True)
class SeriesRow:
    """A ``kalshi.series`` row in DDL order, without ``raw`` (D2)."""

    ticker: str
    frequency: str | None
    title: str | None
    category: str | None
    tags: Any
    settlement_sources: Any
    fee_type: str | None
    fee_multiplier: Decimal | None
    contract_url: str | None
    contract_terms_url: str | None
    product_metadata: Any
    last_updated_ts: datetime | None
    first_seen_at: datetime
    last_synced_at: datetime


@dataclass(frozen=True)
class EventRow:
    """A ``kalshi.events`` row in DDL order, without ``raw`` (D2)."""

    event_ticker: str
    series_ticker: str
    title: str | None
    sub_title: str | None
    category: str | None
    mutually_exclusive: bool | None
    strike_date: datetime | None
    strike_period: str | None
    collateral_return_type: str | None
    available_on_brokers: bool | None
    settlement_sources: Any
    product_metadata: Any
    last_updated_ts: datetime | None
    first_seen_at: datetime
    last_synced_at: datetime


@dataclass(frozen=True)
class MarketRow:
    """A ``kalshi.markets`` row in DDL order, without ``raw`` (D2).

    Flat — one field per column. The lifecycle / settlement / economics
    grouping the API publishes is a response-model concern, not the reader's.
    """

    ticker: str
    event_ticker: str
    market_type: str | None
    status: str
    title: str | None
    subtitle: str | None
    yes_sub_title: str | None
    no_sub_title: str | None
    rules_primary: str | None
    rules_secondary: str | None
    created_time: datetime | None
    open_time: datetime | None
    close_time: datetime
    expiration_time: datetime | None
    expected_expiration_time: datetime | None
    latest_expiration_time: datetime | None
    updated_time: datetime | None
    result: str | None
    expiration_value: str | None
    can_close_early: bool | None
    settlement_ts: datetime | None
    settlement_value_dollars: Decimal | None
    notional_value_dollars: Decimal | None
    last_price_dollars: Decimal | None
    previous_price_dollars: Decimal | None
    yes_bid_dollars: Decimal | None
    yes_ask_dollars: Decimal | None
    no_bid_dollars: Decimal | None
    no_ask_dollars: Decimal | None
    liquidity_dollars: Decimal | None
    volume_fp: Decimal | None
    volume_24h_fp: Decimal | None
    open_interest_fp: Decimal | None
    yes_bid_size_fp: Decimal | None
    yes_ask_size_fp: Decimal | None
    previous_yes_bid_dollars: Decimal | None
    previous_yes_ask_dollars: Decimal | None
    strike_type: str | None
    price_level_structure: str | None
    is_provisional: bool | None
    mve_collection_ticker: str | None
    first_seen_at: datetime
    last_synced_at: datetime


def _columns(record: type) -> tuple[str, ...]:
    """The record's column names, which are its field names in DDL order.

    Taking the projection from the dataclass is what keeps a column named
    once per record type: the ``SELECT`` list and the row mapper are built
    from the same tuple, so they cannot fall out of step.
    """
    return tuple(f.name for f in fields(record))


_SERIES_SELECT = _columns(SeriesRow)
_EVENT_SELECT = _columns(EventRow)
_MARKET_SELECT = _columns(MarketRow)


def _projection(columns: Sequence[str]) -> sql.Composed:
    return sql.SQL(", ").join(sql.Identifier(name) for name in columns)


_CATEGORIES_SQL = sql.SQL(
    "SELECT category, count(*) FROM kalshi.series GROUP BY category ORDER BY category"
)


def fetch_categories(conn: psycopg.Connection) -> list[CategoryCount]:
    """Every distinct series category with the number of series carrying it.

    ``category`` is free text Kalshi assigns — it is not an enum anywhere in
    this codebase — so unlike ``status`` its accepted values cannot be derived
    from a type. This reader is the only way a client can learn what the
    series list's ``category=`` filter accepts, which is why it exists (D2).

    No count guard and no filter parameters: the result is bounded by the
    number of distinct categories (20 in production on 2026-09-13) and the
    aggregate measured 15 ms over the whole table. Ordering by ``category``
    keeps the response stable between calls.
    """
    rows = conn.execute(_CATEGORIES_SQL).fetchall()
    return [CategoryCount(category=row[0], series_count=row[1]) for row in rows]


def _seek[Row](
    conn: psycopg.Connection,
    record: Callable[..., Row],
    columns: Sequence[str],
    table: str,
    key_column: str,
    key: str,
) -> Row | None:
    """One primary-key ``SELECT``; ``None`` when the row is absent.

    ``None`` means "no such row" and nothing else. No exception is caught
    here: a ``psycopg.Error`` propagates so a failed seek can never be
    reported to the client as a 404 (D10).
    """
    statement = sql.SQL("SELECT {cols} FROM {table} WHERE {key} = %s").format(
        cols=_projection(columns),
        table=sql.SQL(table),  # noqa: S608 — module-local literal, never caller input
        key=sql.Identifier(key_column),
    )
    row = conn.execute(statement, (key,)).fetchone()
    return None if row is None else record(*row)


def fetch_series(conn: psycopg.Connection, ticker: str) -> SeriesRow | None:
    """Seek one series by its ticker."""
    return _seek(conn, SeriesRow, _SERIES_SELECT, "kalshi.series", "ticker", ticker)


def fetch_event(conn: psycopg.Connection, event_ticker: str) -> EventRow | None:
    """Seek one event by its event ticker."""
    return _seek(
        conn, EventRow, _EVENT_SELECT, "kalshi.events", "event_ticker", event_ticker
    )


def fetch_market(conn: psycopg.Connection, ticker: str) -> MarketRow | None:
    """Seek one market by its ticker."""
    return _seek(conn, MarketRow, _MARKET_SELECT, "kalshi.markets", "ticker", ticker)


@dataclass(frozen=True)
class _Predicate:
    """A rendered ``WHERE`` clause and its parameters.

    Count and fetch are built from the same ``_Predicate`` per resource, so
    the admission guard and the read can never run over different rows.
    """

    clause: sql.Composed
    params: tuple[Any, ...]


def _predicate(clauses: Sequence[sql.Composable], params: Sequence[Any]) -> _Predicate:
    return _Predicate(
        clause=sql.SQL(" AND ").join(clauses),
        params=tuple(params),
    )


def _count(conn: psycopg.Connection, table: str, where: _Predicate) -> int:
    statement = sql.SQL("SELECT count(*) FROM {table} WHERE {clause}").format(
        table=sql.SQL(table),  # noqa: S608 — module-local literal, never caller input
        clause=where.clause,
    )
    row = conn.execute(statement, where.params).fetchone()
    if row is None:  # pragma: no cover - count(*) always returns one row
        msg = f"count(*) over {table} returned no row"
        raise RuntimeError(msg)
    return int(row[0])


def _list[Row](
    conn: psycopg.Connection,
    record: Callable[..., Row],
    columns: Sequence[str],
    table: str,
    where: _Predicate,
    order_by: str,
) -> list[Row]:
    statement = sql.SQL(
        "SELECT {cols} FROM {table} WHERE {clause} ORDER BY {order}"
    ).format(
        cols=_projection(columns),
        table=sql.SQL(table),  # noqa: S608 — module-local literal, never caller input
        clause=where.clause,
        order=sql.Identifier(order_by),
    )
    return [record(*row) for row in conn.execute(statement, where.params).fetchall()]


def _series_where(category: str | None, search: str | None) -> _Predicate:
    """The series list's filter: exact ``category``, ticker-prefix ``search``."""
    clauses: list[sql.Composable] = [sql.SQL("TRUE")]
    params: list[Any] = []
    if category is not None:
        clauses.append(sql.SQL("category = %s"))
        params.append(category)
    if search is not None:
        # The ``symbols.py::_LIST_FILTERED_SQL`` spelling: a prefix search,
        # not a substring one.
        clauses.append(sql.SQL("ticker ILIKE %s"))
        params.append(search + "%")
    return _predicate(clauses, params)


def count_series(
    conn: psycopg.Connection, *, category: str | None, search: str | None
) -> int:
    """How many series match the filter, for the admission guard (D8)."""
    return _count(conn, "kalshi.series", _series_where(category, search))


def fetch_series_list(
    conn: psycopg.Connection, *, category: str | None, search: str | None
) -> list[SeriesRow]:
    """Every series matching the filter, ordered by ticker."""
    return _list(
        conn,
        SeriesRow,
        _SERIES_SELECT,
        "kalshi.series",
        _series_where(category, search),
        "ticker",
    )


def _events_where(
    series_ticker: str, strike_from: datetime | None, strike_to: datetime | None
) -> _Predicate:
    """The events list's filter: scoped to one series, inclusive strike bounds."""
    clauses: list[sql.Composable] = [sql.SQL("series_ticker = %s")]
    params: list[Any] = [series_ticker]
    # ``events.strike_date`` is TIMESTAMPTZ, so the bounds bind as aware
    # datetimes rather than dates — D4's binding rule applies to every
    # hypertable-adjacent predicate.
    if strike_from is not None:
        clauses.append(sql.SQL("strike_date >= %s"))
        params.append(strike_from)
    if strike_to is not None:
        clauses.append(sql.SQL("strike_date <= %s"))
        params.append(strike_to)
    return _predicate(clauses, params)


def count_events(
    conn: psycopg.Connection,
    series_ticker: str,
    *,
    strike_from: datetime | None,
    strike_to: datetime | None,
) -> int:
    """How many events match the filter, for the admission guard (D8)."""
    return _count(
        conn, "kalshi.events", _events_where(series_ticker, strike_from, strike_to)
    )


def fetch_events(
    conn: psycopg.Connection,
    series_ticker: str,
    *,
    strike_from: datetime | None,
    strike_to: datetime | None,
) -> list[EventRow]:
    """Every event of the series matching the filter, ordered by event ticker."""
    return _list(
        conn,
        EventRow,
        _EVENT_SELECT,
        "kalshi.events",
        _events_where(series_ticker, strike_from, strike_to),
        "event_ticker",
    )


def _markets_where(event_ticker: str, statuses: Sequence[str] | None) -> _Predicate:
    """The markets list's filter: scoped to one event, optional status set."""
    clauses: list[sql.Composable] = [sql.SQL("event_ticker = %s")]
    params: list[Any] = [event_ticker]
    if statuses is not None:
        clauses.append(sql.SQL("status = ANY(%s)"))
        params.append(list(statuses))
    return _predicate(clauses, params)


def count_markets(
    conn: psycopg.Connection, event_ticker: str, *, statuses: Sequence[str] | None
) -> int:
    """How many markets match the filter, for the admission guard (D8)."""
    return _count(conn, "kalshi.markets", _markets_where(event_ticker, statuses))


def fetch_markets(
    conn: psycopg.Connection, event_ticker: str, *, statuses: Sequence[str] | None
) -> list[MarketRow]:
    """Every market of the event matching the filter, ordered by ticker."""
    return _list(
        conn,
        MarketRow,
        _MARKET_SELECT,
        "kalshi.markets",
        _markets_where(event_ticker, statuses),
        "ticker",
    )
