"""Load tests for the Kalshi API surface (slice 188 D11).

Four assertions, each chosen because no unit or integration test can make it:

1. **Trades at the admission ceiling** — a windowed request admitting exactly
   ``API_MAX_BARS_PER_REQUEST`` rows, end to end. ``statement_timeout`` bounds a
   statement, not a request; only this measures what a client waits for.
2. **The refusal costs nothing** — an unbounded request over a market holding
   more than the ceiling returns 422 *and issues no row fetch*. That the count
   guard runs before the fetch is the whole of D8; a test that only checked the
   status code would pass even if the rows were read and thrown away.
3. **The widest catalog list** — 25,000 events under one series, above the
   24,382 maximum measured on production.
4. **Concurrency / pool contention** — 16 concurrent market-detail requests
   against a pool of 8, the 185 D8a shape.

**How assertion 2 proves "no fetch" (D11).** The pool is opened with a psycopg
``ClientCursor`` subclass that appends every rendered statement to a list before
delegating to the real cursor. Nothing is faked: the statements execute against
the real database through the real connection, and the recording is a side
effect. ``pg_stat_statements`` was the alternative and was rejected — it is not
installed on the test cluster and needs ``shared_preload_libraries``, which the
test role cannot even read. Statement text is what the assertion inspects: a
``count(*)`` over ``kalshi.trades`` must appear and a row-projecting ``SELECT``
from it must not.

**Every bound below was measured first and then written down**, with the
measured figure in a comment beside it. The design's provisional numbers were
starting points to check against, not values to commit unverified.

**Which machine these thresholds describe.** As with ``test_187_api_nfr.py``,
these were established against the dedicated test cluster (20 cores, 62 GiB,
reached over a LAN), not manta9000. A breach on different hardware is not
automatically a regression; see ``conftest.py`` and
project-documents/user/runbooks/test-database-cluster.md.

**Gating (187 D9, matched).** Runs only with ``MT_RUN_LOAD_TESTS=1``, and
requires ``MT_TIMESCALE_TEST_URL`` for the throwaway database. The documented
manual invocation is:

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/

CI wiring is **slice 907's** deliverable, not this slice's:
``.github/workflows/ci.yml`` is publish-on-tag with no test job, so this tier
runs manually until 907 (CI Pipeline and Load-Test Gating) lands. Nothing here
is enforced by CI today.

This tier never reads the production DB URL — ``create_app(db_url=...)`` is what
makes that possible, and ``test_load_tier_never_references_prod_db_url``
enforces it mechanically.
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import psycopg
import pytest

from manta_trading.api_server.app import create_app
from manta_trading.constants import API_MAX_BARS_PER_REQUEST

from .conftest import (
    KALSHI_DENSE_EVENT,
    KALSHI_DENSE_MARKET,
    KALSHI_SERIES,
    KALSHI_START,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    # The fixture seeds ~176k rows; the measurements themselves are seconds.
    pytest.mark.timeout(900),
]

_MEASURED_RUNS = 5
"""Median of five; one scheduling hiccup must not fail a healthy bound."""

_CONCURRENCY = 16
"""Twice the pool's ``max_size=8``, so requests genuinely queue."""

_QUEUEING_FACTOR = 6.0
"""How far a request at concurrency 16 may exceed the single-request bound.

The 187 allowance, for the same reason: this assertion exists to catch a pool
problem, not to pin a precise queueing model.
"""

_TRADES_CEILING_BOUND_S = 5.0
"""D11.1. **Measured 1.034 s** (median of five: 0.983-1.045) for a trades
request admitting exactly 75,000 rows, on the test cluster 2026-09-13. The
design's provisional 15 s was a ceiling for the bars path; the Kalshi tape is
an order of magnitude quicker, so the bound is set at ~5x the measurement
rather than left 15x loose — a regression to 5 s would be a real one."""

_EVENTS_LIST_BOUND_S = 1.5
"""D11.3. **Measured 0.303 s** (median of five: 0.290-0.362) for the 25,000-row
events list, on the test cluster 2026-09-13. ~5x headroom over the measurement,
matching the provisional 2 s closely enough to keep it in the same order."""

_MARKET_DETAIL_BOUND_S = 0.1
"""D11.4 single-request baseline. **Measured 0.015 s** median at concurrency 16
(range 0.010-0.020), on the test cluster 2026-09-13. The bound is the
single-request figure; the concurrency assertion multiplies it by
``_QUEUEING_FACTOR``, giving 0.6 s against a measured 0.015 s."""

CANDLES_CEILING_RESPONSE_MB = 34.80
"""D8's open question, **measured 2026-09-13**: a ceiling-sized (75,000-row)
candle response is 34.80 MB (34,800,184 bytes), against the 11.58 MB slice 186
measured for a ceiling-sized bars response.

Three times the size for the same row count, because a candle carries sixteen
decimal *strings* where a bar carries five floats. The shared ceiling bounds
rows, not bytes (D8), so the two differ by exactly this factor. Recorded here
rather than asserted: it is an input to whether a byte ceiling is ever wanted,
not a threshold this slice defends."""


class _RecordingCursor(psycopg.ClientCursor):
    """A real cursor that appends each rendered statement to ``statements``.

    ``ClientCursor`` renders parameters client-side, so ``mogrify`` gives the
    statement as the server will see it without a second round trip.
    """

    statements: list[str] = []  # noqa: RUF012 — deliberately class-level

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        rendered = self.mogrify(query, params) if params else str(query)
        type(self).statements.append(rendered)
        return super().execute(query, params, **kwargs)


@asynccontextmanager
async def _client(
    db_url: str, *, cursor_factory: type | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client against a real app pointed at ``db_url`` (187 D9).

    The lifespan is entered explicitly so the measured path includes the real
    pool with its real ``max_size``. ``cursor_factory`` is applied to the pool's
    connections after startup, for assertion 2.
    """
    app = create_app(db_url=db_url)
    async with app.router.lifespan_context(app):
        if cursor_factory is not None:
            # Set on the live connections rather than at pool construction:
            # the app builds its own pool inside the lifespan, and this leaves
            # that construction — and everything else in the measured path —
            # exactly as production runs it.
            pool = app.state.db_pool
            pool.wait()
            with pool.connection() as probe:
                probe.cursor_factory = cursor_factory
            for conn in list(pool._pool):  # noqa: SLF001 — no public accessor
                conn.cursor_factory = cursor_factory
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


def _ceiling_window() -> str:
    """The query string admitting exactly ``API_MAX_BARS_PER_REQUEST`` rows.

    The fixture seeds one row per minute from ``KALSHI_START``, so N rows is
    N-1 minutes of span. Derived from the constant rather than written down, so
    raising the ceiling moves the window with it.
    """
    end = KALSHI_START + timedelta(minutes=API_MAX_BARS_PER_REQUEST - 1)
    # ``Z`` rather than ``+00:00``: an unencoded ``+`` in a query string is a
    # space, and the bound then fails to parse. Clients hit this too, which is
    # why the design's examples are written the same way.
    return f"?start={_z(KALSHI_START)}&end={_z(end)}"


def _z(moment: datetime) -> str:
    """An instant as ISO-8601 with a ``Z``, safe to put in a query string."""
    return moment.isoformat().replace("+00:00", "Z")


# --- assertion 1 (D11.1) -----------------------------------------------------


def test_trades_at_the_admission_ceiling(kalshi_dense_db: str) -> None:
    """A windowed trades request admitting exactly the ceiling, end to end."""
    url = f"/api/v1/kalshi/markets/{KALSHI_DENSE_MARKET}/trades{_ceiling_window()}"

    async def _run() -> tuple[float, list[float], int]:
        async with _client(kalshi_dense_db) as client:
            median, samples = await _measure(client, url)
            body = (await client.get(url)).json()
            return median, samples, body["count"]

    median, samples, count = asyncio.run(_run())
    print("\n" + _report("trades-ceiling", median, samples, _TRADES_CEILING_BOUND_S))
    assert count == API_MAX_BARS_PER_REQUEST, (
        f"the window must admit exactly the ceiling; got {count:,}"
    )
    assert median < _TRADES_CEILING_BOUND_S, _report(
        "trades-ceiling", median, samples, _TRADES_CEILING_BOUND_S
    )


def test_candles_at_the_admission_ceiling_records_its_response_size(
    kalshi_dense_db: str,
) -> None:
    """The byte size of a ceiling-sized candle response (D8's open question).

    186 measured 11.58 MB for a ceiling-sized bars response. This records the
    Kalshi equivalent, which carries sixteen decimal-string columns per row
    rather than five floats, so the two are worth comparing.
    """
    url = (
        f"/api/v1/kalshi/markets/{KALSHI_DENSE_MARKET}/candlesticks"
        f"{_ceiling_window()}"
    )

    async def _run() -> tuple[int, int]:
        async with _client(kalshi_dense_db) as client:
            response = await client.get(url)
            assert response.status_code == 200, response.text[:300]
            return len(response.content), response.json()["count"]

    size, count = asyncio.run(_run())
    megabytes = size / 1_000_000
    print(
        f"\ncandles-ceiling: {count:,} rows, {megabytes:.2f} MB "
        f"({size:,} bytes); 186 measured 11.58 MB for bars"
    )
    assert count == API_MAX_BARS_PER_REQUEST


# --- assertion 2 (D11.2) — the refusal costs nothing -------------------------


def test_over_ceiling_is_refused_without_fetching_rows(kalshi_dense_db: str) -> None:
    """422 over the ceiling, and no row-projecting SELECT against the tape.

    The fixture seeds ceiling + 1,000 trades, so an unbounded request is over
    by construction. See the module docstring for why a recording cursor is the
    mechanism.
    """
    url = f"/api/v1/kalshi/markets/{KALSHI_DENSE_MARKET}/trades"

    async def _run() -> tuple[int, list[str]]:
        _RecordingCursor.statements = []
        async with _client(kalshi_dense_db, cursor_factory=_RecordingCursor) as client:
            response = await client.get(url)
            return response.status_code, list(_RecordingCursor.statements)

    status, statements = asyncio.run(_run())
    trade_statements = [s for s in statements if "kalshi.trades" in s]
    counts = [s for s in trade_statements if "count(*)" in s]
    fetches = [s for s in trade_statements if "count(*)" not in s]

    print(f"\nover-ceiling: {len(statements)} statements, {len(counts)} count(s)")
    assert status == 422, f"expected a refusal, got {status}"
    assert counts, "the guard's count(*) must have run"
    assert not fetches, f"a row fetch ran despite the refusal: {fetches[:2]}"


# --- assertion 3 (D11.3) — the widest catalog list ---------------------------


def test_events_list_at_production_width(kalshi_dense_db: str) -> None:
    """25,000 events under one series, above production's 24,382 maximum."""
    url = f"/api/v1/kalshi/series/{KALSHI_SERIES}/events"

    async def _run() -> tuple[float, list[float], int]:
        async with _client(kalshi_dense_db) as client:
            median, samples = await _measure(client, url)
            body = (await client.get(url)).json()
            return median, samples, body["count"]

    median, samples, count = asyncio.run(_run())
    print("\n" + _report("events-list", median, samples, _EVENTS_LIST_BOUND_S))
    assert count > 24_382, f"the fixture must exceed production's width; got {count:,}"
    assert median < _EVENTS_LIST_BOUND_S, _report(
        "events-list", median, samples, _EVENTS_LIST_BOUND_S
    )


# --- assertion 4 (D11.4) — concurrency --------------------------------------


def test_concurrent_market_detail_requests(kalshi_dense_db: str) -> None:
    """16 concurrent market seeks against a pool of 8 (the 185 D8a shape)."""
    url = f"/api/v1/kalshi/markets/{KALSHI_DENSE_MARKET}"
    bound = _MARKET_DETAIL_BOUND_S * _QUEUEING_FACTOR

    async def _run() -> tuple[float, list[float]]:
        async with _client(kalshi_dense_db) as client:
            # Warm the pool so connection setup is not charged to the measurement.
            await client.get(url)

            async def _one() -> float:
                started = time.perf_counter()
                response = await client.get(url)
                assert response.status_code == 200, response.text[:200]
                return time.perf_counter() - started

            samples = await asyncio.gather(
                *(_one() for _ in range(_CONCURRENCY))
            )
            return statistics.median(samples), list(samples)

    median, samples = asyncio.run(_run())
    print("\n" + _report(f"market-detail x{_CONCURRENCY}", median, samples, bound))
    assert median < bound, _report(
        f"market-detail x{_CONCURRENCY}", median, samples, bound
    )


def test_markets_list_scoped_to_the_dense_event(kalshi_dense_db: str) -> None:
    """A fast read of the wrong thing would satisfy a latency bound alone."""

    async def _run() -> dict[str, Any]:
        async with _client(kalshi_dense_db) as client:
            response = await client.get(
                f"/api/v1/kalshi/events/{KALSHI_DENSE_EVENT}/markets"
            )
            assert response.status_code == 200, response.text[:300]
            return response.json()  # type: ignore[no-any-return]

    body = asyncio.run(_run())
    assert body["count"] == 450
    assert body["event_ticker"] == KALSHI_DENSE_EVENT
