"""The trades block of ``mt data kalshi status`` (slice 265, Decision 10).

Read-only, synchronous psycopg — one short read, the ``status.py`` pattern.
Every figure is a persisted fact: the state row (``sync_state['trades']``)
and the catalog join under the collection rule's ``"ever"`` form. **Nothing
counts rows in ``kalshi.trades``** (journal 20260720); per-market
completeness is derived from the single watermark and the coverage floor.
Neither the client nor the transport is imported (Criterion 11).

The four closed-market counts partition the selected closed markets, in
this precedence — a market is counted once, by the first that applies:

1. ``before_coverage`` — closed before the tape starts (266's input);
2. ``short_of_close`` — the tape has not reached its close yet;
3. ``partial_history`` — the tape starts mid-life (opened before the
   coverage floor, or with no recorded open — nothing proves the tape covers
   its whole life);
4. ``complete_through_close`` — opened at or after the floor and closed at
   or before the watermark.

(The design lists the four by their defining condition; precedence is what
makes them a partition — a market opened before the floor whose close the
tape has not reached is *short of close*, not yet *partial*.)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.constants import TRADE_LAG_STALE_AFTER, Surface
from manta_trading.data.kalshi.selection import (
    CATALOG_JOIN,
    CollectionRule,
    selection_sql,
)
from manta_trading.data.kalshi.sync_types import iso_utc


@dataclass(frozen=True)
class TradeStatus:
    """The trades block (design *CLI and rendering*)."""

    last_phase_at: datetime | None
    tape_through: datetime
    lag: timedelta
    behind: bool
    coverage_from: datetime
    complete_through_close: int
    partial_history: int
    short_of_close: int
    before_coverage: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_phase_at": iso_utc(self.last_phase_at),
            "tape_through": iso_utc(self.tape_through),
            "lag_minutes": int(self.lag.total_seconds() // 60),
            "behind": self.behind,
            "coverage_from": iso_utc(self.coverage_from),
            "complete_through_close": self.complete_through_close,
            "partial_history": self.partial_history,
            "short_of_close": self.short_of_close,
            "before_coverage": self.before_coverage,
            "stale_after_minutes": int(TRADE_LAG_STALE_AFTER.total_seconds() // 60),
        }


#: The state row and the lag, in one read; ``now()`` is the database's so the
#: lag and the closed-market cut below agree.
STATE_QUERY = sql.SQL(
    "SELECT last_full_sync_at, watermark_ts, coverage_from_ts, "
    "now() - watermark_ts FROM kalshi.sync_state WHERE surface = %(surface)s"
)
#: One statement, one scan of the join: the rule embeds ``selection_sql``
#: (never re-spelled). The four buckets are the module docstring's precedence,
#: spelled so each row satisfies exactly one; ``COALESCE(..., FALSE)`` keeps a
#: NULL ``open_time`` in *partial*, never in none.
TRADE_COUNTS = sql.SQL(
    "SELECT "
    "count(*) FILTER (WHERE m.close_time < %(coverage_from)s), "
    "count(*) FILTER (WHERE m.close_time >= %(coverage_from)s "
    "  AND m.close_time > %(watermark)s), "
    "count(*) FILTER (WHERE m.close_time >= %(coverage_from)s "
    "  AND m.close_time <= %(watermark)s "
    "  AND NOT COALESCE(m.open_time >= %(coverage_from)s, FALSE)), "
    "count(*) FILTER (WHERE m.close_time >= %(coverage_from)s "
    "  AND m.close_time <= %(watermark)s "
    "  AND COALESCE(m.open_time >= %(coverage_from)s, FALSE)), "
    "count(*) "
    "{catalog}"
    "WHERE {ever} AND m.close_time < now()"
)


def read_trade_status(
    conn: psycopg.Connection[Any], rule: CollectionRule
) -> TradeStatus | None:
    """``None`` until the trades phase has run once (no ``sync_state`` row)."""
    state = conn.execute(STATE_QUERY, {"surface": Surface.TRADES.value}).fetchone()
    if state is None:
        return None
    last_phase_at, watermark, coverage_from, lag = state
    if watermark is None or coverage_from is None or lag is None:
        raise RuntimeError(
            "kalshi.sync_state['trades'] exists without a watermark or coverage "
            "floor; the row is written only by the trades phase's init_state"
        )
    ever = selection_sql(rule, "ever")
    statement = TRADE_COUNTS.format(catalog=CATALOG_JOIN, ever=ever.predicate)
    row = conn.execute(
        statement,
        {**ever.params, "coverage_from": coverage_from, "watermark": watermark},
    ).fetchone()
    if row is None:
        raise RuntimeError("the trade counts aggregate returned no row")
    before, short, partial, complete, total = (int(value) for value in row)
    if before + short + partial + complete != total:
        raise RuntimeError(
            f"trade status counts do not partition the selected closed markets: "
            f"{before} + {short} + {partial} + {complete} != {total}"
        )
    return TradeStatus(
        last_phase_at=last_phase_at,
        tape_through=watermark,
        lag=lag,
        behind=lag > TRADE_LAG_STALE_AFTER,
        coverage_from=coverage_from,
        complete_through_close=complete,
        partial_history=partial,
        short_of_close=short,
        before_coverage=before,
    )
