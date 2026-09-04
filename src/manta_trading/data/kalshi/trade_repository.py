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
    trades_filter_sql,
)
from manta_trading.data.kalshi.trade_types import UnknownTradesFilterCategoryError

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
    """What one ``write_page`` did — six independently sourced numbers.

    ``fetched`` is what the client handed over (``len(rows)``); the others
    come from the statement. ``selected`` is **carried, not derived**:
    derived as ``fetched − unknown − excluded`` the identity below could never
    fail, and it exists to catch page rows that never reached ``classified``
    (a join or ``unnest`` arity bug). Criterion 2 is an exact accounting.

    ``excluded_by_trades_filter`` (slice 268, Decision 4) counts rule-selected
    rows the trades-tape category filter kept out of storage; precedence is
    unknown → excluded-by-rule → excluded-by-trades-filter → stored/duplicate,
    so the buckets partition the page. ``selected`` here means selected for
    storage — rule-selected AND not tape-filtered — keeping ``duplicates``
    exact under a filter.
    """

    fetched: int
    unknown_market: int
    excluded_by_rule: int
    excluded_by_trades_filter: int
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
            self.written
            + self.unknown_market
            + self.excluded_by_rule
            + self.excluded_by_trades_filter
            + self.duplicates
        )
        if self.fetched != accounted:
            raise PageAccountingError(
                f"page accounting: fetched {self.fetched} != written {self.written} "
                f"+ unknown {self.unknown_market} + excluded {self.excluded_by_rule} "
                f"+ filtered {self.excluded_by_trades_filter} "
                f"+ duplicates {self.duplicates} (selected {self.selected})"
            )


@dataclass(frozen=True)
class TradeState:
    """A tape surface's ``kalshi.sync_state`` row — the watermark and its
    floor. For ``trades`` the watermark is the top of the live tape; for
    ``historical`` (slice 267) it is the bottom of the backfilled range and
    the floor is ``HISTORICAL_TRADES_FLOOR``."""

    watermark_ts: datetime | None
    coverage_from_ts: datetime | None


def _write_page_statement(
    rule: CollectionRule,
    trades_excluded: frozenset[str],
) -> tuple[sql.Composed, dict[str, object]]:
    """The Decision 5 statement and the parameters it binds — the rule's and,
    since slice 268, the trades-tape filter's.

    ``selected`` is computed once in ``classified``; ``flagged`` derives
    ``tape_filtered`` from it (``known AND selected AND`` the filter's
    membership test) — the rule predicate is never pasted twice, which is
    what makes the precedence unknown → excluded-by-rule →
    excluded-by-trades-filter → stored/duplicate structural. The insert and
    the returned ``selected`` count both use ``selected AND NOT
    tape_filtered``, so ``duplicates`` stays exact.
    """
    selection = selection_sql(rule, "any")
    trades_filter = trades_filter_sql(trades_excluded)
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
        "COALESCE({predicate}, FALSE) AS selected, "
        "{tape_test} AS tape_test "
        "FROM page p LEFT JOIN ({catalog}) ON m.ticker = p.market_ticker"
        "), flagged AS ("
        "SELECT *, known AND selected AND tape_test AS tape_filtered "
        "FROM classified"
        "), ins AS ("
        "INSERT INTO kalshi.trades ({names}) SELECT {names} FROM flagged "
        "WHERE selected AND NOT tape_filtered ON CONFLICT DO NOTHING RETURNING 1"
        ") "
        "SELECT count(*) FILTER (WHERE NOT known), "
        "count(*) FILTER (WHERE known AND NOT selected), "
        "count(*) FILTER (WHERE tape_filtered), "
        "count(*) FILTER (WHERE selected AND NOT tape_filtered), "
        "(SELECT count(*) FROM ins), "
        "COALESCE(array_agg(market_ticker) FILTER (WHERE NOT known), ARRAY[]::text[]) "
        "FROM flagged"
    ).format(
        arrays=arrays,
        names=names,
        predicate=selection.predicate,
        tape_test=trades_filter.predicate,
        catalog=CATALOG_TABLES,
    )
    return statement, {**selection.params, **trades_filter.params}


class TradeRepository:
    """SQL for the trades phase over one open async connection.

    ``surface`` selects the ``sync_state`` row the state methods bind
    (slice 267, Decision 5): ``trades`` for the live phase, ``historical``
    for the backward drain. ``write_page`` is surface-free — the tape is one
    table.
    """

    def __init__(
        self,
        conn: psycopg.AsyncConnection[Any],
        rule: CollectionRule,
        *,
        trades_excluded: frozenset[str],
        surface: Surface = Surface.TRADES,
    ) -> None:
        self._conn = conn
        self._surface = surface
        # Required keyword, no default (slice 268): a construction site that
        # forgets the filter must fail to construct, not silently store
        # filtered categories. The repository is the one carrier of the set —
        # sync reads it back through the property for its start log line.
        self._trades_excluded = trades_excluded
        # The statement is rule- and filter-dependent, both fixed for the
        # run: render it once, bind a page's nine arrays per call.
        self._statement, self._rule_params = _write_page_statement(
            rule, trades_excluded
        )
        # ``sync_state`` statements are shared with the catalog; one spelling.
        self._sync_state = CatalogRepository(conn)

    @property
    def trades_excluded(self) -> frozenset[str]:
        """The trades-tape category filter this repository's statement
        embeds (slice 268)."""
        return self._trades_excluded

    def transaction(self) -> AbstractAsyncContextManager[psycopg.AsyncTransaction]:
        """A transaction block on the run's connection (caller-owned granularity)."""
        return self._conn.transaction()

    # ------------------------------------------------------------------
    # State (Data Flow steps 1, 2, 5, 6)
    # ------------------------------------------------------------------

    @property
    def surface(self) -> Surface:
        return self._surface

    async def read_state(self) -> TradeState | None:
        """This surface's row, or ``None`` on the first run. Its own
        statement: ``CatalogRepository.get_sync_state`` does not carry
        ``coverage_from_ts`` (the catalog surface has no coverage floor)."""
        row = await self._read_row("watermark_ts, coverage_from_ts", self._surface)
        if row is None:
            return None
        return TradeState(watermark_ts=row[0], coverage_from_ts=row[1])

    async def init_state(self, watermark: datetime, coverage_from: datetime) -> None:
        """First run only: seed this surface's row. The live phase passes the
        cutoff twice (Decision 2: the tape starts at the cutoff and is
        complete through it); the historical phase passes the live floor and
        ``HISTORICAL_TRADES_FLOOR`` (slice 267). Set once: an instant already
        on the row is never overwritten, so re-entry is a no-op. A row that
        exists with NULL instants is filled — the archive walk creates the
        historical row through ``set_cursor`` before the tape is seeded
        (Decision 9: the watermark, set here, is the walk's done marker)."""
        await self._conn.execute(
            "INSERT INTO kalshi.sync_state "
            "(surface, watermark_ts, coverage_from_ts, updated_at) "
            "VALUES (%s, %s, %s, now()) ON CONFLICT (surface) DO UPDATE SET "
            "watermark_ts = COALESCE(kalshi.sync_state.watermark_ts, "
            "EXCLUDED.watermark_ts), "
            "coverage_from_ts = COALESCE(kalshi.sync_state.coverage_from_ts, "
            "EXCLUDED.coverage_from_ts), "
            "updated_at = now()",
            (self._surface.value, watermark, coverage_from),
        )

    async def advance_watermark(self, window_edge: datetime) -> None:
        """Data Flow step 5: the tape is complete through ``window_edge`` —
        the window's far edge in the walk's direction (slice 267,
        Decision 5)."""
        await self._sync_state.set_watermark(self._surface, window_edge)

    async def set_last_full_sync(self, phase_start: datetime) -> None:
        """Data Flow step 6."""
        await self._sync_state.set_last_full_sync(self._surface, phase_start)

    async def read_live_coverage_from(self) -> datetime | None:
        """The live ``trades`` row's ``coverage_from_ts`` — where the
        historical drain starts descending from (slice 267, Criterion 2);
        ``None`` when the live phase has never run."""
        row = await self._read_row("coverage_from_ts", Surface.TRADES)
        return None if row is None else row[0]

    async def read_cursor(self) -> str | None:
        """This surface's ``sync_state.cursor`` — the archive walk's resume
        point (slice 267, Decision 9); ``None`` when no walk is in progress."""
        row = await self._read_row("cursor", self._surface)
        return None if row is None else row[0]

    async def set_cursor(self, cursor: str | None) -> None:
        """Save (or, with ``None``, clear) this surface's resume cursor. Its
        own statement: ``CatalogRepository._set_state_column`` types its
        value as a datetime."""
        await self._conn.execute(
            "INSERT INTO kalshi.sync_state (surface, cursor, updated_at) "
            "VALUES (%s, %s, now()) ON CONFLICT (surface) DO UPDATE "
            "SET cursor = EXCLUDED.cursor, updated_at = now()",
            (self._surface.value, cursor),
        )

    async def _read_row(
        self, columns: LiteralString, surface: Surface
    ) -> tuple[Any, ...] | None:
        cursor = await self._conn.execute(
            f"SELECT {columns} FROM kalshi.sync_state WHERE surface = %s",
            (surface.value,),
        )
        return await cursor.fetchone()

    async def read_catalog_walk_start(self) -> datetime | None:
        """``sync_state['catalog'].last_full_sync_at`` — what the pass bound
        trails (Decision 5); ``None`` when the catalog has never walked."""
        state = await self._sync_state.get_sync_state(Surface.CATALOG)
        return None if state is None else state.last_full_sync_at

    async def assert_trades_filter_known(self) -> None:
        """Decision 9 (slice 268), the one spelling of the check for both
        phases: every configured filter category must appear on some
        ``kalshi.series`` row, else raise — a typo must never become a
        silent no-op. An empty filter skips the query entirely."""
        if not self._trades_excluded:
            return
        cursor = await self._conn.execute(
            "SELECT DISTINCT category FROM kalshi.series WHERE category IS NOT NULL"
        )
        known = {row[0] for row in await cursor.fetchall()}
        unknown = sorted(self._trades_excluded - known)
        if unknown:
            raise UnknownTradesFilterCategoryError(unknown, sorted(known))

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
            return PageCounts(0, 0, 0, 0, 0, 0)
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
        unknown, excluded, filtered, selected, written = (
            int(value) for value in counts[:5]
        )
        return PageCounts(
            fetched=len(rows),
            unknown_market=unknown,
            excluded_by_rule=excluded,
            excluded_by_trades_filter=filtered,
            selected=selected,
            written=written,
            unknown_tickers=tuple(counts[5]),
        )
