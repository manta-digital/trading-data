"""``CandleRepository`` — every SQL statement of the candle phase (slice 264).

The collection rule is rendered in exactly one place —
``candle_selection.selection_sql`` — and the pending queries and counts
here embed it; nothing in this module re-spells a clause of it.

The shape follows ``CatalogRepository``: an open async connection the caller
owns, one transaction per batch chosen by the core, multi-row writes chunked
under the bind-parameter ceiling, lifecycle values bound from
``MarketStatus``. No exception is caught here — the storage failure taxonomy
(an ``IntegrityError`` retried per market, ``OperationalError`` as storage
abort, anything else a bug) is applied by ``CandleSync``, as 262's is.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.candle_plan import (
    PendingMarket,
    last_complete_period,
    period_span,
)
from manta_trading.data.kalshi.candle_selection import (
    BACKLOG_CONDITION,
    BEHIND_CUTOFF_CONDITION,
    MARKET_JOIN,
    SelectionForm,
    selection_sql,
)
from manta_trading.data.kalshi.candle_types import CandleRule
from manta_trading.data.kalshi.constants import CandlePeriod, MarketStatus, Surface
from manta_trading.data.kalshi.models import Candlestick
from manta_trading.data.kalshi.repository import _MAX_BIND_PARAMS, CatalogRepository

_OHLC = ("open", "high", "low", "close")
#: Decision 10: the flattening map from ``Candlestick``'s nested ``yes_bid`` /
#: ``yes_ask`` / ``price`` objects to the table's non-key columns —
#: ``(column, attribute path)``. Defined once; the migration parity test
#: checks it against the live table in both directions.
CANDLE_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    *(
        (f"{obj}_{field}_dollars", (obj, f"{field}_dollars"))
        for obj, fields in (
            ("yes_bid", _OHLC),
            ("yes_ask", _OHLC),
            ("price", (*_OHLC, "previous", "mean")),
        )
        for field in fields
    ),
    ("volume_fp", ("volume_fp",)),
    ("open_interest_fp", ("open_interest_fp",)),
)
_KEY_COLUMNS = ("market_ticker", "period", "end_period_ts")


@dataclass(frozen=True)
class StateAdvance:
    """One ``market_candle_state`` upsert after a batch (Data Flow step 5)."""

    ticker: str
    watermark_ts: datetime
    coverage_from_ts: datetime


#: Decision 3: pending = opened before the phase and watermark NULL or below
#: the target end ``min(close_time + period, last_complete_period)``.
_PENDING = sql.SQL(
    "AND m.open_time IS NOT NULL AND m.open_time < %(phase_start)s "
    "AND (st.watermark_ts IS NULL OR st.watermark_ts < "
    "LEAST(m.close_time + %(span)s, %(last_complete)s)) "
)


class CandleRepository:
    """SQL for the candle phase over one open async connection."""

    def __init__(self, conn: psycopg.AsyncConnection[Any], rule: CandleRule) -> None:
        self._conn = conn
        self._rule = rule
        # ``sync_state`` statements are shared with the catalog; one spelling.
        self._sync_state = CatalogRepository(conn)

    def transaction(self) -> AbstractAsyncContextManager[psycopg.AsyncTransaction]:
        """A transaction block on the run's connection (caller-owned granularity)."""
        return self._conn.transaction()

    # ------------------------------------------------------------------
    # Pending sets (Data Flow step 2)
    # ------------------------------------------------------------------

    async def pending_live(
        self, period: CandlePeriod, phase_start: datetime
    ) -> list[PendingMarket]:
        """Not finalized, recent-trade form; unbounded — the steady state."""
        return await self._pending(
            "recent",
            sql.SQL("AND m.status <> %(finalized)s "),
            period,
            phase_start,
            order=sql.SQL("ORDER BY m.ticker"),
        )

    async def pending_finishing(
        self, period: CandlePeriod, phase_start: datetime
    ) -> list[PendingMarket]:
        """Finalized *with* a state row short of close; unbounded, so a market
        that settled since the last pass never queues behind history."""
        return await self._pending(
            "ever",
            sql.SQL("AND m.status = %(finalized)s AND st.watermark_ts IS NOT NULL "),
            period,
            phase_start,
            order=sql.SQL("ORDER BY m.ticker"),
        )

    async def pending_backlog(
        self, period: CandlePeriod, phase_start: datetime, cutoff: datetime, limit: int
    ) -> list[PendingMarket]:
        """Finalized since the cutoff with no state row, oldest settlement
        first, capped (Decision 6)."""
        return await self._pending(
            "ever",
            sql.SQL(
                "AND m.status = %(finalized)s AND m.settlement_ts >= %(cutoff)s "
                "AND st.market_ticker IS NULL "
            ),
            period,
            phase_start,
            order=sql.SQL("ORDER BY m.settlement_ts, m.ticker LIMIT %(limit)s"),
            cutoff=cutoff,
            limit=limit,
        )

    async def _pending(
        self,
        form: SelectionForm,
        condition: sql.SQL,
        period: CandlePeriod,
        phase_start: datetime,
        *,
        order: sql.SQL,
        **extra: object,
    ) -> list[PendingMarket]:
        selection = selection_sql(self._rule, form)
        statement = sql.Composed(
            [
                sql.SQL("SELECT m.ticker, m.open_time, m.close_time, st.watermark_ts "),
                MARKET_JOIN,
                sql.SQL("WHERE "),
                selection.predicate,
                sql.SQL(" "),
                _PENDING,
                condition,
                order,
            ]
        )
        params: dict[str, object] = {
            **selection.params,
            "period": int(period),
            "phase_start": phase_start,
            "span": period_span(period),
            "last_complete": last_complete_period(phase_start, period),
            "finalized": MarketStatus.FINALIZED.value,
            **extra,
        }
        cursor = await self._conn.execute(statement, params)
        return [PendingMarket(*row) for row in await cursor.fetchall()]

    # ------------------------------------------------------------------
    # Counts the core reports (Criteria 8 and 9)
    # ------------------------------------------------------------------

    async def count_backlog_remaining(
        self, period: CandlePeriod, cutoff: datetime
    ) -> int:
        """Selected finalized markets since the cutoff with no state row — the
        *full* remainder, not the capped set ``pending_backlog`` returns."""
        return await self._count(period, cutoff, BACKLOG_CONDITION)

    async def count_behind_cutoff(self, period: CandlePeriod, cutoff: datetime) -> int:
        """Selected finalized markets before the cutoff with no state row —
        no longer served live; slice 266's input."""
        return await self._count(period, cutoff, BEHIND_CUTOFF_CONDITION)

    async def _count(
        self, period: CandlePeriod, cutoff: datetime, condition: sql.SQL
    ) -> int:
        selection = selection_sql(self._rule, "ever")
        statement = sql.Composed(
            [
                sql.SQL("SELECT count(*) "),
                MARKET_JOIN,
                sql.SQL("WHERE "),
                selection.predicate,
                sql.SQL(" AND "),
                condition,
            ]
        )
        cursor = await self._conn.execute(
            statement,
            {
                **selection.params,
                "period": int(period),
                "cutoff": cutoff,
                "finalized": MarketStatus.FINALIZED.value,
            },
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------
    # Writes (Data Flow step 5)
    # ------------------------------------------------------------------

    async def insert_candles(
        self, period: CandlePeriod, candles: Iterable[tuple[str, Candlestick]]
    ) -> int:
        """Conflict-ignore insert of ``(ticker, candle)`` pairs; rows written.

        Never ``DO UPDATE`` (Decision 10): a stored candle is immutable, and
        the one-period guard keeps a still-settling one out of the table.
        """
        columns = [*_KEY_COLUMNS, *(column for column, _ in CANDLE_COLUMNS)]
        rows = [
            [ticker, int(period), candle.end_period_ts, *_flatten(candle)]
            for ticker, candle in candles
        ]
        if not rows:
            return 0
        rows_per_statement = max(1, _MAX_BIND_PARAMS // len(columns))
        row_template = sql.SQL("({})").format(
            sql.SQL(", ").join(sql.Placeholder() * len(columns))
        )
        written = 0
        for start in range(0, len(rows), rows_per_statement):
            chunk = rows[start : start + rows_per_statement]
            statement = sql.SQL(
                "INSERT INTO kalshi.candlesticks ({cols}) VALUES {values} "
                "ON CONFLICT DO NOTHING"
            ).format(
                cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
                values=sql.SQL(", ").join(row_template * len(chunk)),
            )
            cursor = await self._conn.execute(
                statement, [value for row in chunk for value in row]
            )
            written += cursor.rowcount
        return written

    async def advance_state(
        self, period: CandlePeriod, advances: Sequence[StateAdvance]
    ) -> None:
        """One multi-row upsert: the watermark moves to the batch's; the
        coverage start is set once and never moved later by a re-run."""
        if not advances:
            return
        row_template = sql.SQL("(%s, %s, %s, %s, now())")
        statement = sql.SQL(
            "INSERT INTO kalshi.market_candle_state "
            "(market_ticker, period, watermark_ts, coverage_from_ts, updated_at) "
            "VALUES {values} "
            "ON CONFLICT (market_ticker, period) DO UPDATE SET "
            "watermark_ts = EXCLUDED.watermark_ts, "
            "coverage_from_ts = COALESCE(kalshi.market_candle_state.coverage_from_ts, "
            "EXCLUDED.coverage_from_ts), "
            "updated_at = now()"
        ).format(values=sql.SQL(", ").join(row_template * len(advances)))
        params: list[object] = []
        for advance in advances:
            params.extend(
                (
                    advance.ticker,
                    int(period),
                    advance.watermark_ts,
                    advance.coverage_from_ts,
                )
            )
        await self._conn.execute(statement, params)

    async def set_sync_state(self, phase_start: datetime, cutoff: datetime) -> None:
        """Decision 11: ``last_full_sync_at`` = this phase's start, and
        ``watermark_ts`` = the historical cutoff it observed."""
        await self._sync_state.set_last_full_sync(Surface.CANDLESTICKS, phase_start)
        await self._sync_state.set_watermark(Surface.CANDLESTICKS, cutoff)


def _flatten(candle: Candlestick) -> list[object]:
    """Column values in ``CANDLE_COLUMNS`` order (nested OHLC → columns)."""
    values: list[object] = []
    for _, path in CANDLE_COLUMNS:
        value: object = candle
        for attribute in path:
            value = getattr(value, attribute)
        values.append(value)
    return values
