"""Read-only queries behind ``mt data kalshi status`` (slice 262).

Synchronous psycopg — one short read, the ``mt data status`` pattern. No
API call. Bucket edges and the stuck threshold are bound parameters from
``constants`` so the report and the constant can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString

import psycopg
from psycopg import sql

from manta_trading.data.kalshi.constants import (
    AWAITING_AGE_BUCKETS,
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
