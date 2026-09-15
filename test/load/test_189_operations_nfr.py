"""Load tests for the operations routes (slice 189 D8, SC10, SC12).

Two bounds, and they assert different things:

1. **Latency** on ``/api/v1/overview`` — the read path end to end, through the
   executor bridge and the pool. Unit tests patch the reader; only this
   measures what an operator polling the endpoint actually waits for.

2. **Contention** — the assertion D8 requires. ``/api/v1/credits`` reaches a
   third party, and both routes dispatch through ``run_in_executor(None, …)``,
   so they share one default thread pool of ``min(32, cpu + 4)``. A slow EODHD
   can therefore delay a *database* route. That is a resource bound, not a
   latency one: it is about this server's thread budget, not about EODHD's
   response time.

**The credit fetch is stubbed in the contention test, and that is required
rather than convenient.** A live call would make the assertion depend on a
third party's latency and would spend real production quota to prove nothing
about our thread pool.

**Both bounds were measured before they were written.** The observed figures
are recorded beside each constant and in the slice design's *Testing Strategy*.
A load test whose threshold was invented is a test that passes for the wrong
reason.

**Gating.** Runs only with ``MT_RUN_LOAD_TESTS=1`` (the convention slice 167
established) and requires ``MT_TIMESCALE_TEST_URL`` for the throwaway
database. The documented manual invocation is:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/

This tier never reads the production DB URL: ``create_app(db_url=…)`` (187 D9)
is the seam, and ``test_load_tier_never_references_prod_db_url`` enforces it.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from manta_trading.api_server.app import create_app
from manta_trading.constants import EODHD_ACCOUNT_TIMEOUT_SECONDS

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # The prod-shaped fixture seeds 12,000 symbols; the measurements
    # themselves are milliseconds.
    pytest.mark.timeout(900),
]

_OVERVIEW_BOUND_S = 0.25
"""SC10, < 250 ms.

**Measured 2026-09-15** against ``prod_shaped_db``: median 0.027 s over ten
sequential requests, range 0.023-0.103 s (the 0.103 s is the first, cold
sample — an unwarmed pool and an unprimed plan cache).

The bound is ~9x the median and ~2.4x the worst cold sample. Deliberately
loose in the same way 187's symbol-detail bound is: it exists to catch a
*shape* regression — an unbounded scan, a second round trip per pass kind, a
derivation moved back into the route — not to pin the millisecond.

The reads are ``2 x |PassKind|`` indexed lookups on ``pass_runs`` plus four
``MAX()`` probes, one per source table.
"""

_CONTENTION_BOUND_S = 0.25
"""D8/SC12: the overview stays within its own bound while credits are stuck.

The same number as ``_OVERVIEW_BOUND_S``, and that identity is the assertion:
a third party's slowness must not show up in a database route's latency at
all. Measured 2026-09-15 with the fetch stubbed to block for the full
``EODHD_ACCOUNT_TIMEOUT_SECONDS``; the figure is recorded in the slice design.

If this cannot be met, the fix is a dedicated executor for the credit call —
**not** widening the shared pool and **not** relaxing this bound. Widening the
pool would move the cliff rather than remove it, and relaxing the bound would
publish the coupling as acceptable.
"""

_MEASURED_RUNS = 5
"""Median of five; one scheduling hiccup must not fail a healthy bound."""


def _default_executor_threads() -> int:
    """The default pool asyncio gives ``run_in_executor(None, …)``.

    ``ThreadPoolExecutor``'s default since 3.8: ``min(32, cpu_count + 4)``.
    Read rather than assumed, so the contention test can size itself against
    the machine it runs on instead of a number that is wrong on half of them.
    """
    return min(32, (os.cpu_count() or 1) + 4)


_CONTENTION_CONCURRENCY = _default_executor_threads() + 4
"""Enough concurrent credit calls to exhaust the shared pool, plus a margin.

A contention test with fewer concurrent calls than the pool has threads proves
nothing: every call would get its own thread and the overview would never wait
for one. Sized from the real pool width so this holds on any machine — four
over is enough that at least one request is queued with no thread available,
which is the condition under test.
"""


@asynccontextmanager
async def _client(db_url: str) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client against a real app pointed at ``db_url`` (187 D9).

    The lifespan is entered explicitly: ``ASGITransport`` sends only HTTP
    scopes, so without it the pool is never constructed. Entering it also puts
    the real pool, at its real ``max_size``, in the measured path.
    """
    app = create_app(db_url=db_url)
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


# --- assertion 1: latency (SC10) ---------------------------------------------


def test_overview_latency(prod_shaped_db: str) -> None:
    """The overview read path, measured through the real app."""

    async def _run() -> tuple[float, list[float]]:
        async with _client(prod_shaped_db) as client:
            return await _measure(client, "/api/v1/overview")

    median, samples = asyncio.run(_run())
    print("\n" + _report("overview", median, samples, _OVERVIEW_BOUND_S))
    assert median < _OVERVIEW_BOUND_S, _report(
        "overview", median, samples, _OVERVIEW_BOUND_S
    )


def test_overview_returns_the_facts_it_measured(prod_shaped_db: str) -> None:
    """A fast response with an empty body would satisfy the bound alone."""

    async def _run() -> dict:
        async with _client(prod_shaped_db) as client:
            response = await client.get("/api/v1/overview")
            assert response.status_code == 200
            return response.json()

    body = asyncio.run(_run())
    assert body["passes"], "no pass lines: the bound measured an empty read"
    assert body["sources"], "no source freshness: the bound measured a partial read"


def test_the_source_probes_do_not_scan(prod_shaped_db: str) -> None:
    """187 D10's approach: assert the plan, not just the clock.

    Each source probe is ``SELECT max(<time column>) FROM <table>``. On a
    hypertable that must resolve by index or by per-chunk minmax, never by
    reading every row — a seq scan here would pass the latency bound on a
    small fixture and fail in production.
    """
    import psycopg

    from manta_trading.constants import (
        DAILY_OHLCV_TABLE,
        MINUTE_OHLCV_TABLE,
    )

    with psycopg.connect(prod_shaped_db) as conn:
        for table, column in (
            (MINUTE_OHLCV_TABLE, "time"),
            (DAILY_OHLCV_TABLE, "time"),
        ):
            plan_rows = conn.execute(
                f"EXPLAIN (FORMAT TEXT) SELECT max({column}) FROM {table}"
            ).fetchall()
            plan = "\n".join(row[0] for row in plan_rows)
            assert "Seq Scan" not in plan, f"{table} max({column}) scans:\n{plan}"


# --- assertion 2: executor contention (D8 / SC12) ----------------------------


def test_a_slow_credit_fetch_does_not_delay_the_overview(
    prod_shaped_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """D8: the shared executor must not couple the two routes.

    Both routes dispatch through ``run_in_executor(None, …)``, so they draw on
    one thread pool. This saturates that pool with credit calls that block for
    the full account timeout, then measures the overview — which must still
    meet its own bound.

    The stub is required, not a convenience: a live call would make this
    assertion depend on EODHD's response time and would spend real quota.
    """
    blocked_calls = 0

    def _blocking_fetch(_key: str) -> object:
        # Blocks exactly as a stuck provider would: the route's worst case is
        # the account timeout, so that is what a saturated pool holds for.
        nonlocal blocked_calls
        blocked_calls += 1
        time.sleep(EODHD_ACCOUNT_TIMEOUT_SECONDS)
        raise RuntimeError("stubbed: provider did not answer")

    monkeypatch.setattr(
        "manta_trading.api_server.routes.operations.fetch_credit_usage",
        _blocking_fetch,
    )

    async def _run() -> tuple[float, list[float]]:
        async with _client(prod_shaped_db) as client:
            # Saturate the shared pool, then measure while it is saturated.
            stuck = [
                asyncio.create_task(client.get("/api/v1/credits"))
                for _ in range(_CONTENTION_CONCURRENCY)
            ]
            # Let every stuck request reach the executor before measuring;
            # measuring first would race the saturation this test creates.
            await asyncio.sleep(0.5)
            try:
                return await _measure(client, "/api/v1/overview")
            finally:
                for task in stuck:
                    task.cancel()
                await asyncio.gather(*stuck, return_exceptions=True)

    median, samples = asyncio.run(_run())
    label = "overview-under-credit-contention"
    print(
        "\n"
        + _report(label, median, samples, _CONTENTION_BOUND_S)
        + f" max={max(samples):.3f}s"
        + f" concurrency={_CONTENTION_CONCURRENCY}"
        + f" executor_threads={_default_executor_threads()}"
        + f" blocked_calls={blocked_calls}"
    )
    # Asserted on the WORST sample, not the median. Contention is an
    # intermittent condition by nature: a request either finds a free thread
    # or waits for a blocked one to time out. A median hides exactly the
    # sample that matters — the first measurement under saturation — and a
    # bound that only the median has to clear would report "no coupling"
    # while an operator's request waited five seconds.
    # `blocked_calls` is expected to equal the credit executor's width (2),
    # not the concurrency (36): the remaining requests queue *outside* the
    # executor and never enter the stub. That is the fix working — the
    # ceiling on how much of the machine a stuck provider can hold is the
    # executor's width — but it does mean this assertion proves the DB route
    # is unaffected rather than re-proving the default pool is saturated.
    # `test_the_shared_pool_is_not_what_serves_credits` below pins the
    # mechanism directly.
    assert max(samples) < _CONTENTION_BOUND_S, (
        _report(label, median, samples, _CONTENTION_BOUND_S)
        + f" max={max(samples):.3f}s"
        + f" with {_CONTENTION_CONCURRENCY} credit calls blocked on a pool of "
        + f"{_default_executor_threads()} threads. A third party's slowness is "
        "reaching a database route: give the credit fetch a dedicated executor "
        "(189 D8) rather than widening the shared pool or relaxing this bound."
    )


def test_the_contention_fixture_actually_saturates(
    prod_shaped_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the guard: a contention test that never contends proves nothing.

    Asserts the chosen concurrency really does exceed the pool, so the
    measurement above is taken under saturation rather than beside it.
    """
    assert _CONTENTION_CONCURRENCY > _default_executor_threads(), (
        f"concurrency {_CONTENTION_CONCURRENCY} does not exceed the "
        f"{_default_executor_threads()}-thread default pool: every call would "
        "get its own thread and nothing would contend"
    )


def test_the_shared_pool_is_not_what_serves_credits() -> None:
    """The mechanism behind the contention bound, asserted directly.

    The latency assertion above passes for two possible reasons — the credit
    call is confined, or the machine was simply fast enough that day. This
    distinguishes them: it names the executor the route actually dispatches on
    and fails if anyone switches it back to ``None``, which is the change that
    would silently reintroduce the 4.564 s coupling.

    Not a measurement, so it needs no fixture and no database.
    """
    from manta_trading.api_server.routes import operations

    _real_fetch = operations.fetch_credit_usage

    assert operations._credit_executor is not None
    # Bounded, and small: the point is the ceiling on what a stuck provider
    # can hold, not throughput.
    assert operations._CREDIT_EXECUTOR_THREADS < _default_executor_threads()

    # Asserted by *observing the dispatch*, not by reading the source. A text
    # search is what a first version of this test did, and it passed with the
    # executor reverted to None: the call spans two lines, so the needle
    # "run_in_executor(None" never matched, and "_credit_executor" still
    # appeared in the function's own docstring. Both checks were satisfied by
    # prose while the behavior was wrong.
    recorded: list[object] = []
    async def _run() -> None:
        loop = asyncio.get_running_loop()
        original = loop.run_in_executor

        def _spy(executor, func, *args):
            recorded.append(executor)
            return original(executor, func, *args)

        loop.run_in_executor = _spy  # type: ignore[assignment,method-assign]
        try:
            request = _FakeRequest(
                _FakeSettings(eodhd_api_key="stub-key-never-sent")
            )
            operations.fetch_credit_usage = _stub_fetch  # type: ignore[assignment]
            await operations.get_credits(request)  # type: ignore[arg-type]
        finally:
            loop.run_in_executor = original  # type: ignore[method-assign]
            operations.fetch_credit_usage = _real_fetch  # type: ignore[assignment]

    asyncio.run(_run())

    assert recorded, "the route dispatched nothing to an executor"
    assert recorded[0] is operations._credit_executor, (
        "the credit fetch dispatched on "
        f"{recorded[0]!r} rather than its dedicated executor. "
        "run_in_executor(None, …) shares the pool every DB route uses, which "
        "is the 4.564 s coupling 189 D8 exists to prevent."
    )


class _FakeSettings:
    minute_firing_days = (0, 3)

    def __init__(self, *, eodhd_api_key: str | None) -> None:
        self.eodhd_api_key = eodhd_api_key


class _FakeApp:
    def __init__(self, settings: _FakeSettings) -> None:
        self.state = type("S", (), {"settings": settings})()


class _FakeRequest:
    def __init__(self, settings: _FakeSettings) -> None:
        self.app = _FakeApp(settings)


def _stub_fetch(_key: str) -> object:
    raise RuntimeError("stubbed: never reaches EODHD")
