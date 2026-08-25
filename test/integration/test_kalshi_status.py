"""Integration tests: ``read_catalog_status`` on a throwaway database (slice 262).

Seeds through the Section 3 repository so rows are real served shapes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
from kalshi_helpers import market_rows, parent_events, parent_series

from manta_trading.data.kalshi.constants import MarketStatus, Surface
from manta_trading.data.kalshi.repository import CatalogRepository
from manta_trading.data.kalshi.status import age_bucket_labels, read_catalog_status


def test_never_synced_is_none(kalshi_db: str):
    with psycopg.connect(kalshi_db) as conn:
        assert read_catalog_status(conn) is None


async def test_histogram_threshold_and_oldest(
    kalshi_db: str, kalshi_conn: psycopg.AsyncConnection[Any]
):
    now = datetime.now(UTC)
    ages = {"HALF": 0.5, "THREE": 3, "TEN": 10, "FORTY": 40}
    base = market_rows("markets_open")[0]
    markets = [
        base.model_copy(
            update={
                "ticker": ticker,
                "status": MarketStatus.CLOSED.value,
                "close_time": now - timedelta(days=days),
            }
        )
        for ticker, days in ages.items()
    ]
    repo = CatalogRepository(kalshi_conn)
    async with repo.transaction():
        events = parent_events(markets)
        await repo.upsert_series(parent_series(events))
        await repo.upsert_events(events)
        await repo.upsert_markets(markets)
        await repo.enter_awaiting(now)
        await repo.mark_checked(["TEN"], now)
        await repo.set_last_full_sync(Surface.CATALOG, now)

    with psycopg.connect(kalshi_db) as sync_conn:
        status = read_catalog_status(sync_conn)
    assert status is not None
    assert status.last_full_sync_at == now and status.watermark_ts is None
    assert status.series == 1 and status.events == 1
    assert status.markets_by_status[MarketStatus.CLOSED] == 4
    assert status.markets_by_status[MarketStatus.ACTIVE] == 0
    assert status.awaiting.total == 4
    assert status.awaiting.age_histogram == (1, 1, 1, 1)
    assert status.awaiting.past_threshold == 2
    assert status.awaiting.oldest_ticker == "FORTY"
    assert (
        status.awaiting.oldest_age is not None and status.awaiting.oldest_age.days == 40
    )
    assert status.awaiting.checked_directly == 1
    payload = status.to_dict()
    assert age_bucket_labels() == ["<1d", "1d-7d", "7d-30d", ">30d"]
    assert payload["awaiting_age"] == {"<1d": 1, "1d-7d": 1, "7d-30d": 1, ">30d": 1}
    assert payload["awaiting_past_threshold"] == 2
    assert payload["awaiting_oldest_age_days"] == 40
    assert payload["stuck_threshold_days"] == 7
