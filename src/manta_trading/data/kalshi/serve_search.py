"""Ranked title search over the Kalshi catalog (#63).

One reader behind ``GET /api/v1/kalshi/search``: three statements, one per
level of the hierarchy, on one caller-supplied connection (the
``serve_catalog.py`` discipline: frozen dataclasses out, SQL here, no HTTP).

A row matches when its stemmed full-text document contains every query word
(``websearch_to_tsquery``), or the query is a typo-close trigram match of its
title, or (series only) the query is a prefix of its ticker. Ranking is: ticker
prefix, then full-text match over typo match, then text rank plus similarity,
then a level-specific tiebreak, because templated titles ("Bitcoin price on
...") tie on text rank and the tiebreak is what a user sees. Events rank the
nearest upcoming strike first; markets, the soonest close.

Unlike every other catalog reader this one has a ``LIMIT``: a ranked search is
a top-N by definition, not a complete listing (the row ceiling does not apply).
Markets are searched only while open (``OPEN_MARKET_PREDICATE``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime  # noqa: TC003 — dataclass annotations
from typing import TYPE_CHECKING

from manta_trading.data.kalshi.constants import SEARCH_TYPO_THRESHOLD
from manta_trading.data.kalshi.search_expr import (
    EVENT_DOC,
    MARKET_DOC,
    OPEN_MARKET_PREDICATE,
    SERIES_DOC,
    TS_CONFIG,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    import psycopg


@dataclass(frozen=True)
class SeriesHit:
    ticker: str
    title: str | None
    category: str | None


@dataclass(frozen=True)
class EventHit:
    event_ticker: str
    series_ticker: str
    title: str | None
    sub_title: str | None
    strike_date: datetime | None


@dataclass(frozen=True)
class MarketHit:
    ticker: str
    event_ticker: str
    series_ticker: str
    title: str | None
    yes_sub_title: str | None
    no_sub_title: str | None
    close_time: datetime


@dataclass(frozen=True)
class SearchHits:
    series: list[SeriesHit]
    events: list[EventHit]
    markets: list[MarketHit]


_TSQUERY = f"websearch_to_tsquery('{TS_CONFIG}', %(q)s)"


def _statement(
    *,
    columns: str,
    table: str,
    doc: str,
    title: str,
    ticker: str | None,
    where_extra: str,
    tiebreak: str,
    fuzzy: bool,
) -> str:
    """One level's statement; ``ticker`` adds the prefix match and its rank.

    ``fuzzy`` adds the trigram typo match to the ``WHERE``. It is a separate
    statement because the trigram index matches every row containing the word
    — doubling the candidate set for "bitcoin" — so it runs only to fill a
    group the exact statement left short.
    """
    prefix = f"starts_with(upper({ticker}), upper(%(q)s))" if ticker else None
    matches = [f"{doc} @@ {_TSQUERY}"]
    if fuzzy:
        matches.append(f"%(q)s <%% {title}")
    ranks = [
        f"{doc} @@ {_TSQUERY} DESC",
        f"ts_rank_cd({doc}, {_TSQUERY}) "
        f"+ coalesce(word_similarity(%(q)s, {title}), 0) DESC",
        tiebreak,
    ]
    if prefix:
        matches.append(prefix)
        ranks.insert(0, f"{prefix} DESC")
    return f"""
        SELECT {columns}
        FROM {table}
        WHERE {where_extra} AND ({" OR ".join(matches)})
        ORDER BY {", ".join(ranks)}
        LIMIT %(limit)s
    """  # noqa: S608 — every interpolation is a module-local literal


@dataclass(frozen=True)
class _Level[Hit]:
    """One level of the hierarchy: its hit type and its two statements."""

    hit: Callable[..., Hit]
    exact_sql: str
    fuzzy_sql: str


def _level_spec[Hit](hit: Callable[..., Hit], **fields: str | None) -> _Level[Hit]:
    return _Level(
        hit,
        _statement(**fields, fuzzy=False),  # type: ignore[arg-type]
        _statement(**fields, fuzzy=True),  # type: ignore[arg-type]
    )


_SERIES = _level_spec(
    SeriesHit,
    columns="ticker, title, category",
    table="kalshi.series",
    doc=SERIES_DOC,
    title="title",
    ticker="ticker",
    where_extra="TRUE",
    tiebreak="ticker",
)

_EVENTS = _level_spec(
    EventHit,
    columns="event_ticker, series_ticker, title, sub_title, strike_date",
    table="kalshi.events",
    doc=EVENT_DOC,
    title="title",
    ticker=None,
    where_extra="TRUE",
    tiebreak=(
        "(strike_date >= now()) DESC NULLS LAST, "
        "abs(extract(epoch FROM strike_date - now())) NULLS LAST, event_ticker"
    ),
)

_MARKETS = _level_spec(
    MarketHit,
    columns=(
        "ticker, event_ticker, "
        "(SELECT series_ticker FROM kalshi.events e "
        "WHERE e.event_ticker = kalshi.markets.event_ticker), "
        "title, yes_sub_title, no_sub_title, close_time"
    ),
    table="kalshi.markets",
    doc=MARKET_DOC,
    title="title",
    ticker=None,
    where_extra=OPEN_MARKET_PREDICATE,
    tiebreak="close_time, ticker",
)


def _run[Hit](
    conn: psycopg.Connection, level: _Level[Hit], params: dict[str, object]
) -> list[Hit]:
    rows = conn.execute(level.exact_sql, params).fetchall()
    if len(rows) < params["limit"]:  # type: ignore[operator]
        rows = conn.execute(level.fuzzy_sql, params).fetchall()
    return [level.hit(*row) for row in rows]


def search(conn: psycopg.Connection, query: str, *, limit: int) -> SearchHits:
    """The top ``limit`` hits at each level for ``query``.

    The typo threshold is set for this transaction only (``set_config(...,
    true)``), so a pooled connection goes back with pg_trgm's default.
    """
    params: dict[str, object] = {"q": query, "limit": limit}
    with conn.transaction():
        conn.execute(
            "SELECT set_config('pg_trgm.word_similarity_threshold', %s, true)",
            (str(SEARCH_TYPO_THRESHOLD),),
        )
        return SearchHits(
            series=_run(conn, _SERIES, params),
            events=_run(conn, _EVENTS, params),
            markets=_run(conn, _MARKETS, params),
        )
