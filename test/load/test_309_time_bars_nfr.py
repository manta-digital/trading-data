"""Load test for the futures time-bar routes (slice 309, "Cost").

The busiest real session (``busiest``, shared with 231 and 308): the archived
2024-12-18 ``tbbo`` day ingested through the real pass, of which ESH5 holds
about 670,000 trades. No provider bars are held, so every bar is cut from
ticks. Every URL is passed through ``create_app(db_url=…, tick_db_url=…)`` on
test-cluster databases; this tier never reads a production URL
(``test_load_tier_never_references_prod_db_url``).

The product request uses ``rule=c.1`` (one day is held, so ``v.0`` cannot
answer it; ``c.1`` names ESH5) at ``15s``: 4,919 bars. ``1s`` cannot
serve here: a full session is about 82,800 bars, over the 75,000-row ceiling,
and is read in pieces with ``from``/``until``. The bound at the trade limit
was measured on production (``user/notes/309-time-bar-measurement.md``).

Gate (CI runs no test job; issue #32 and slice 907 own that, so the gate is
manual until then):

    MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- \\
        test/load/test_309_time_bars_nfr.py
"""

from __future__ import annotations

import os
import statistics
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import psycopg
import pytest
from tick_support.measure_script import measure_script

from load.busiest_day import BUSIEST_DAY
from manta_trading.api_server.app import create_app
from manta_trading.constants import API_MAX_TIME_BAR_TRADES

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(1800),
]

#: The probing and the starvation check are the 308 script's
#: (``scripts/measure_tick_bars.py``), so the routes are judged alike.
SCRIPT = measure_script()
TIME_BARS = "/api/v1/futures/products/ES/bars"
#: ESH5's instrument id in the archived day (its definition file).
ESH5_ID = 5002
PARAMS = {
    "granularity": "15s",
    "rule": "c.1",
    "start": BUSIEST_DAY.isoformat(),
    "end": "2024-12-19",
}
RUNS = 5

#: About 2× the median of five runs on the test cluster. 2026-11-15, 661,950
#: ESH5 trades cut into 4,919 bars: 0.508, 0.427, 0.580, 0.485, 0.444 s; median
#: 0.485 s. The 308 script's ``BOUND_S`` (10 s) is the bound at the trade limit,
#: and this one may never exceed it.
TIME_BARS_BOUND_S = 1.0
assert TIME_BARS_BOUND_S <= SCRIPT.BOUND_S


@asynccontextmanager
async def _client(primary: str, tick: str) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client over the real lifespan (as 231's and 308's load tests)."""
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


async def test_product_15s_bars_over_the_busiest_session(
    busiest: str, prod_shaped_db: str
) -> None:
    """Bound: see TIME_BARS_BOUND_S."""
    records, volume = _ledger(busiest)
    assert records < API_MAX_TIME_BAR_TRADES
    samples = []
    async with _client(prod_shaped_db, busiest) as client:
        for _ in range(RUNS):
            elapsed, response = await SCRIPT.timed(client, TIME_BARS, PARAMS)
            assert response.status_code == 200, response.text[:300]
            samples.append(elapsed)
    body = response.json()
    median = statistics.median(samples)
    shown = ", ".join(f"{s:.3f}" for s in samples)
    print(
        f"\ntime bars 15s over {records:,} trades, {body['count']:,} bars: median"
        f" {median:.3f} s (runs {shown}); bound {TIME_BARS_BOUND_S:.3f} s"
    )
    bars = body["bars"]
    assert {b["raw_symbol"] for b in bars} == {"ESH5"}
    assert [run["source"] for run in body["sources"]] == ["ticks"]
    assert sum(b["volume"] for b in bars) == volume
    assert median < TIME_BARS_BOUND_S


async def test_the_catalog_is_not_starved_while_cutting(
    busiest: str, prod_shaped_db: str
) -> None:
    """``/futures/products`` during a time-bar request answers within the 308
    script's ``STARVATION_MARGIN_S`` of its idle median: the cut and the model
    build run off the loop."""
    async with _client(prod_shaped_db, busiest) as client:
        timing, response = await SCRIPT.measure(client, TIME_BARS, PARAMS)
    assert response.status_code == 200, response.text[:300]
    print(
        f"\nproducts idle {[round(s, 3) for s in timing.idle]};"
        f" during time bars {[round(s, 3) for s in timing.busy]}"
    )
    assert SCRIPT.starvation(timing) == []
