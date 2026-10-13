"""Load test for the futures API (slice 231, Technical Requirements).

Two throwaway tick databases, both on the test cluster:

- **The busiest real session.** The archived 2024-12-18 ``tbbo`` day (889,373
  records) ingested through the real pass, as slice 226's load test does. Its
  ESZ4 session is the 687,287-tick session the design measured on the dev tick
  database. It carries the ceiling-sized read and the refused read.
- **A long roll.** ``bulk_ticks.seed_minute_roll``: one tick a minute for ESU4
  and ESZ4 through every CME session in the range, so a product ``1m`` bars
  read across the September roll returns about 1,380 bars a session. It
  carries the 54-session bars read and the status tick block.

The primary is ``prod_shaped_db``, which also holds the CME calendar. Every
URL is passed through ``create_app(db_url=…, tick_db_url=…)``; this tier never
reads a production URL (``test_load_tier_never_references_prod_db_url``).

Each bound is about twice the median of five runs, measured on the test
cluster; the measurement and its date are recorded beside each bound. The
status addition is its own pass/fail test: ``/api/v1/status`` as a whole is
already over its 187 bound on main (known debt, 2.79 s against 1.5 s), and
this test keeps the tick block from hiding inside that.

Gate (CI runs no test job; see slice 907):

    MT_RUN_LOAD_TESTS=1 uv run python scripts/run_tests.py load -- \
        test/load/test_231_futures_api_nfr.py
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from tick_support.bulk_ticks import MinuteRoll, seed_minute_roll
from tick_support.ingest import ingest_inputs, run_ingest_phase
from tick_support.roll_seed import ESZ4
from tick_support.tier_units import seed_tier_unit

from manta_trading.api_server.app import create_app
from manta_trading.api_server.models.futures import (
    FuturesTicksResponse,
    TickRecord,
)
from manta_trading.constants import API_MAX_BARS_PER_REQUEST
from manta_trading.data.tick.constants import TickSchema
from manta_trading.data.tick.get_plan import GetPlan, contract_plan
from manta_trading.data.tick.pass_contract import TickOutcome
from manta_trading.data.tick.tick_format import cell_value
from manta_trading.data.tick.tick_reads import TICK_COLUMNS, Row, read_ticks

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("MT_RUN_LOAD_TESTS") != "1",
        reason="MT_RUN_LOAD_TESTS=1 required",
    ),
    pytest.mark.timeout(1800),
]

ARCHIVE = Path("/data/tick-archive")
BUSIEST_DAY = date(2024, 12, 18)
BUSIEST_TIER = ARCHIVE / "GLBX-20250123-XT4GD5UM6C" / "glbx-mdp3-20241218.tbbo.dbn.zst"
BUSIEST_DEFINITIONS = (
    ARCHIVE / "GLBX-20260930-HVGRLYKHRN" / "glbx-mdp3-20241218.definition.dbn.zst"
)
BUSIEST_RECORDS = 889_373
#: The roll seed: sessions dated [ROLL_SEED_FIRST, ROLL_END); the read starts a
#: week later so the volume rule's lag sessions are held.
ROLL_SEED_FIRST, ROLL_READ_FIRST, ROLL_END = (
    date(2024, 7, 29),
    date(2024, 8, 5),
    date(2024, 10, 19),
)
RUNS = 5
#: The design's 54-session ``1m`` read: about 1,380 bars a session, under the
#: 75,000 ceiling. Taken from the seeded sessions, because a holiday-shortened
#: session (Labor Day trades) shifts how many a date range holds.
READ_SESSIONS = 54

#: Bounds: about 2× the median of five runs on the test cluster, from the
#: second run of 2026-10-13 (the shipped, validated-row path); the first run's
#: medians are in brackets.
#: 75,000 ESZ4 ticks of 2024-12-18 through ``until``: median 1.514 s [1.704].
TICKS_AT_CEILING_BOUND_S = 3.0
#: The whole 2024-12-18 ESZ4 session, refused at the ceiling: 0.279 s [0.264].
REFUSED_READ_BOUND_S = 0.55
#: 54 sessions of ``1m`` product bars across the roll, 74,280 bars: 1.577 s.
PRODUCT_BARS_BOUND_S = 3.2
#: ``/status`` with a tick pool minus without: −0.006 s [0.032]. Not 2× that:
#: the route's own run-to-run spread is about 0.3 s, so the bound sits above
#: the noise and still catches a block costing a quarter-second.
STATUS_TICK_BLOCK_BOUND_S = 0.25


@asynccontextmanager
async def _client(primary: str, tick: str | None) -> AsyncIterator[httpx.AsyncClient]:
    """An ASGI client over the real lifespan (187 D9's seam, plus 231's)."""
    app = create_app(db_url=primary, tick_db_url=tick)
    async with app.router.lifespan_context(app):
        if app.state.tick_pool is not None:
            await app.state.tick_pool.wait()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://load-test"
        ) as client:
            yield client


async def _median(
    client: httpx.AsyncClient, path: str, params: dict[str, str], status: int
) -> tuple[float, list[float]]:
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        response = await client.get(path, params=params)
        samples.append(time.perf_counter() - started)
        assert response.status_code == status, response.text[:300]
    return statistics.median(samples), samples


def _report(name: str, median: float, samples: Sequence[float], bound: float) -> str:
    shown = ", ".join(f"{s:.3f}" for s in samples)
    return f"{name}: median {median:.3f} s (runs {shown}); bound {bound:.3f} s"


@pytest.fixture
async def busiest(migrated_tick_db: str, prod_shaped_db: str, tmp_path: Path) -> str:
    """The 2024-12-18 tbbo day, ingested for real."""
    for path in (BUSIEST_TIER, BUSIEST_DEFINITIONS):
        assert path.is_file(), f"archived day missing: {path}"
    seeded = await seed_tier_unit(
        migrated_tick_db,
        tmp_path,
        TickSchema.TBBO,
        BUSIEST_DAY,
        job_id=BUSIEST_TIER.parent.name,
        tier_file=BUSIEST_TIER,
        definition_file=BUSIEST_DEFINITIONS,
    )
    assert seeded.record_count == BUSIEST_RECORDS
    report = await run_ingest_phase(
        migrated_tick_db, tmp_path, ingest_inputs(migrated_tick_db, prod_shaped_db)
    )
    assert report.outcome is TickOutcome.OK, report.error
    return migrated_tick_db


@pytest.fixture
def long_roll(second_migrated_tick_db: str, prod_shaped_db: str) -> MinuteRoll:
    return seed_minute_roll(
        second_migrated_tick_db, prod_shaped_db, ROLL_SEED_FIRST, ROLL_END
    )


def _session_count(url: str) -> int:
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT sum(record_count) FROM tick_ingest_ledger"
            " WHERE instrument_id = %s AND session_date = %s",
            (ESZ4.instrument_id, BUSIEST_DAY),
        ).fetchone()
    assert row is not None and row[0] is not None
    return int(row[0])


def _until_at_ceiling(url: str) -> datetime:
    """The latest microsecond bound with at most the ceiling of ESZ4 ticks from
    the session open: the stamp of the tick just past the ceiling, floored."""
    with psycopg.connect(url) as conn:
        row = conn.execute(
            "SELECT ts_event FROM tick_trade WHERE instrument_id = %s"
            " AND ts_event >= %s ORDER BY ts_event, sequence, sequence_ordinal"
            " OFFSET %s LIMIT 1",
            (
                ESZ4.instrument_id,
                int(datetime(2024, 12, 17, 23, tzinfo=UTC).timestamp()) * 10**9,
                API_MAX_BARS_PER_REQUEST,
            ),
        ).fetchone()
    assert row is not None
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=row[0] // 1000)


TICKS = "/api/v1/futures/contracts/ESZ4/ticks"
SESSION = {"start": "2024-12-18", "end": "2024-12-19"}


async def test_a_ceiling_sized_tick_read(busiest: str, prod_shaped_db: str) -> None:
    """Bound measured 2026-10-13: see TICKS_AT_CEILING_BOUND_S."""
    assert _session_count(busiest) > API_MAX_BARS_PER_REQUEST
    params = {**SESSION, "until": _until_at_ceiling(busiest).isoformat()}
    async with _client(prod_shaped_db, busiest) as client:
        body = (await client.get(TICKS, params=params)).json()
        median, samples = await _median(client, TICKS, params, 200)
    print("\n" + _report("ticks at ceiling", median, samples, TICKS_AT_CEILING_BOUND_S))
    assert 0.9 * API_MAX_BARS_PER_REQUEST < body["count"] <= API_MAX_BARS_PER_REQUEST
    assert median < TICKS_AT_CEILING_BOUND_S


async def test_a_refused_whole_session_read(busiest: str, prod_shaped_db: str) -> None:
    """Bound measured 2026-10-13: see REFUSED_READ_BOUND_S."""
    async with _client(prod_shaped_db, busiest) as client:
        median, samples = await _median(client, TICKS, SESSION, 422)
    print("\n" + _report("refused session", median, samples, REFUSED_READ_BOUND_S))
    assert median < REFUSED_READ_BOUND_S


async def test_a_product_1m_bars_read_across_the_roll(
    long_roll: MinuteRoll, second_migrated_tick_db: str, prod_shaped_db: str
) -> None:
    """Bound measured 2026-10-13: see PRODUCT_BARS_BOUND_S."""
    dates = [
        s.session_date for s in long_roll.sessions if s.session_date >= ROLL_READ_FIRST
    ]
    params = {
        "granularity": "1m",
        "start": dates[0].isoformat(),
        "end": dates[READ_SESSIONS].isoformat(),  # exclusive: READ_SESSIONS sessions
    }
    path = "/api/v1/futures/products/ES/bars"
    async with _client(prod_shaped_db, second_migrated_tick_db) as client:
        body = (await client.get(path, params=params)).json()
        median, samples = await _median(client, path, params, 200)
    sessions = {b["session_date"] for b in body["bars"]}
    print(
        "\n"
        + _report("product 1m bars", median, samples, PRODUCT_BARS_BOUND_S)
        + f"; {len(sessions)} sessions, {body['count']} bars"
    )
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESU4", "ESZ4"]
    assert len(sessions) == READ_SESSIONS and body["unresolved"] == []
    assert median < PRODUCT_BARS_BOUND_S


async def test_the_tick_block_adds_little_to_status(
    long_roll: MinuteRoll, second_migrated_tick_db: str, prod_shaped_db: str
) -> None:
    """The difference in median ``/status`` latency with and without a tick
    pool. Bound measured 2026-10-13: see STATUS_TICK_BLOCK_BOUND_S."""
    path, params = "/api/v1/status", dict[str, str]()
    async with _client(prod_shaped_db, None) as client:
        without, without_samples = await _median(client, path, params, 200)
    async with _client(prod_shaped_db, second_migrated_tick_db) as client:
        assert (await client.get(path)).json()["tick"]["state"] == "ok"
        with_tick, with_samples = await _median(client, path, params, 200)
    added = with_tick - without
    print(
        f"\nstatus without tick pool: {without:.3f} s {without_samples}"
        f"\nstatus with tick pool: {with_tick:.3f} s {with_samples}"
        f"\nadded {added:.3f} s; bound {STATUS_TICK_BLOCK_BOUND_S:.3f} s"
    )
    assert added < STATUS_TICK_BLOCK_BOUND_S


# -- model_construct versus validation (TD5) -----------------------------------


async def _ceiling_rows(
    url: str, calendar_url: str, until: datetime
) -> tuple[GetPlan, list[Row]]:
    rows: list[Row] = []
    async with await psycopg.AsyncConnection.connect(url) as conn:
        plan = await contract_plan(
            conn, calendar_url, "ESZ4", BUSIEST_DAY, BUSIEST_DAY + timedelta(days=1)
        )
        window = plan.segments[0]
        clipped = type(window)(
            window.session_date,
            window.instrument_id,
            window.raw_symbol,
            window.open_ns,
            int(until.timestamp() * 10**6) * 1000 - 1,
        )
        await read_ticks(conn, clipped, rows.extend)
    return plan, rows


def _constructed(plan: GetPlan, rows: list[Row]) -> FuturesTicksResponse:
    """The alternative TD5 first proposed: rows built without validation."""
    built = FuturesTicksResponse.from_plan(plan, [], (None, None))
    ticks = [
        TickRecord.model_construct(
            **{n: cell_value(n, v) for n, v in zip(TICK_COLUMNS, r, strict=True)}
        )
        for r in rows
    ]
    return built.model_copy(update={"ticks": ticks, "count": len(ticks)})


def _timed(build: Any) -> float:
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        build().model_dump(mode="json")
        samples.append(time.perf_counter() - started)
    return statistics.median(samples)


async def test_model_construct_versus_validation(
    busiest: str, prod_shaped_db: str
) -> None:
    """Records both figures. 2026-10-13, 75,000 ticks, build plus JSON dump:
    ``model_construct`` 1.446 s / 1.364 s, validated 1.185 s / 1.165 s (two
    runs), so the models validate their rows and TD5 says so. Asserts the
    shipped path is not the slower one by more than noise."""
    plan, rows = await _ceiling_rows(
        busiest, prod_shaped_db, _until_at_ceiling(busiest)
    )
    validated = _timed(lambda: FuturesTicksResponse.from_plan(plan, rows, (None, None)))
    constructed = _timed(lambda: _constructed(plan, rows))
    print(
        f"\n{len(rows)} ticks: model_construct {constructed:.3f} s, "
        f"validated {validated:.3f} s"
    )
    assert len(rows) > 0.9 * API_MAX_BARS_PER_REQUEST
    assert validated < 1.2 * constructed
