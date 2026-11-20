"""Load test for session-aligned bars (slice 174 task 13.1).

A year of dense SPY minutes (every minute 04:00-20:00 ET of every NYSE session,
2025-11-13..2026-11-13) on a test-cluster database, read through
``create_app`` over ``httpx.ASGITransport`` with the shipped ceilings. The
one-year ``1h`` ``regular`` read must be admitted with p95 under 2 s (the
20 s statement timeout is the hard limit), and ``/api/v1/health`` p95 must
stay under 0.5 s while it runs. This tier never reads a production URL.

Gate (CI runs no test job; issue #32 and slice 907 own that, so the gate is
manual until then):

    MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- \\
        test/load/test_174_session_bars_nfr.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, timedelta

import httpx
import psycopg
import pytest
from psycopg_pool import ConnectionPool

from manta_trading.api_server.app import create_app
from manta_trading.constants import SessionMode
from manta_trading.market.schema.migrations.minute import MINUTE_MIGRATIONS
from manta_trading.market.schema.runner import apply_migrations
from manta_trading.market.session_bars import session_bounds
from manta_trading.market.session_phases import estimate_bars

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(1800),
]

START, END = date(2025, 11, 13), date(2026, 11, 13)
READ = f"/api/v1/bars/SPY?granularity=1h&start={START}&end={END}&session=regular"
HEALTH = "/api/v1/health"
RUNS = 20
READ_P95_BOUND_S = 2.0
HEALTH_P95_BOUND_S = 0.5
HEALTH_EVERY_S = 0.1


def p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20, method="inclusive")[-1]


@pytest.fixture
def year_of_spy(ephemeral_db: str) -> str:
    with ConnectionPool(ephemeral_db, min_size=1, max_size=2) as pool:
        apply_migrations(pool, MINUTE_MIGRATIONS)
    with psycopg.connect(ephemeral_db) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange) VALUES "
            "('SPY.US', 'SPY', 'etf', 'US', 'NYSE', 'ETF', 'NYSE ARCA')"
        )
        sessions = conn.execute(
            "SELECT extended_open_utc, extended_close_utc FROM trading_sessions "
            "WHERE calendar_id = 'NYSE' AND session_date BETWEEN %s AND %s",
            (START, END),
        ).fetchall()
        with conn.cursor() as cur:
            with cur.copy(
                "COPY minute_ohlcv (time, symbol, open, high, low, close, volume) "
                "FROM STDIN"
            ) as copy:
                for ext_open, ext_close in sessions:
                    minutes = int((ext_close - ext_open) / timedelta(minutes=1))
                    for i in range(minutes):
                        price = 500 + (i % 50) * 0.01
                        copy.write_row(
                            (ext_open + timedelta(minutes=i), "SPY",
                             price, price + 0.05, price - 0.05, price, 100 + i)
                        )  # fmt: skip
    return ephemeral_db


def _expected_bars(url: str) -> int:
    """Every regular 1h bar of the year: the seed is dense, so the estimator's
    count is exact (7 per full session, 4 per early close)."""
    with psycopg.connect(url) as conn:
        bounds = session_bounds(conn, "NYSE", START, END, SessionMode.REGULAR)
    return estimate_bars(bounds, SessionMode.REGULAR, timedelta(hours=1))


@asynccontextmanager
async def _client(url: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(db_url=url, tick_db_url=None)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://load-test",
            timeout=60,
        ) as client:
            yield client


async def _timed(client: httpx.AsyncClient, url: str) -> tuple[float, httpx.Response]:
    started = time.perf_counter()
    response = await client.get(url)
    return time.perf_counter() - started, response


async def test_one_year_regular_1h_with_health(year_of_spy: str) -> None:
    reads: list[float] = []
    health: list[float] = []
    done = asyncio.Event()

    async with _client(year_of_spy) as client:

        async def poll_health() -> None:
            while not done.is_set():
                elapsed, response = await _timed(client, HEALTH)
                assert response.status_code == 200, response.text[:300]
                health.append(elapsed)
                await asyncio.sleep(HEALTH_EVERY_S)

        poller = asyncio.create_task(poll_health())
        try:
            for _ in range(RUNS):
                elapsed, response = await _timed(client, READ)
                assert response.status_code == 200, response.text[:300]
                reads.append(elapsed)
        finally:
            done.set()
            await poller

    body = response.json()
    print(
        f"\nsession bars 1y regular 1h: {body['count']:,} bars;"
        f" read p95 {p95(reads):.3f} s (median {statistics.median(reads):.3f},"
        f" max {max(reads):.3f}); health p95 {p95(health):.3f} s over"
        f" {len(health)} polls"
    )
    assert body["count"] == _expected_bars(year_of_spy)
    assert p95(reads) < READ_P95_BOUND_S
    assert p95(health) < HEALTH_P95_BOUND_S
