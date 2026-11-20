"""``GET /api/v1/bars?session=...`` on a real database (slice 174 task 12.3).

One module database with every minute (04:00-20:00 ET) of the 2026-01-14
session seeded for SPY, the minute rollups and coverage refreshed (``limit``
reads anchor on them). The tests only read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.api import client_for

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)

DAY = date(2026, 1, 14)
FIRST = datetime(2026, 1, 14, 9, tzinfo=UTC)  # 04:00 EST
VIEWS = [
    GRANULARITY_SOURCE[g]
    for g in (Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4)
] + [MINUTE_COVERAGE_VIEW]
BARS = "/api/v1/bars/SPY?granularity={g}&start=2026-01-14&end=2026-01-14&adjusted=false"


@pytest.fixture(scope="module")
def client(module_migrated_db: str) -> Iterator[TestClient]:
    url = module_migrated_db
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO instruments (canonical_id, symbol, asset_class, venue, "
            "trading_calendar_id, eodhd_type, eodhd_exchange) VALUES "
            "('SPY.US', 'SPY', 'etf', 'US', 'NYSE', 'ETF', 'NYSE ARCA')"
        )
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close, "
                "volume) VALUES (%s, 'SPY', 100, 101, 99, 100.5, 10)",
                [(FIRST + timedelta(minutes=i),) for i in range(960)],
            )
    with psycopg.connect(url, autocommit=True) as conn:
        for view in VIEWS:
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    yield from client_for(url, None)


def test_default_response_unchanged_apart_from_applied_session(
    client: TestClient,
) -> None:
    body = client.get(BARS.format(g="1h")).json()
    assert body["applied"]["session"] == "utc"
    assert set(body["bars"][0]) == {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }


def test_session_bars_carry_session_date_and_phase(client: TestClient) -> None:
    body = client.get(BARS.format(g="1h") + "&session=regular").json()
    assert body["applied"]["session"] == "regular"
    assert body["count"] == 7
    assert body["bars"][0]["timestamp"].startswith("2026-01-14T14:30:00")
    assert {b["session_date"] for b in body["bars"]} == {"2026-01-14"}
    assert {b["phase"] for b in body["bars"]} == {"regular"}
    extended = client.get(BARS.format(g="1h") + "&session=extended").json()
    phases = [b["phase"] for b in extended["bars"]]
    assert (phases.count("pre"), phases.count("regular"), phases.count("post")) == (
        6,
        7,
        4,
    )


def test_session_with_daily_granularity_is_refused(client: TestClient) -> None:
    response = client.get(BARS.format(g="1d") + "&session=regular")
    assert response.status_code == 422
    assert response.json()["reason"] == "session_granularity"


def test_past_the_calendar_horizon_is_refused(client: TestClient) -> None:
    url = (
        "/api/v1/bars/SPY?granularity=1h&start=2028-12-01&end=2029-01-02"
        "&session=regular"
    )
    response = client.get(url)
    assert response.status_code == 422
    assert response.json() == {
        "error": response.json()["error"],
        "reason": "calendar_horizon",
        "holidays_seeded_through": "2028-12-31",
    }


def test_over_the_minute_ceiling_is_refused(client: TestClient) -> None:
    # Two years of extended sessions: ~480,000 minutes, ~2,500 4h bars.
    url = (
        "/api/v1/bars/SPY?granularity=4h&start=2024-01-02&end=2025-12-31"
        "&session=extended"
    )
    response = client.get(url)
    assert response.status_code == 422
    body = response.json()
    assert body["reason"] == "minute_ceiling"
    assert body["limit"] == 195_000 and body["estimate"] > 195_000


def test_limit_with_regular_returns_the_latest_session_bars(client: TestClient) -> None:
    body = client.get(
        "/api/v1/bars/SPY?granularity=1h&limit=3&adjusted=false&session=regular"
    ).json()
    assert body["count"] == 3
    assert [b["timestamp"][11:16] for b in body["bars"]] == ["18:30", "19:30", "20:30"]
    assert {b["phase"] for b in body["bars"]} == {"regular"}


def test_unknown_symbol_is_404(client: TestClient) -> None:
    response = client.get(
        "/api/v1/bars/NOPE?granularity=1h&start=2026-01-14&end=2026-01-14&session=regular"
    )
    assert response.status_code == 404
