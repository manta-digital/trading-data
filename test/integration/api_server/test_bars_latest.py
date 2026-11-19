"""``GET /api/v1/bars?limit=N`` on a real database (issue #13).

The latest-N read anchors on the symbol's held range from the coverage caggs
plus a bounded head probe, so the fixture refreshes the minute rollups and
``minute_coverage`` the way production's policies do. One database per file;
the tests only read.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.api import client_for

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    MINUTE_COVERAGE_VIEW,
    Granularity,
)
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "off_tape"
DAYS = ["SPY_2026-09-08", "SPY_2026-09-11"]
PRICES = ["open", "high", "low", "close"]
#: Refreshed in hierarchy order, then the coverage cagg built on them.
VIEWS = [
    GRANULARITY_SOURCE[g]
    for g in (Granularity.M5, Granularity.M15, Granularity.H1, Granularity.H4)
] + [MINUTE_COVERAGE_VIEW]
#: The 09-11 session's after-hours bars run past midnight UTC into 09-12.
EXPLICIT = (
    "/api/v1/bars/SPY?granularity={g}&start=2026-09-08&end=2026-09-12&adjusted=false"
)
LATEST = "/api/v1/bars/SPY?granularity={g}&limit={n}&adjusted=false"


@pytest.fixture(scope="module")
def client(module_migrated_db: str) -> Iterator[TestClient]:
    url = module_migrated_db
    writer = TimescaleMinuteDataDB(url)
    try:
        for name in DAYS:
            bars = pd.read_csv(FIXTURE / f"{name}.csv")
            bars["time"] = pd.to_datetime(bars["time"], utc=True)
            frame = bars.set_index("time")[[*PRICES, "volume"]]
            assert writer.write_minute_data_bulk("SPY", frame)
    finally:
        writer.close()
    with psycopg.connect(url, autocommit=True) as conn:
        for view in VIEWS:
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    yield from client_for(url, None)


@pytest.mark.parametrize(("granularity", "n"), [("1m", 25), ("5m", 10)])
def test_limit_is_the_tail_of_the_explicit_range(
    client: TestClient, granularity: str, n: int
) -> None:
    explicit = client.get(EXPLICIT.format(g=granularity)).json()["bars"]
    latest = client.get(LATEST.format(g=granularity, n=n)).json()

    assert len(explicit) > n
    assert latest["bars"] == explicit[-n:]
    assert latest["applied"]["end"] == "2026-09-12"


def test_limit_with_end_pages_back(client: TestClient) -> None:
    body = client.get(LATEST.format(g="1m", n=5) + "&end=2026-09-08").json()

    assert body["count"] == 5
    assert all(b["timestamp"] < "2026-09-09" for b in body["bars"])


def test_limit_over_what_is_held_returns_everything(client: TestClient) -> None:
    explicit = client.get(EXPLICIT.format(g="1h")).json()["bars"]
    body = client.get(LATEST.format(g="1h", n=10_000)).json()

    assert body["bars"] == explicit
