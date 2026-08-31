"""``TradeRepository`` — every SQL statement of the trades phase (slice 265).

The collection rule is rendered in exactly one place —
``selection.selection_sql`` — and ``write_page`` embeds its ``"any"`` form
(Decision 3: a trade is proof of trading). Classification happens in SQL,
per page, in one data-modifying statement (Decision 5): the page's rows are
``unnest``-ed, ``LEFT JOIN``-ed onto the catalog, split into *unknown*
(no market row), *excluded* (known, the rule does not select) and
*selected*, and the selected rows are inserted conflict-ignore — the
statement returns all four counts, plus the unknown tickers for the core's
display-only prefix tally, in one round trip.

No exception is caught here: ``psycopg.OperationalError`` propagates as a
storage abort and **any other** ``psycopg.Error`` propagates as a bug — a
non-UUID ``trade_id`` (``DataError``) or a missing ``is_block_trade``
(``NotNullViolation``) must fail the page loudly, never be coalesced.
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.constants import Surface
from manta_trading.data.kalshi.models import Trade
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.selection import (
    CATALOG_TABLES,
    CollectionRule,
    selection_sql,
)

#: Decision 11: the model→column map — ``(column, Trade attribute)``. The
#: key columns are mapped too (``Trade.ticker`` is ``market_ticker``);
#: ``taker_side`` is deprecated and not stored. Defined once; the integration
#: parity test checks it against the live table in both directions.
TRADE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("market_ticker", "ticker"),
    ("created_time", "created_time"),
    ("trade_id", "trade_id"),
    ("count_fp", "count_fp"),
    ("yes_price_dollars", "yes_price_dollars"),
    ("no_price_dollars", "no_price_dollars"),
    ("taker_outcome_side", "taker_outcome_side"),
    ("taker_book_side", "taker_book_side"),
    ("is_block_trade", "is_block_trade"),
)
#: The SQL array type each column's page array is cast to inside ``unnest``
#: — the cast is what lets PostgreSQL type the bound arrays, and what makes a
#: non-UUID id fail before classification (``::uuid[]``).
_ARRAY_TYPES: dict[str, LiteralString] = {
    "market_ticker": "text[]",
    "created_time": "timestamptz[]",
    "trade_id": "uuid[]",
    "count_fp": "numeric[]",
    "yes_price_dollars": "numeric[]",
    "no_price_dollars": "numeric[]",
    "taker_outcome_side": "text[]",
    "taker_book_side": "text[]",
    "is_block_trade": "boolean[]",
}


class PageAccountingError(ValueError):
    """A page's counts do not add up — rows never reached ``classified``."""


@dataclass(frozen=True)
class PageCounts:
    """What one ``write_page`` did — five independently sourced numbers.

    ``fetched`` is what the client handed over (``len(rows)``); the other
    four come from the statement. ``selected`` is **carried, not derived**:
    derived as ``fetched − unknown − excluded`` the identity below could never
    fail, and it exists to catch page rows that never reached ``classified``
    (a join or ``unnest`` arity bug). Criterion 2 is an exact accounting.
    """

    fetched: int
    unknown_market: int
    excluded_by_rule: int
    selected: int
    written: int
    #: The page's tickers with no market row, one per unknown trade — for the
    #: core's once-per-phase prefix log line (Decision 5, display only).
    unknown_tickers: tuple[str, ...] = ()

    @property
    def duplicates(self) -> int:
        """Selected rows the conflict-ignore insert did not write."""
        return self.selected - self.written

    def __post_init__(self) -> None:
        accounted = (
            self.written + self.unknown_market + self.excluded_by_rule + self.duplicates
        )
        if self.fetched != accounted:
            raise PageAccountingError(
                f"page accounting: fetched {self.fetched} != written {self.written} "
                f"+ unknown {self.unknown_market} + excluded {self.excluded_by_rule} "
                f"+ duplicates {self.duplicates} (selected {self.selected})"
            )


@dataclass(frozen=True)
class TradeState:
    """``kalshi.sync_state['trades']`` — the tape watermark and its floor."""

    watermark_ts: datetime | None
    coverage_from_ts: datetime | None


def _write_page_statement(
    rule: CollectionRule,
) -> tuple[sql.Composed, dict[str, object]]:
    """The Decision 5 statement and the rule parameters it binds."""
    selection = selection_sql(rule, "any")
    columns = [column for column, _ in TRADE_COLUMNS]
    arrays = sql.SQL(", ").join(
        sql.SQL("{}::{}").format(sql.Placeholder(column), sql.SQL(_ARRAY_TYPES[column]))
        for column in columns
    )
    names = sql.SQL(", ").join(map(sql.Identifier, columns))
    statement = sql.SQL(
        "WITH page AS ("
        "SELECT * FROM unnest({arrays}) AS p({names})"
        "), classified AS ("
        "SELECT p.*, m.ticker IS NOT NULL AS known, "
        "COALESCE({predicate}, FALSE) AS selected "
        "FROM page p LEFT JOIN ({catalog}) ON m.ticker = p.market_ticker"
        "), ins AS ("
        "INSERT INTO kalshi.trades ({names}) SELECT {names} FROM classified "
        "WHERE selected ON CONFLICT DO NOTHING RETURNING 1"
        ") "
        "SELECT count(*) FILTER (WHERE NOT known), "
        "count(*) FILTER (WHERE known AND NOT selected), "
        "count(*) FILTER (WHERE selected), "
        "(SELECT count(*) FROM ins), "
        "COALESCE(array_agg(market_ticker) FILTER (WHERE NOT known), ARRAY[]::text[]) "
        "FROM classified"
    ).format(
        arrays=arrays,
        names=names,
        predicate=selection.predicate,
        catalog=CATALOG_TABLES,
    )
    return statement, selection.params


class TradeRepository:
    """SQL for the trades phase over one open async connection."""

    def __init__(
        self, conn: psycopg.AsyncConnection[Any], rule: CollectionRule
    ) -> None:
        self._conn = conn
        # The statement is rule-dependent and the rule is fixed for the run:
        # render it once, bind a page's nine arrays per call.
        self._statement, self._rule_params = _write_page_statement(rule)
        # ``sync_state`` statements are shared with the catalog; one spelling.
        self._sync_state = CatalogRepository(conn)

    def transaction(self) -> AbstractAsyncContextManager[psycopg.AsyncTransaction]:
        """A transaction block on the run's connection (caller-owned granularity)."""
        return self._conn.transaction()

    # ------------------------------------------------------------------
    # State (Data Flow steps 1, 2, 5, 6)
    # ------------------------------------------------------------------

    async def read_state(self) -> TradeState | None:
        """The trades row, or ``None`` on the first run. Its own statement:
        ``CatalogRepository.get_sync_state`` does not carry
        ``coverage_from_ts`` (the catalog surface has no coverage floor)."""
        cursor = await self._conn.execute(
            "SELECT watermark_ts, coverage_from_ts "
            "FROM kalshi.sync_state WHERE surface = %s",
            (Surface.TRADES.value,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return TradeState(watermark_ts=row[0], coverage_from_ts=row[1])

    async def init_state(self, cutoff: datetime) -> None:
        """First run only (Decision 2): the tape starts at the cutoff and is
        complete through it. A no-op when the row exists — ``surface`` is
        the primary key, so a plain insert would raise on re-entry."""
        await self._conn.execute(
            "INSERT INTO kalshi.sync_state "
            "(surface, watermark_ts, coverage_from_ts, updated_at) "
            "VALUES (%s, %s, %s, now()) ON CONFLICT (surface) DO NOTHING",
            (Surface.TRADES.value, cutoff, cutoff),
        )

    async def advance_watermark(self, window_end: datetime) -> None:
        """Data Flow step 5: the tape is complete through ``window_end``."""
        await self._sync_state.set_watermark(Surface.TRADES, window_end)

    async def set_last_full_sync(self, phase_start: datetime) -> None:
        """Data Flow step 6."""
        await self._sync_state.set_last_full_sync(Surface.TRADES, phase_start)

    async def read_catalog_walk_start(self) -> datetime | None:
        """``sync_state['catalog'].last_full_sync_at`` — what the pass bound
        trails (Decision 5); ``None`` when the catalog has never walked."""
        state = await self._sync_state.get_sync_state(Surface.CATALOG)
        return None if state is None else state.last_full_sync_at

    # ------------------------------------------------------------------
    # Writes (Data Flow step 4)
    # ------------------------------------------------------------------

    async def write_page(self, rows: Sequence[Trade]) -> PageCounts:
        """Classify and store one page in one statement (Decision 5).

        The page is bound as one array per column — nine arrays, not nine
        thousand placeholders — which is what keeps a 1,000-row page under
        the bind-parameter ceiling by construction. The caller owns the
        transaction (one per page, Data Flow step 4).
        """
        if not rows:
            return PageCounts(0, 0, 0, 0, 0)
        arrays: dict[str, object] = {
            column: [getattr(row, attribute) for row in rows]
            for column, attribute in TRADE_COLUMNS
        }
        cursor = await self._conn.execute(
            self._statement, {**self._rule_params, **arrays}
        )
        counts = await cursor.fetchone()
        if counts is None:
            raise PageAccountingError("write_page statement returned no row")
        unknown, excluded, selected, written = (int(value) for value in counts[:4])
        return PageCounts(
            fetched=len(rows),
            unknown_market=unknown,
            excluded_by_rule=excluded,
            selected=selected,
            written=written,
            unknown_tickers=tuple(counts[4]),
        )
