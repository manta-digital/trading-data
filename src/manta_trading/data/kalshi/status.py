"""Read-only queries behind ``mt data kalshi status`` (slices 262 and 264).

Synchronous psycopg — one short read, the ``mt data status`` pattern. No
API call, and (Criterion 12) neither the client nor the transport is
imported. Bucket edges, the stuck threshold, and the lag horizon are bound
parameters from ``constants`` so the report and the constant can never
disagree; the candle block's rule-dependent counts embed
``candle_selection.selection_sql`` so collection and reporting cannot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.candle_plan import period_span
from manta_trading.data.kalshi.candle_selection import (
    BACKLOG_CONDITION,
    BEHIND_CUTOFF_CONDITION,
    MARKET_JOIN,
    selection_sql,
)
from manta_trading.data.kalshi.candle_types import CandleRule
from manta_trading.data.kalshi.constants import (
    AWAITING_AGE_BUCKETS,
    CANDLE_LAG_STALE_AFTER,
    COLLECTED_CANDLE_PERIOD,
    KALSHI_SETTLEMENT_STUCK_AFTER,
    MarketStatus,
    Surface,
)


@dataclass(frozen=True)
class AwaitingStatus:
    total: int
    #: Counts per age bucket, oldest last: one entry per edge in
    #: ``AWAITING_AGE_BUCKETS`` plus the open-ended tail.
    age_histogram: tuple[int, ...]
    past_threshold: int
    oldest_ticker: str | None
    oldest_age: timedelta | None
    checked_directly: int


@dataclass(frozen=True)
class CatalogStatus:
    last_full_sync_at: datetime | None
    watermark_ts: datetime | None
    series: int
    events: int
    markets_by_status: dict[MarketStatus, int]
    awaiting: AwaitingStatus

    def to_dict(self) -> dict[str, Any]:
        labels = age_bucket_labels()
        return {
            "last_full_sync_at": _iso(self.last_full_sync_at),
            "watermark_ts": _iso(self.watermark_ts),
            "series": self.series,
            "events": self.events,
            "markets_by_status": {
                s.value: n for s, n in self.markets_by_status.items()
            },
            "awaiting_total": self.awaiting.total,
            "awaiting_age": dict(zip(labels, self.awaiting.age_histogram, strict=True)),
            "awaiting_past_threshold": self.awaiting.past_threshold,
            "awaiting_oldest_ticker": self.awaiting.oldest_ticker,
            "awaiting_oldest_age_days": (
                self.awaiting.oldest_age.days if self.awaiting.oldest_age else None
            ),
            "awaiting_checked_directly": self.awaiting.checked_directly,
            "stuck_threshold_days": KALSHI_SETTLEMENT_STUCK_AFTER.days,
        }


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value else None


def age_bucket_labels() -> list[str]:
    """``["<1d", "1-7d", "7-30d", ">30d"]`` for the default edges."""
    edges = [f"{b.days}d" for b in AWAITING_AGE_BUCKETS]
    labels = [f"<{edges[0]}"]
    labels += [f"{lo}-{hi}" for lo, hi in zip(edges, edges[1:], strict=False)]
    labels.append(f">{edges[-1]}")
    return labels


def read_catalog_status(conn: psycopg.Connection[Any]) -> CatalogStatus | None:
    """``None`` when the catalog has never synced (no ``sync_state`` row)."""
    state = conn.execute(
        "SELECT last_full_sync_at, watermark_ts FROM kalshi.sync_state "
        "WHERE surface = %s",
        (Surface.CATALOG.value,),
    ).fetchone()
    if state is None:
        return None
    series = _scalar(conn, "SELECT count(*) FROM kalshi.series")
    events = _scalar(conn, "SELECT count(*) FROM kalshi.events")
    by_status = {s: 0 for s in MarketStatus}
    for status, count in conn.execute(
        "SELECT status, count(*) FROM kalshi.markets GROUP BY status"
    ).fetchall():
        by_status[MarketStatus(status)] = count
    return CatalogStatus(
        last_full_sync_at=state[0],
        watermark_ts=state[1],
        series=series,
        events=events,
        markets_by_status=by_status,
        awaiting=_read_awaiting(conn),
    )


def _scalar(conn: psycopg.Connection[Any], query: LiteralString) -> int:
    row = conn.execute(query).fetchone()
    return int(row[0]) if row else 0


def _read_awaiting(conn: psycopg.Connection[Any]) -> AwaitingStatus:
    edges = list(AWAITING_AGE_BUCKETS)
    # One SELECT: a count per bucket (age in [edge_{i-1}, edge_i)), the
    # open-ended tail, the past-threshold count, and the checked count.
    bucket = sql.SQL(
        "count(*) FILTER "
        "(WHERE now() - close_time >= {lo} AND now() - close_time < {hi})"
    )
    buckets = sql.SQL(", ").join(
        bucket.format(lo=sql.Placeholder(f"lo{i}"), hi=sql.Placeholder(f"hi{i}"))
        for i in range(len(edges))
    )
    params: dict[str, Any] = {"tail": edges[-1], "stuck": KALSHI_SETTLEMENT_STUCK_AFTER}
    for i, edge in enumerate(edges):
        params[f"lo{i}"] = edges[i - 1] if i else timedelta(0)
        params[f"hi{i}"] = edge
    row = conn.execute(
        sql.SQL(
            "SELECT count(*), {buckets}, "
            "count(*) FILTER (WHERE now() - close_time >= %(tail)s), "
            "count(*) FILTER (WHERE now() - close_time >= %(stuck)s), "
            "count(*) FILTER (WHERE last_checked_at IS NOT NULL) "
            "FROM kalshi.awaiting_settlement"
        ).format(buckets=buckets),
        params,
    ).fetchone()
    assert row is not None  # an aggregate query always returns one row
    total, *rest = row
    histogram = tuple(int(n) for n in rest[: len(edges) + 1])
    past_threshold, checked = int(rest[len(edges) + 1]), int(rest[len(edges) + 2])
    oldest = conn.execute(
        "SELECT market_ticker, now() - close_time FROM kalshi.awaiting_settlement "
        "ORDER BY close_time ASC LIMIT 1"
    ).fetchone()
    return AwaitingStatus(
        total=int(total),
        age_histogram=histogram,
        past_threshold=past_threshold,
        oldest_ticker=oldest[0] if oldest else None,
        oldest_age=oldest[1] if oldest else None,
        checked_directly=checked,
    )


# ---------------------------------------------------------------------------
# Candlesticks (slice 264, Decision 11): persisted facts only, no API call
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandleStatus:
    """The candle block of ``mt data kalshi status`` (design *CLI and
    rendering*). Every field is read from the database; nothing counts rows
    in ``kalshi.candlesticks``."""

    period_minutes: int
    last_phase_at: datetime | None
    cutoff_observed: datetime | None
    rule: CandleRule
    selected_open: int
    markets_tracked: int
    open_lagging: int
    open_oldest_watermark: datetime | None
    complete_through_close: int
    closed_short_of_close: int
    backlog_remaining: int
    behind_cutoff_uncollected: int
    closed_excluded_by_rule: int
    partial_history: int

    def to_dict(self) -> dict[str, Any]:
        rule = self.rule
        return {
            "period_minutes": self.period_minutes,
            "last_phase_at": _iso(self.last_phase_at),
            "cutoff_observed": _iso(self.cutoff_observed),
            "rule": {
                "traded_only": rule.traded_only,
                "categories": sorted(rule.categories),
                "excluded_categories": sorted(rule.excluded_categories),
                "excluded_series_pattern": rule.excluded_series_pattern,
                "excluded_title_pattern": rule.excluded_title_pattern,
                "description": rule.describe(),
            },
            "selected_open": self.selected_open,
            "markets_tracked": self.markets_tracked,
            "open_lagging": self.open_lagging,
            "open_oldest_watermark": _iso(self.open_oldest_watermark),
            "complete_through_close": self.complete_through_close,
            "closed_short_of_close": self.closed_short_of_close,
            "backlog_remaining": self.backlog_remaining,
            "behind_cutoff_uncollected": self.behind_cutoff_uncollected,
            "closed_excluded_by_rule": self.closed_excluded_by_rule,
            "partial_history": self.partial_history,
        }


#: One statement, one scan of the join: every rule-dependent figure embeds
#: ``selection_sql`` (never re-spelled), the cutoff comes from ``sync_state``.
#: ``NOT COALESCE(pred, FALSE)`` counts *every* unselected closed market as
#: excluded, including one an allow-list leaves NULL on a NULL category.
_CANDLE_COUNTS = sql.SQL(
    "SELECT "
    "count(*) FILTER (WHERE {recent} AND m.status <> %(finalized)s "
    "  AND m.open_time IS NOT NULL AND m.open_time < now()), "
    "count(*) FILTER (WHERE st.market_ticker IS NOT NULL), "
    "count(*) FILTER (WHERE {lagging}), "
    "min(st.watermark_ts) FILTER (WHERE {lagging}), "
    "count(*) FILTER (WHERE st.watermark_ts >= m.close_time + %(span)s), "
    "count(*) FILTER (WHERE st.market_ticker IS NOT NULL AND m.close_time < now() "
    "  AND (m.settlement_ts IS NULL OR m.settlement_ts >= %(cutoff)s) "
    "  AND st.watermark_ts < m.close_time + %(span)s), "
    "count(*) FILTER (WHERE {ever} AND {backlog}), "
    "count(*) FILTER (WHERE {ever} AND {behind}), "
    "count(*) FILTER (WHERE st.market_ticker IS NULL AND m.close_time < now() "
    "  AND NOT COALESCE({ever}, FALSE)), "
    "count(*) FILTER (WHERE st.coverage_from_ts > m.open_time) "
)
#: Tracked, still open, still selected, short of its close, and two firings
#: behind — a deselected market is idle, not lagging, and a market already
#: complete through ``close_time + period`` has nothing left to fetch (seen
#: in the 2026-08-27 rehearsal: markets closed hours earlier and awaiting
#: determination were counted as lagging).
_LAGGING = sql.SQL(
    "st.market_ticker IS NOT NULL AND m.status <> %(finalized)s AND {recent} "
    "AND st.watermark_ts < m.close_time + %(span)s "
    "AND st.watermark_ts < now() - %(stale)s"
)


def read_candle_status(
    conn: psycopg.Connection[Any], rule: CandleRule
) -> CandleStatus | None:
    """``None`` until the candle phase has run once (no ``sync_state`` row)."""
    state = conn.execute(
        "SELECT last_full_sync_at, watermark_ts FROM kalshi.sync_state "
        "WHERE surface = %s",
        (Surface.CANDLESTICKS.value,),
    ).fetchone()
    if state is None:
        return None
    last_phase_at, cutoff = state
    recent, ever = selection_sql(rule, "recent"), selection_sql(rule, "ever")
    lagging = _LAGGING.format(recent=recent.predicate)
    statement = sql.Composed(
        [
            _CANDLE_COUNTS.format(
                recent=recent.predicate,
                lagging=lagging,
                ever=ever.predicate,
                backlog=BACKLOG_CONDITION,
                behind=BEHIND_CUTOFF_CONDITION,
            ),
            MARKET_JOIN,
        ]
    )
    row = conn.execute(
        statement,
        {
            **recent.params,
            **ever.params,
            "period": int(COLLECTED_CANDLE_PERIOD),
            "finalized": MarketStatus.FINALIZED.value,
            "span": period_span(COLLECTED_CANDLE_PERIOD),
            "stale": CANDLE_LAG_STALE_AFTER,
            "cutoff": cutoff,
        },
    ).fetchone()
    assert row is not None  # an aggregate query always returns one row
    return CandleStatus(
        period_minutes=int(COLLECTED_CANDLE_PERIOD),
        last_phase_at=last_phase_at,
        cutoff_observed=cutoff,
        rule=rule,
        selected_open=int(row[0]),
        markets_tracked=int(row[1]),
        open_lagging=int(row[2]),
        open_oldest_watermark=row[3],
        complete_through_close=int(row[4]),
        closed_short_of_close=int(row[5]),
        backlog_remaining=int(row[6]),
        behind_cutoff_uncollected=int(row[7]),
        closed_excluded_by_rule=int(row[8]),
        partial_history=int(row[9]),
    )
