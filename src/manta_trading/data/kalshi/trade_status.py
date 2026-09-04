"""The trades block of ``mt data kalshi status`` (slice 265, Decision 10).

Read-only, synchronous psycopg — one short read, the ``status.py`` pattern.
Every figure is a persisted fact: the state row (``sync_state['trades']``)
and the catalog join under the collection rule's ``"ever"`` form. **Nothing
counts rows in ``kalshi.trades``** (journal 20260720); per-market
completeness is derived from the single watermark and the coverage floor.
Neither the client nor the transport is imported (Criterion 11).

Since slice 268 a fifth count, ``tape_filtered_markets``, covers the
rule-selected closed markets the trades filter keeps off the tape; the four
buckets below cover the rest, and all five together partition the selected
closed markets. Within the unfiltered four, precedence — a market is counted
once, by the first that applies:

1. ``before_coverage`` — closed before the **effective floor**: the live
   floor, or the historical watermark once the historical phase (267) has
   walked below it — ``min(trades.coverage_from_ts,
   historical.watermark_ts)`` (267 Decision 8), so the bucket shrinks as
   the backfill descends;
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
    trades_filter_sql,
)
from manta_trading.data.kalshi.sync_types import iso_utc


@dataclass(frozen=True)
class TradeStatus:
    """The trades block (design *CLI and rendering*).

    Slice 268: the four closed-market buckets cover rule-selected markets
    **not** tape-filtered; ``tape_filtered_markets`` counts the filtered
    closed markets (markets, never trade rows), and the partition check
    includes it. ``excluded_categories`` is the filter in force — the same
    ``Settings`` value the pass reads — so text and JSON render from here.
    """

    last_phase_at: datetime | None
    tape_through: datetime
    lag: timedelta
    behind: bool
    coverage_from: datetime
    complete_through_close: int
    partial_history: int
    short_of_close: int
    before_coverage: int
    excluded_categories: frozenset[str]
    tape_filtered_markets: int

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
            "filter": {
                "excluded_categories": sorted(self.excluded_categories),
                "tape_filtered_markets": self.tape_filtered_markets,
            },
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
#: NULL ``open_time`` in *partial*, never in none. Slice 268: the inner
#: select flags each selected closed market ``tape_filtered`` (the
#: ``trades_filter_sql`` membership test — never re-spelled either); the four
#: buckets cover the unfiltered, a fifth count the filtered, and together
#: they still partition the total.
TRADE_COUNTS = sql.SQL(
    "SELECT "
    "count(*) FILTER (WHERE NOT tape_filtered AND close_time < %(coverage_from)s), "
    "count(*) FILTER (WHERE NOT tape_filtered "
    "  AND close_time >= %(coverage_from)s "
    "  AND close_time > %(watermark)s), "
    "count(*) FILTER (WHERE NOT tape_filtered "
    "  AND close_time >= %(coverage_from)s "
    "  AND close_time <= %(watermark)s "
    "  AND NOT COALESCE(open_time >= %(coverage_from)s, FALSE)), "
    "count(*) FILTER (WHERE NOT tape_filtered "
    "  AND close_time >= %(coverage_from)s "
    "  AND close_time <= %(watermark)s "
    "  AND COALESCE(open_time >= %(coverage_from)s, FALSE)), "
    "count(*) FILTER (WHERE tape_filtered), "
    "count(*) "
    "FROM (SELECT m.close_time, m.open_time, {tape_test} AS tape_filtered "
    "{catalog}"
    "WHERE {ever} AND m.close_time < now()) closed"
)


def _effective_floor(conn: psycopg.Connection[Any], live_floor: datetime) -> datetime:
    """267 Decision 8: the lower of the live floor and the historical row's
    watermark (the oldest hour the backfill has fully walked); the live floor
    alone until the historical row exists with a watermark."""
    row = conn.execute(STATE_QUERY, {"surface": Surface.HISTORICAL.value}).fetchone()
    if row is None or row[1] is None:
        return live_floor
    return min(live_floor, row[1])


def read_trade_status(
    conn: psycopg.Connection[Any],
    rule: CollectionRule,
    trades_excluded: frozenset[str],
) -> TradeStatus | None:
    """``None`` until the trades phase has run once (no ``sync_state`` row).

    ``trades_excluded`` comes from the same ``Settings`` the pass reads (the
    264 Decision 2 invariant), so filtering and reporting cannot disagree.
    """
    state = conn.execute(STATE_QUERY, {"surface": Surface.TRADES.value}).fetchone()
    if state is None:
        return None
    last_phase_at, watermark, live_floor, lag = state
    if watermark is None or live_floor is None or lag is None:
        raise RuntimeError(
            "kalshi.sync_state['trades'] exists without a watermark or coverage "
            "floor; the row is written only by the trades phase's init_state"
        )
    coverage_from = _effective_floor(conn, live_floor)
    ever = selection_sql(rule, "ever")
    trades_filter = trades_filter_sql(trades_excluded)
    statement = TRADE_COUNTS.format(
        catalog=CATALOG_JOIN, ever=ever.predicate, tape_test=trades_filter.predicate
    )
    row = conn.execute(
        statement,
        {
            **ever.params,
            **trades_filter.params,
            "coverage_from": coverage_from,
            "watermark": watermark,
        },
    ).fetchone()
    if row is None:
        raise RuntimeError("the trade counts aggregate returned no row")
    before, short, partial, complete, tape_filtered, total = (
        int(value) for value in row
    )
    if before + short + partial + complete + tape_filtered != total:
        raise RuntimeError(
            f"trade status counts do not partition the selected closed markets: "
            f"{before} + {short} + {partial} + {complete} + {tape_filtered} "
            f"!= {total}"
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
        excluded_categories=trades_excluded,
        tape_filtered_markets=tape_filtered,
    )
