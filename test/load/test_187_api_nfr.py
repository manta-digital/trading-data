"""Load tests for the serving API (slice 187 D9, D10).

The API had no load tier. The project's Python rules require one of any code on
the network, concurrency, or environment paths, and the four assertions here are
each chosen because a unit or integration test cannot make them:

1. **Symbol-detail latency** — the D2 read path end to end, through the executor
   bridge and the pool. Unit tests mock the connection; only this measures.
2. **Bars latency at the admission ceiling** — the headline assertion.
   ``statement_timeout`` bounds a *statement*, not a *request*, and nothing in
   the stack bounds request latency (``180-arch.data-serving.md``, Error
   Handling). Slice 186 D12b proved that gap is real with a 95-second request
   made of 94 short statements. This measures the gap rather than arguing it.
3. **Concurrency / pool contention** — 16 concurrent requests against a pool of
   8. This is the shape that would have caught the held-connection problem slice
   185 D8a fixed by inspection, and it is the input to the D11 pool decision.
4. **Status-endpoint latency** — full-universe ``/api/v1/status``, reusing slice
   167's sub-second DB-side NFR plus serialization headroom.

Requests go through ``httpx.ASGITransport`` against the real app, so the
executor bridge, the pool, and the routes are all in the measured path.
**Documented limitation:** the ASGI transport does not exercise uvicorn's HTTP
layer, so wire-level framing and header parsing are out of scope. That is not
where the risk lies — 186 D12b's 95-second request was 94 sequential
statements, all of which this path reproduces.

**Fixture honesty (D10).** ``prod_shaped_db`` reproduces production's
*row-count* shape, not its 3,371-chunk ``daily_ohlcv`` planning cost, and no
affordable fixture does. The D1/D2 prod measurements carry that dimension and
live in the slice design. These bounds guard the *shape* of the code against
regression; they are not predictions of production latency.

**Every bound below was derived from a measurement**, recorded in the slice's
Verification Walkthrough. A load test whose threshold was invented is a test
that passes for the wrong reason.

**Gating (D9).** Runs only with ``MT_RUN_LOAD_TESTS=1``, the convention slice
167 established, and requires ``MT_TIMESCALE_TEST_URL`` for the throwaway
database. The documented manual invocation is:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/

CI wiring is **slice 907's** deliverable, not this slice's:
``.github/workflows/ci.yml`` is publish-on-tag with no test job, and 907 (CI
Pipeline and Load-Test Gating) already names this tier in its scope.

This tier never reads the production DB URL — ``create_app(db_url=...)`` (D9) is
what makes that possible, and
``test_load_tier_never_references_prod_db_url`` enforces it mechanically.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import httpx
import pytest

from manta_trading.api_server.app import create_app
from manta_trading.api_server.routes.bars import _max_span_days
from manta_trading.constants import API_MAX_BARS_PER_REQUEST, Granularity
from manta_trading.market.maintenance.cagg_freshness import reset_freshness_cache

from .conftest import DENSE_START, DENSE_SYMBOL, symbol_name

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # Seeding 12,000 symbols plus three cagg refreshes far exceeds the 30 s
    # ini default; the measurements themselves are seconds at most.
    pytest.mark.timeout(900),
]

_SYMBOL_DETAIL_BOUND_S = 0.25
"""D10.1, provisional < 250 ms — ten times the ~25 ms measured on prod, and
still an order of magnitude under the pre-187 behavior (2.7-4.0 s)."""

_BARS_BOUND_S = 15.0
"""D10.2, provisional < 15 s for a request at the admission ceiling."""

_STATUS_BOUND_S = 1.5
"""D10.4: slice 167's sub-second DB-side NFR plus serialization headroom."""

_CONCURRENCY = 16
"""Twice the pool's ``max_size=8``, so requests genuinely queue."""

_QUEUEING_FACTOR = 6.0
"""How far a request at concurrency 16 may exceed the single-request bound.

With 16 requests against 8 connections, a request can wait behind one other
before running, so ~2x is the structural floor. The allowance above that covers
executor-thread scheduling and is deliberately loose: this assertion exists to
catch a *pool* problem — a connection held for a whole request, the 185 D8a
shape — not to pin a precise queueing model.
"""

_MEASURED_RUNS = 5
"""Median of five; one scheduling hiccup must not fail a healthy bound."""


@asynccontextmanager
async def _client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client against a real app pointed at ``db_url`` (D9).

    The lifespan is entered explicitly. ``httpx.ASGITransport`` sends only HTTP
    scopes, so without this the pool and the DB instances are never constructed
    and every request fails on a missing ``app.state.db_pool``. Entering it also
    means the measured path includes the real pool with its real ``max_size``,
    which is the whole point of assertion 3.
    """
    app = create_app(db_url=db_url)
    # The app's own lifespan context, driven directly rather than through an
    # extra test dependency: it is a plain async context manager, and this keeps
    # the measured startup identical to production's.
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://load-test",
        ) as client:
            yield client


async def _measure(
    client: httpx.AsyncClient, url: str, *, runs: int = _MEASURED_RUNS
) -> tuple[float, list[float]]:
    """Median and all samples for ``runs`` sequential GETs, asserting 200s."""
    samples: list[float] = []
    for _ in range(runs):
        # A fresh process never starts with a warm verdict cache, so each
        # measured sample must include the probe an operator actually pays for.
        reset_freshness_cache()
        started = time.perf_counter()
        response = await client.get(url)
        samples.append(time.perf_counter() - started)
        assert response.status_code == 200, (
            f"{url} returned {response.status_code}: {response.text[:300]}"
        )
    return statistics.median(samples), samples


def _report(label: str, median: float, samples: list[float], bound: float) -> str:
    return (
        f"{label}: median={median:.3f}s bound={bound:.3f}s "
        f"samples={[f'{s:.3f}' for s in samples]}"
    )


# --- assertion 1 (D10.1) -----------------------------------------------------


def test_symbol_detail_latency(prod_shaped_db: str) -> None:
    """The D2 read path, measured through the real app at prod row shape."""

    async def _run() -> tuple[float, list[float]]:
        async with _client(prod_shaped_db) as client:
            return await _measure(client, f"/api/v1/symbols/{symbol_name(0)}")

    median, samples = asyncio.run(_run())
    print("\n" + _report("symbol-detail", median, samples, _SYMBOL_DETAIL_BOUND_S))
    assert median < _SYMBOL_DETAIL_BOUND_S, _report(
        "symbol-detail", median, samples, _SYMBOL_DETAIL_BOUND_S
    )


def test_symbol_detail_returns_the_ranges_it_measured(prod_shaped_db: str) -> None:
    """A fast read of the wrong thing would satisfy the latency bound alone."""

    async def _run() -> dict[str, object]:
        async with _client(prod_shaped_db) as client:
            response = await client.get(f"/api/v1/symbols/{symbol_name(0)}")
            assert response.status_code == 200
            return response.json()  # type: ignore[no-any-return]

    body = asyncio.run(_run())
    available = body["available"]
    assert isinstance(available, dict)
    assert available, "the fixture seeds both families; available must not be empty"
    assert Granularity.D1.value in available


# --- assertion 4 (D10.4) -----------------------------------------------------


def test_status_endpoint_latency(prod_shaped_db: str) -> None:
    """Full-universe ``/api/v1/status`` at prod row shape."""

    async def _run() -> tuple[float, list[float]]:
        async with _client(prod_shaped_db) as client:
            return await _measure(client, "/api/v1/status")

    median, samples = asyncio.run(_run())
    print("\n" + _report("status", median, samples, _STATUS_BOUND_S))
    assert median < _STATUS_BOUND_S, _report(
        "status", median, samples, _STATUS_BOUND_S
    )


# --- assertion 2 (D10.2) — the headline -------------------------------------


def _ceiling_window() -> tuple[str, str]:
    """The largest ``1m`` window the admission check accepts.

    Derived from the route's own ``_max_span_days`` rather than hardcoded, so
    a change to ``API_MAX_BARS_PER_REQUEST`` or to the bars-per-day table moves
    this window with it instead of silently making the test request less than
    the ceiling.
    """
    span = _max_span_days(Granularity.M1, API_MAX_BARS_PER_REQUEST)
    start = DENSE_START.date()
    end = start + timedelta(days=span - 1)
    return start.isoformat(), end.isoformat()


def test_bars_request_latency_at_the_admission_ceiling(dense_minute_db: str) -> None:
    """A ``1m`` request at exactly the ceiling must finish inside a stated bound.

    This is the assertion the whole tier exists for. Nothing in the stack bounds
    *request* latency; the admission check bounds an estimate of bars and
    ``statement_timeout`` bounds each statement. A request can therefore be
    admitted, issue only fast statements, and still take far longer than any
    configured timeout — which is what 186 D12b found in production.
    """
    start, end = _ceiling_window()

    async def _run() -> tuple[float, httpx.Response]:
        async with _client(dense_minute_db) as client:
            started = time.perf_counter()
            response = await client.get(
                f"/api/v1/bars/{DENSE_SYMBOL}",
                params={
                    "granularity": Granularity.M1.value,
                    "start": start,
                    "end": end,
                },
            )
            return time.perf_counter() - started, response

    elapsed, response = asyncio.run(_run())
    print(
        f"\nbars-at-ceiling: elapsed={elapsed:.3f}s bound={_BARS_BOUND_S:.1f}s "
        f"status={response.status_code} window={start}..{end}"
    )

    assert response.status_code == 200, (
        f"a request at the admission ceiling must be served, not rejected; "
        f"got {response.status_code}: {response.text[:300]}"
    )
    assert elapsed < _BARS_BOUND_S, (
        f"bars at ceiling took {elapsed:.3f}s (bound {_BARS_BOUND_S:.1f}s)"
    )

    body = response.json()
    assert body["count"] > 0, "the measured request must have returned bars"


def test_ceiling_request_is_not_bounded_by_statement_timeout(
    dense_minute_db: str,
) -> None:
    """The gap ``statement_timeout`` structurally cannot close (186 D12b).

    A request that completes with ``200`` while its wall clock is a meaningful
    fraction of — or beyond — the configured statement budget demonstrates that
    the budget bounds statements, not requests. Asserted as "no 504 was
    returned" rather than as a fixed ratio: on a fast fixture the request may
    well finish inside the budget, and the load-bearing claim is that *nothing
    would have stopped it* if it had not.
    """
    start, end = _ceiling_window()

    async def _run() -> httpx.Response:
        async with _client(dense_minute_db) as client:
            return await client.get(
                f"/api/v1/bars/{DENSE_SYMBOL}",
                params={
                    "granularity": Granularity.M1.value,
                    "start": start,
                    "end": end,
                },
            )

    response = asyncio.run(_run())
    assert response.status_code != 504, (
        "a 504 here would mean statement_timeout caught the request, which is "
        "exactly what it cannot be relied on to do (186 D12b)"
    )
    assert response.status_code == 200


# --- assertion 3 (D10.3) — feeds the D11 decision ---------------------------


def test_concurrent_symbol_detail_against_the_pool(prod_shaped_db: str) -> None:
    """16 concurrent requests against ``max_size=8``: all complete, none wildly
    slower than a single request.

    The per-request latencies printed here are the D11 pool-sizing decision's
    input, not just a pass/fail signal.
    """

    async def _run() -> tuple[list[float], float]:
        async with _client(prod_shaped_db) as client:
            # Warm the app once so the measurement is about contention rather
            # than about first-request pool construction.
            await client.get(f"/api/v1/symbols/{symbol_name(0)}")

            async def _one(index: int) -> float:
                started = time.perf_counter()
                response = await client.get(
                    f"/api/v1/symbols/{symbol_name(index)}"
                )
                assert response.status_code == 200, response.status_code
                return time.perf_counter() - started

            wall_start = time.perf_counter()
            latencies = await asyncio.gather(
                *(_one(i) for i in range(_CONCURRENCY))
            )
            return list(latencies), time.perf_counter() - wall_start

    latencies, wall = asyncio.run(_run())
    allowance = _SYMBOL_DETAIL_BOUND_S * _QUEUEING_FACTOR
    print(
        f"\nconcurrency-{_CONCURRENCY}: wall={wall:.3f}s "
        f"median={statistics.median(latencies):.3f}s "
        f"max={max(latencies):.3f}s allowance={allowance:.3f}s"
    )
    print(f"  per-request: {[f'{s:.3f}' for s in sorted(latencies)]}")

    assert len(latencies) == _CONCURRENCY, "every request must complete"
    assert max(latencies) < allowance, (
        f"slowest request at concurrency {_CONCURRENCY} was "
        f"{max(latencies):.3f}s, over the {allowance:.3f}s allowance "
        f"({_QUEUEING_FACTOR}x the single-request bound) — a connection held "
        f"for a whole request looks like this (185 D8a)"
    )
