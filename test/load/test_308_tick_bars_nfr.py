"""Load test for the futures tick-bar routes (slice 308 D5, D9, D10).

The busiest real session (``busiest``, shared with 231): the archived
2024-12-18 ``tbbo`` day (889,373 records) ingested through the real pass. By
that day the volume had rolled to ESH5, which holds 672,277 of the records.
Every URL is passed through ``create_app(db_url=…,
tick_db_url=…)`` on test-cluster databases; this tier never reads a production
URL (``test_load_tier_never_references_prod_db_url``).

The product request uses ``rule=c.1``: one day is held, so ``v.0`` (ranked by
an earlier session's volume) cannot answer it, and ``c.1``, the second-nearest
expiration, names ESH5. It scans ESH5's ledger count for the session, about a
third of the 2,000,000 default trade limit, so it is a proxy: the bound at the
limit is checked only by the Phase 7 measurement on production (issue #75,
``scripts/measure_tick_bars.py``).

Gate (CI runs no test job; see slice 907):

    MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- \\
        test/load/test_308_tick_bars_nfr.py
"""

from __future__ import annotations

import asyncio
import math
import os
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import psycopg
import pytest

from load.busiest_day import BUSIEST_DAY
from manta_trading.api_server.app import create_app
from manta_trading.constants import API_MAX_TICK_BAR_TRADES

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(1800),
]

TICK_BARS = "/api/v1/futures/products/ES/tick-bars"
PRODUCTS = "/api/v1/futures/products"
#: ESH5's instrument id in the archived day (its definition file).
ESH5_ID = 5002
N = 1000
PARAMS = {
    "ticks_per_bar": str(N),
    "rule": "c.1",
    "start": BUSIEST_DAY.isoformat(),
    "end": "2024-12-19",
}
RUNS = 5
PROBES = 7

#: About 2× the median of five runs on the test cluster. 2026-11-12, 661,950
#: ESH5 trades in the session: 2.445, 2.485, 2.451, 2.435, 2.498 s; median
#: 2.451 s. Never above 10 s (306 D10).
TICK_BARS_BOUND_S = 5.0
#: A concurrent catalog request answers within this of its idle median (D9).
#: Measured on the test cluster, 2026-11-12: idle ``/products`` is 43-131 ms
#: (the probe itself varies by ~80 ms) and cutting adds 50-90 ms of GIL sharing
#: with the cutter thread (busy max 0.160-0.177 s over idle medians 0.066-0.095
#: s; max-of-7 against +0.1 s failed one run in four). Starvation would track
#: the multi-second cut, so 0.25 s separates it from that contention.
STARVATION_MARGIN_S = 0.25


@asynccontextmanager
async def _client(primary: str, tick: str) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client over the real lifespan (as 231's load test)."""
    app = create_app(db_url=primary, tick_db_url=tick)
    async with app.router.lifespan_context(app):
        await app.state.tick_pool.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://load-test",
            timeout=60,
        ) as client:
            yield client


def _ledger(url: str) -> tuple[int, int]:
    """ESH5's ledger record count and volume for the session."""
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT sum(record_count), sum(volume) FROM tick_ingest_ledger"
            " WHERE instrument_id = %s AND session_date = %s",
            (ESH5_ID, BUSIEST_DAY),
        ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0]), int(row[1])


async def _timed_get(
    client: httpx.AsyncClient, path: str, params: dict[str, str]
) -> tuple[float, httpx.Response]:
    started = time.perf_counter()
    response = await client.get(path, params=params)
    return time.perf_counter() - started, response


async def test_product_tick_bars_over_the_busiest_session(
    busiest: str, prod_shaped_db: str
) -> None:
    """Bound: see TICK_BARS_BOUND_S."""
    records, volume = _ledger(busiest)
    assert records < API_MAX_TICK_BAR_TRADES
    samples = []
    async with _client(prod_shaped_db, busiest) as client:
        for _ in range(RUNS):
            elapsed, response = await _timed_get(client, TICK_BARS, PARAMS)
            assert response.status_code == 200, response.text[:300]
            samples.append(elapsed)
    body = response.json()
    median = statistics.median(samples)
    shown = ", ".join(f"{s:.3f}" for s in samples)
    print(
        f"\ntick bars N={N} over {records:,} trades: median {median:.3f} s"
        f" (runs {shown}); bound {TICK_BARS_BOUND_S:.3f} s"
    )
    bars = body["bars"]
    assert {b["raw_symbol"] for b in bars} == {"ESH5"}
    assert sum(b["trade_count"] for b in bars) == records
    assert sum(b["volume"] for b in bars) == volume
    assert body["count"] == len(bars) == math.ceil(records / N)
    assert median < TICK_BARS_BOUND_S


async def test_the_catalog_is_not_starved_while_cutting(
    busiest: str, prod_shaped_db: str
) -> None:
    """``/futures/products`` during a tick-bar request answers within
    STARVATION_MARGIN_S of its idle median: the loop is not starved (D9)."""
    async with _client(prod_shaped_db, busiest) as client:
        idle = []
        for _ in range(PROBES):
            elapsed, response = await _timed_get(client, PRODUCTS, {})
            assert response.status_code == 200
            idle.append(elapsed)
        idle_median = statistics.median(idle)

        cutting = asyncio.create_task(client.get(TICK_BARS, params=PARAMS))
        await asyncio.sleep(0.2)  # let the read get past planning and admission
        busy: list[float] = []
        while not cutting.done() and len(busy) < PROBES:
            elapsed, response = await _timed_get(client, PRODUCTS, {})
            assert response.status_code == 200
            busy.append(elapsed)
        overlapped = not cutting.done()
        assert (await cutting).status_code == 200
    print(
        f"\nproducts idle median {idle_median:.3f} s {[round(s, 3) for s in idle]};"
        f" during tick bars {[round(s, 3) for s in busy]}"
        f" (request still running after probes: {overlapped})"
    )
    assert len(busy) >= 3, "the tick-bar request finished before three probes"
    assert max(busy) <= idle_median + STARVATION_MARGIN_S
