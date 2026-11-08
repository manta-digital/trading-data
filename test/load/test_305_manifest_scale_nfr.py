"""Load test for the futures completeness surfaces at 307's scale (slice 305 D9).

``manifest_scale`` (``manifest_scale_seed.py``) builds one throwaway tick
database with about 30,000 units, their ledgers and 19.4 M compressed
``tick_statistics`` rows, once per test session, and drops it afterwards. The
first test pins the seed's shape, so a timing taken on a seed that quietly came
out small cannot pass. The timed tests follow in the same file.

Gate (CI runs no test job; this is the load tier's gate, as for every other
load test):

    MT_RUN_LOAD_TESTS=1 uv run pytest test/load/test_305_manifest_scale_nfr.py -s

Requires ``MT_TIMESCALE_TEST_URL``. This tier never reads the production DB URL;
``test_load_tier_never_references_prod_db_url`` covers this file.
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest
from tick_support.fake_provider import FakeClock, FakeTickProvider

from load.manifest_scale_seed import ManifestScale
from manta_trading.config import Settings
from manta_trading.data.tick import acquisition_pass, purchase_plan
from manta_trading.data.tick.acquisition_pass import run_pass
from manta_trading.data.tick.databento.dbn_file import DbnFileReader
from manta_trading.data.tick.family_report import ReadProgress, futures_completeness
from manta_trading.data.tick.ingest_select import select_units
from manta_trading.data.tick.pass_contract import PassResult, TickOutcome
from manta_trading.data.tick.run_context import open_tick_run
from manta_trading.data.tick.schema_families import FAMILY_LEDGER
from manta_trading.data.tick.store_context import connect_migrated
from manta_trading.data.tick.tick_status_build import build_status

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(3600),
]

#: 307's size: ES and GC, every UTC day a session touches, three families each,
#: plus the held tbbo window.
EXPECTED_UNITS = 30_000
UNITS_TOLERANCE = 0.05
#: The architecture's cost table for ES and GC statistics.
EXPECTED_STATISTICS_ROWS = 19_400_000
ROWS_TOLERANCE = 0.10


def test_the_seed_has_the_shape_307_will_make(manifest_scale: ManifestScale) -> None:
    assert (
        abs(manifest_scale.units - EXPECTED_UNITS) <= UNITS_TOLERANCE * EXPECTED_UNITS
    )
    assert (
        abs(manifest_scale.statistics_rows - EXPECTED_STATISTICS_ROWS)
        <= ROWS_TOLERANCE * EXPECTED_STATISTICS_ROWS
    )
    with psycopg.connect(manifest_scale.tick_url) as conn:
        for family, table in FAMILY_LEDGER.items():
            row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
            assert row is not None and row[0] > 0, f"{family}: empty ledger"
        compressed = conn.execute(
            "SELECT count(*) FILTER (WHERE is_compressed), count(*)"
            " FROM timescaledb_information.chunks"
            " WHERE hypertable_name = 'tick_statistics'"
        ).fetchone()
    assert compressed is not None and compressed[0] == compressed[1] > 0


# -- the timed surfaces (D9) --------------------------------------------------

#: Bounds in seconds (D9's table). Each is the median of three runs after one
#: warm-up; none is raised to pass (D10).
COMPLETENESS_BOUND_S = 5.0
SINGLE_STATEMENT_BOUND_S = 5.0  # a 2x margin against the composed 10 s timeout
API_BLOCK_BOUND_S = 0.25
STATUS_BOUND_S = 30.0
PREFLIGHT_AND_PASS_BOUND_S = 10.0
SELECT_UNITS_BOUND_S = 2.0
RUNS = 3
#: Bounds still missed at 307's scale after 305's D10 fixes; each needs stored
#: derived state, which D10 puts outside 305. Strict: the test fails when the
#: bound is met, so the marker is removed with the fix and the bound never moves.
KNOWN_MISS = pytest.mark.xfail(
    strict=True, reason="manta-digital/trading-data#74: needs stored derived state"
)
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)


class TimedConn:
    """Times every ``execute`` and keeps (seconds, statement, parameters).

    Everything else is the wrapped connection's: the surfaces under test use
    ``execute`` for their reads, so this is the connection's logging hook."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn
        self.statements: list[tuple[float, str, Any]] = []

    async def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        started = time.monotonic()
        cursor = await self._conn.execute(query, params, **kwargs)
        self.statements.append((time.monotonic() - started, str(query), params))
        return cursor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


async def measure(label: str, bound: float, run: Callable[[], Awaitable[Any]]) -> float:
    """The median of ``RUNS`` runs after one warm-up; prints the samples."""
    await run()
    samples = []
    for _ in range(RUNS):
        started = time.monotonic()
        await run()
        samples.append(time.monotonic() - started)
    median = statistics.median(samples)
    shown = ", ".join(f"{s:.2f}" for s in samples)
    print(f"\n{label}: median {median:.2f} s (bound {bound} s); samples {shown}")
    return median


async def explain_slowest(conn: Any, timed: TimedConn) -> float:
    """Print the slowest statement and its ``EXPLAIN (ANALYZE, BUFFERS)``."""
    seconds, query, params = max(timed.statements, key=lambda s: s[0])
    print(f"slowest statement ({seconds:.2f} s): {query}\nparameters: {params}")
    plan = await conn.execute(f"EXPLAIN (ANALYZE, BUFFERS) {query}", params)
    for (line,) in await plan.fetchall():
        print(f"  {line}")
    return seconds


@pytest.fixture
async def conn(manifest_scale: ManifestScale) -> AsyncIterator[Any]:
    """The connection the surfaces use in production (``connect_migrated``)."""
    connection = await connect_migrated(manifest_scale.tick_url)
    try:
        yield connection
    finally:
        await connection.close()


@KNOWN_MISS
async def test_futures_completeness_with_cross_tier_within_5_s(
    manifest_scale: ManifestScale, conn: Any
) -> None:
    timed = TimedConn(conn)

    async def run() -> Any:
        return await futures_completeness(
            timed,  # type: ignore[arg-type]
            manifest_scale.calendar_url,
            manifest_scale.universe,
            ReadProgress(),
            cross_tier=True,
        )

    median = await measure(
        "futures_completeness(cross_tier=True)", COMPLETENESS_BOUND_S, run
    )
    slowest = await explain_slowest(conn, timed)
    assert median <= COMPLETENESS_BOUND_S
    assert slowest < SINGLE_STATEMENT_BOUND_S


@KNOWN_MISS
async def test_the_api_tick_block_adds_under_a_quarter_second(
    manifest_scale: ManifestScale, conn: Any
) -> None:
    async def run() -> Any:
        return await futures_completeness(
            conn,
            manifest_scale.calendar_url,
            manifest_scale.universe,
            ReadProgress(),
            cross_tier=False,
        )

    assert (
        await measure("API tick block (cross_tier=False)", API_BLOCK_BOUND_S, run)
        < API_BLOCK_BOUND_S
    )


async def test_status_for_both_products_within_30_s(
    manifest_scale: ManifestScale, conn: Any
) -> None:
    async def run() -> Any:
        return await build_status(
            conn,
            manifest_scale.calendar_url,
            manifest_scale.universe,
            NOW,
            tick_url=manifest_scale.tick_url,
        )

    assert await measure("build_status", STATUS_BOUND_S, run) <= STATUS_BOUND_S


async def test_select_units_with_nothing_selectable_within_2_s(conn: Any) -> None:
    async def run() -> Any:
        selection = await select_units(conn)
        assert not selection.selected  # every unit is ingested already
        return selection

    assert (
        await measure("select_units", SELECT_UNITS_BOUND_S, run) <= SELECT_UNITS_BOUND_S
    )


async def test_preflight_and_a_steady_estimate_only_pass_within_10_s(
    manifest_scale: ManifestScale,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(acquisition_pass, "TICK_UNIVERSE", manifest_scale.universe)
    monkeypatch.setattr(purchase_plan, "TICK_UNIVERSE", manifest_scale.universe)
    clock = FakeClock(NOW)
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        tick_db_url=manifest_scale.tick_url,
        tick_archive_dir=tmp_path,
        timescale_db_url=manifest_scale.calendar_url,
        tick_spend_ceiling_usd=Decimal("100"),
        tick_spend_30d_ceiling_usd=Decimal("500"),
    )

    async def run() -> PassResult:
        async with open_tick_run(
            settings,
            clock=clock,
            provider_factory=lambda _: FakeTickProvider(clock=clock),  # type: ignore[arg-type,return-value]
            env_file=None,
        ) as tick:
            return await run_pass(
                tick, (None, None), True, DbnFileReader(), free=lambda _: 10**12
            )

    result = await run()
    assert result.outcome is TickOutcome.OK, result
    median = await measure(
        "preflight + estimate-only pass", PREFLIGHT_AND_PASS_BOUND_S, run
    )
    assert median <= PREFLIGHT_AND_PASS_BOUND_S
