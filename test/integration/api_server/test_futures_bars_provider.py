"""The futures bars routes read provider ``ohlcv-1m`` only (slice 306, D2, D3).

The September 2024 roll is seeded with ticks (``roll_seed``) and, beside it,
provider bars: 30 one-minute ESZ4 bars in session 2024-09-18 priced apart from
that session's ticks, a few ESU4 bars, and bar-only sessions the week of
2024-08-26 where no ticks are held. Session 2024-09-20 has ticks and no bars.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.roll_bars import seed_bar_sessions
from tick_support.roll_seed import ESH5, ESU4, ESZ4, seed_september_roll, seed_tier_days
from tick_support.rows import insert_statistics
from tick_support.ticks import Tick, seed_ticks

from manta_trading.data.tick.constants import CME_DATASET, SType, TickSchema
from manta_trading.data.tick.ingest_records import utc_ns
from manta_trading.data.tick.schema_families import SchemaFamily
from manta_trading.data.tick.statistics_constants import StatType
from manta_trading.data.tick.statistics_reads import VolumeClass, volume_check
from manta_trading.data.tick.status_reads import Shape, calendar_sessions

CONTRACT = "/api/v1/futures/contracts/{}/bars"
PRODUCT = "/api/v1/futures/products/{}/bars"
SCHEDULE = "/api/v1/futures/products/ES/schedule"
SESSION = date(2024, 9, 18)
N_BARS = 30
BAR_PRICE = 6_000_000_000_000
TICK_PRICE = 5_650_250_000_000
SIZES = ["1m", "5m", "15m", "1h", "4h", "1d"]
#: 2024-08-19 → 08-30 weekdays: bar-only sessions, ESU4 busiest.
BAR_ONLY = [date(2024, 8, 19) + timedelta(days=n) for n in range(12)]
BAR_ONLY = [d for d in BAR_ONLY if d.weekday() < 5]
SEGMENT_KEYS = ("first_session", "last_session", "raw_symbol", "instrument_id", "basis")


def _at(minute: int) -> int:
    return utc_ns(datetime(2024, 9, 18, 14, minute, tzinfo=UTC))


@pytest.fixture
def client(migrated_tick_db: str, tick_client: TestClient) -> TestClient:
    roll = seed_september_roll(migrated_tick_db)
    seed_ticks(
        migrated_tick_db,
        roll.units[SESSION],
        ESZ4.instrument_id,
        [Tick(_at(n), price=TICK_PRICE + n, sequence=n) for n in range(N_BARS)],
    )
    volumes: dict[tuple[date, int], int] = {
        (day, ESU4.instrument_id): 600 for day in BAR_ONLY
    }
    volumes |= {(day, ESZ4.instrument_id): 300 for day in BAR_ONLY}
    volumes |= {(day, ESH5.instrument_id): 5 for day in BAR_ONLY}
    volumes |= {
        (date(2024, 9, 16), ESU4.instrument_id): 400,
        (date(2024, 9, 17), ESU4.instrument_id): 400,
        (SESSION, ESZ4.instrument_id): N_BARS,
        (date(2024, 9, 19), ESZ4.instrument_id): 700,
    }
    seed_bar_sessions(
        migrated_tick_db,
        volumes,
        {
            (SESSION, ESZ4.instrument_id): [
                (_at(n), BAR_PRICE + n * 250_000_000) for n in range(N_BARS)
            ]
        },
    )
    return tick_client


def _bars(client: TestClient, path: str, **params: str) -> dict[str, Any]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_session_with_ticks_and_no_provider_bars_has_no_bars(
    client: TestClient,
) -> None:
    """Criterion 1: 09-20 holds ticks (and the tick units) and no bar unit."""
    body = _bars(
        client,
        CONTRACT.format("ESZ4"),
        granularity="1m",
        start="2024-09-20",
        end="2024-09-21",
    )

    assert (body["count"], body["bars"]) == (0, [])
    assert [i["session_date"] for i in body["incomplete"]] == ["2024-09-20"]
    assert body["incomplete"][0]["status"] != "complete"
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESZ4"]


def test_the_bars_are_the_providers_not_the_ticks(client: TestClient) -> None:
    body = _bars(
        client,
        CONTRACT.format("ESZ4"),
        granularity="1m",
        start="2024-09-18",
        end="2024-09-19",
    )

    assert body["count"] == N_BARS and body["incomplete"] == []
    assert body["bars"][0]["open"] == "6000"  # the bar's, not the tick's
    assert all(b["volume"] == 1 for b in body["bars"])


@pytest.mark.parametrize("granularity", SIZES)
def test_every_size_names_its_source(client: TestClient, granularity: str) -> None:
    body = _bars(
        client,
        CONTRACT.format("ESZ4"),
        granularity=granularity,
        start="2024-09-18",
        end="2024-09-19",
    )

    assert body["source"] == "ohlcv-1m" and body["granularity"] == granularity
    assert body["count"] > 0


def test_a_product_read_names_its_source_across_the_roll(client: TestClient) -> None:
    body = _bars(
        client,
        PRODUCT.format("ES"),
        granularity="1d",
        start="2024-09-16",
        end="2024-09-20",
    )

    assert body["source"] == "ohlcv-1m"
    assert [s["raw_symbol"] for s in body["segments"]] == ["ESU4", "ESZ4"]


@pytest.mark.parametrize(
    ("start", "end"),
    [("2024-08-26", "2024-08-31"), ("2024-08-26", "2024-09-06")],
    ids=["bar-only", "mixed"],
)
def test_schedule_and_bars_give_the_same_segments(
    client: TestClient, start: str, end: str
) -> None:
    """D3: the family changes the default range and ``incomplete``, never the
    contract a rule picks or its basis."""
    schedule = _bars(client, SCHEDULE, start=start, end=end, rule="v.0")
    bars = _bars(client, PRODUCT.format("ES"), granularity="1d", start=start, end=end)

    as_keys = [{k: s[k] for k in SEGMENT_KEYS} for s in schedule["segments"]]
    assert as_keys and as_keys == [
        {k: s[k] for k in SEGMENT_KEYS} for s in bars["segments"]
    ]
    assert "bar_1m" in {s["basis"] for s in bars["segments"]}


async def test_a_session_failing_the_cross_tier_volume_check_is_served(
    client: TestClient, migrated_tick_db: str, session_migrated_db: str
) -> None:
    """The check belongs to ``mt data tick coverage``: ``incomplete`` carries the
    load verdict only. Cleared volume 1 against 30 traded bars is ``over``."""
    days = [date(2024, 9, 17), date(2024, 9, 18)]
    units = seed_tier_days(migrated_tick_db, days, TickSchema.STATISTICS)
    with psycopg.connect(migrated_tick_db, autocommit=True) as conn:
        insert_statistics(
            conn,
            units[SESSION],
            instrument_id=ESZ4.instrument_id,
            stat_type=int(StatType.CLEARED_VOLUME),
            quantity=1,
            price=None,
            session_date=SESSION,
            sequence=0,
        )
    start = datetime(2024, 9, 17, tzinfo=UTC)
    sessions = [
        s
        for s in calendar_sessions(
            session_migrated_db, "CME_EQUITY", start, start + timedelta(days=3)
        )
        if s.session_date == SESSION
    ]
    async with await psycopg.AsyncConnection.connect(migrated_tick_db) as aconn:
        verdicts = await volume_check(
            aconn,
            Shape(CME_DATASET, ("ES.FUT",), SType.PARENT),
            SchemaFamily.BAR_1M,
            "ES",
            sessions,
        )
    assert [v.verdict for v in verdicts if v.instrument_id == ESZ4.instrument_id] == [
        VolumeClass.OVER
    ]

    body = _bars(
        client,
        CONTRACT.format("ESZ4"),
        granularity="1m",
        start="2024-09-18",
        end="2024-09-19",
    )

    assert body["count"] == N_BARS and body["incomplete"] == []
