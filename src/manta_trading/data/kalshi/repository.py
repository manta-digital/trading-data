"""``CatalogRepository`` — every SQL statement for ``kalshi.*`` (slice 262).

Takes an open :class:`psycopg.AsyncConnection` and never opens one. Each
public method runs inside the *caller's* transaction — the sync core wraps a
page in :meth:`CatalogRepository.transaction` — so transaction granularity is
decided (and tested) in one place. No exception is caught here: the storage
failure taxonomy (slice design, review F001) is applied by the sync core.

Write-on-change (Decision 6): every upsert is one multi-row ``INSERT … ON
CONFLICT DO UPDATE … WHERE <table>.raw IS DISTINCT FROM EXCLUDED.raw``, so an
unchanged row costs nothing and ``last_synced_at`` means "last content
change". Column mapping is one-to-one from the 261 models (the migration
parity test guarantees the two sides agree); ``raw`` is the JSON dump of the
served object, so equality is by content.

Lifecycle values (``MarketStatus``) and surfaces are always bound parameters
— no status literal appears in SQL text.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.models import Event, KalshiModel, Market, Series

#: Postgres accepts at most this many bind parameters in one statement; a
#: page is split into as many statements as that ceiling requires.
_MAX_BIND_PARAMS = 65_535

SERIES_COLUMNS: tuple[str, ...] = tuple(Series.model_fields)
#: ``Event.markets`` is the nested list served by ``with_nested_markets``;
#: never a column (migration parity test: ``model_only={"markets"}``).
EVENT_COLUMNS: tuple[str, ...] = tuple(f for f in Event.model_fields if f != "markets")
MARKET_COLUMNS: tuple[str, ...] = tuple(Market.model_fields)


@dataclass(frozen=True)
class MarketUpsertOutcome:
    """Result of one markets page: rows written and status transitions.

    ``transitions`` counts ``(from_status, to_status)`` only for rows that
    already existed with a different status (Decision 7); new rows are not
    transitions.
    """

    written: int
    transitions: dict[tuple[str, str], int]


@dataclass(frozen=True)
class SyncState:
    """One ``kalshi.sync_state`` row (semantics: design *State Management*)."""

    last_full_sync_at: datetime | None
    watermark_ts: datetime | None
    cursor: str | None


def _row_values(
    model: KalshiModel, columns: Sequence[str], raw: dict[str, Any]
) -> list[object]:
    """Bind values for one row: model attributes, JSONB for containers, then ``raw``."""
    values: list[object] = []
    for column in columns:
        value = getattr(model, column)
        if isinstance(value, list | dict):
            value = Jsonb(raw[column])
        values.append(value)
    values.append(Jsonb(raw))
    return values


def _dedupe_by_key(rows: Iterable[KalshiModel], key: str) -> list[KalshiModel]:
    """Last occurrence wins: ``ON CONFLICT DO UPDATE`` refuses to touch a row twice."""
    by_key: dict[str, KalshiModel] = {}
    for row in rows:
        by_key[getattr(row, key)] = row
    return list(by_key.values())


class CatalogRepository:
    """SQL for the Kalshi catalog over one open async connection."""

    def __init__(self, conn: psycopg.AsyncConnection[Any]) -> None:
        self._conn = conn

    def transaction(self) -> AbstractAsyncContextManager[psycopg.AsyncTransaction]:
        """A transaction block on the run's connection (caller-owned granularity)."""
        return self._conn.transaction()

    # ------------------------------------------------------------------
    # Write-on-change upserts
    # ------------------------------------------------------------------

    async def _upsert(
        self,
        table: str,
        key: str,
        columns: Sequence[str],
        rows: Sequence[KalshiModel],
        *,
        raw_exclude: set[str] | None = None,
    ) -> int:
        """One multi-row upsert per parameter-ceiling chunk; returns rows written."""
        rows = _dedupe_by_key(rows, key)
        if not rows:
            return 0
        all_columns = [*columns, "raw"]
        per_row = len(all_columns)
        rows_per_statement = max(1, _MAX_BIND_PARAMS // per_row)
        row_template = sql.SQL("({})").format(
            sql.SQL(", ").join(sql.Placeholder() * per_row)
        )
        assignments = sql.SQL(", ").join(
            sql.SQL("{col} = EXCLUDED.{col}").format(col=sql.Identifier(c))
            for c in all_columns
            if c != key
        )
        written = 0
        for start in range(0, len(rows), rows_per_statement):
            chunk = rows[start : start + rows_per_statement]
            statement = sql.SQL(
                "INSERT INTO kalshi.{table} ({cols}) VALUES {values} "
                "ON CONFLICT ({key}) DO UPDATE "
                "SET {assignments}, last_synced_at = now() "
                "WHERE kalshi.{table}.raw IS DISTINCT FROM EXCLUDED.raw"
            ).format(
                table=sql.Identifier(table),
                cols=sql.SQL(", ").join(map(sql.Identifier, all_columns)),
                values=sql.SQL(", ").join(row_template * len(chunk)),
                key=sql.Identifier(key),
                assignments=assignments,
            )
            params: list[object] = []
            for row in chunk:
                raw = row.model_dump(mode="json", exclude=raw_exclude)
                params.extend(_row_values(row, columns, raw))
            cursor = await self._conn.execute(statement, params)
            written += cursor.rowcount
        return written

    async def upsert_series(self, rows: Sequence[Series]) -> int:
        return await self._upsert("series", "ticker", SERIES_COLUMNS, rows)

    async def upsert_events(self, rows: Sequence[Event]) -> int:
        return await self._upsert(
            "events", "event_ticker", EVENT_COLUMNS, rows, raw_exclude={"markets"}
        )

    async def upsert_markets(self, rows: Sequence[Market]) -> MarketUpsertOutcome:
        """Upsert a markets page and report the status transitions it caused."""
        rows = [r for r in _dedupe_by_key(rows, "ticker") if isinstance(r, Market)]
        if not rows:
            return MarketUpsertOutcome(written=0, transitions={})
        prior = await self._statuses([r.ticker for r in rows])
        written = await self._upsert("markets", "ticker", MARKET_COLUMNS, rows)
        transitions: dict[tuple[str, str], int] = {}
        for row in rows:
            before = prior.get(row.ticker)
            if before is not None and before != row.status:
                edge = (before, row.status)
                transitions[edge] = transitions.get(edge, 0) + 1
        return MarketUpsertOutcome(written=written, transitions=transitions)

    async def _statuses(self, tickers: Sequence[str]) -> dict[str, str]:
        cursor = await self._conn.execute(
            "SELECT ticker, status FROM kalshi.markets WHERE ticker = ANY(%s)",
            (list(tickers),),
        )
        return {ticker: status for ticker, status in await cursor.fetchall()}

    # ------------------------------------------------------------------
    # Parent lookups (Decision 9)
    # ------------------------------------------------------------------

    async def known_event_tickers(self, tickers: Iterable[str]) -> set[str]:
        return await self._known("events", "event_ticker", tickers)

    async def known_series_tickers(self, tickers: Iterable[str]) -> set[str]:
        return await self._known("series", "ticker", tickers)

    async def _known(self, table: str, key: str, tickers: Iterable[str]) -> set[str]:
        wanted = list(set(tickers))
        if not wanted:
            return set()
        cursor = await self._conn.execute(
            sql.SQL("SELECT {key} FROM kalshi.{table} WHERE {key} = ANY(%s)").format(
                key=sql.Identifier(key), table=sql.Identifier(table)
            ),
            (wanted,),
        )
        return {row[0] for row in await cursor.fetchall()}

    # ------------------------------------------------------------------
    # Awaiting-settlement set (Decision 3)
    # ------------------------------------------------------------------

    async def enter_awaiting(self, now: datetime) -> int:
        """Enter every stored market whose close has passed and is not finalized."""
        cursor = await self._conn.execute(
            "INSERT INTO kalshi.awaiting_settlement (market_ticker, close_time) "
            "SELECT ticker, close_time FROM kalshi.markets "
            "WHERE close_time <= %(now)s AND status <> %(finalized)s "
            "ON CONFLICT DO NOTHING",
            {"now": now, "finalized": MarketStatus.FINALIZED.value},
        )
        return cursor.rowcount

    async def retire_awaiting(self) -> int:
        """Retire markets whose stored row is finalized *with* a result."""
        cursor = await self._conn.execute(
            "DELETE FROM kalshi.awaiting_settlement a USING kalshi.markets m "
            "WHERE m.ticker = a.market_ticker "
            "AND m.status = %(finalized)s AND m.result IS NOT NULL",
            {"finalized": MarketStatus.FINALIZED.value},
        )
        return cursor.rowcount

    async def refresh_awaiting_close_times(self) -> int:
        """Copy a changed ``close_time`` from the market to its awaiting row."""
        cursor = await self._conn.execute(
            "UPDATE kalshi.awaiting_settlement a SET close_time = m.close_time "
            "FROM kalshi.markets m "
            "WHERE m.ticker = a.market_ticker AND m.close_time <> a.close_time"
        )
        return cursor.rowcount

    async def awaiting_tickers(self) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT market_ticker FROM kalshi.awaiting_settlement "
            "ORDER BY market_ticker"
        )
        return [row[0] for row in await cursor.fetchall()]

    async def mark_checked(self, tickers: Sequence[str], now: datetime) -> int:
        if not tickers:
            return 0
        cursor = await self._conn.execute(
            "UPDATE kalshi.awaiting_settlement SET last_checked_at = %s "
            "WHERE market_ticker = ANY(%s)",
            (now, list(tickers)),
        )
        return cursor.rowcount

    # ------------------------------------------------------------------
    # sync_state (design: State Management) — ``cursor`` is never written
    # ------------------------------------------------------------------

    async def get_sync_state(self, surface: Surface) -> SyncState | None:
        cursor = await self._conn.execute(
            "SELECT last_full_sync_at, watermark_ts, cursor "
            "FROM kalshi.sync_state WHERE surface = %s",
            (surface.value,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return SyncState(last_full_sync_at=row[0], watermark_ts=row[1], cursor=row[2])

    async def set_last_full_sync(self, surface: Surface, ts: datetime) -> None:
        await self._set_state_column("last_full_sync_at", surface, ts)

    async def set_watermark(self, surface: Surface, ts: datetime) -> None:
        await self._set_state_column("watermark_ts", surface, ts)

    async def _set_state_column(
        self, column: str, surface: Surface, ts: datetime
    ) -> None:
        await self._conn.execute(
            sql.SQL(
                "INSERT INTO kalshi.sync_state (surface, {col}, updated_at) "
                "VALUES (%s, %s, now()) "
                "ON CONFLICT (surface) DO UPDATE "
                "SET {col} = EXCLUDED.{col}, updated_at = now()"
            ).format(col=sql.Identifier(column)),
            (surface.value, ts),
        )
