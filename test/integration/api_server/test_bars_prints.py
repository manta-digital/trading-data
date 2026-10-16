"""``GET /api/v1/bars`` with ``prints`` on a real database (slice 171)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from tick_support.api import client_for

from manta_trading.constants import PRINTS_ELIGIBLE_1M_ONLY_MESSAGE
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "off_tape"
URL = "/api/v1/bars/SPY?granularity={g}&start=2026-09-08&end=2026-09-09&adjusted=false"


@pytest.fixture()
def client(migrated_db: str) -> Iterator[TestClient]:
    bars = pd.read_csv(FIXTURE / "SPY_2026-09-08.csv")
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    writer = TimescaleMinuteDataDB(migrated_db)
    assert writer.write_minute_data_bulk(
        "SPY", bars.set_index("time")[["open", "high", "low", "close", "volume"]]
    )
    writer.close()
    yield from client_for(migrated_db, None)


def test_default_serves_raw_bars_and_says_so(client: TestClient) -> None:
    body = client.get(URL.format(g="1m")).json()
    assert body["prints"] == "all"
    raw = pd.read_csv(FIXTURE / "SPY_2026-09-08.csv")
    assert body["count"] == len(raw)
    assert [bar["high"] for bar in body["bars"]] == raw["high"].tolist()
    assert 770.3516 in [bar["high"] for bar in body["bars"]]


def test_eligible_drops_the_after_hours_spikes(client: TestClient) -> None:
    body = client.get(URL.format(g="1m") + "&prints=eligible").json()
    assert body["prints"] == "eligible"
    highs = [bar["high"] for bar in body["bars"]]
    assert 770.3516 not in highs
    # From 16:00 ET (20:00 UTC) the session trades near 766; the 770.35 wicks
    # fall to each bar's highest eligible value.
    after_close = [
        b["high"] for b in body["bars"] if b["timestamp"] >= "2026-09-08T20:00"
    ]
    assert max(after_close) < 767


def test_eligible_above_1m_is_422(client: TestClient) -> None:
    response = client.get(URL.format(g="5m") + "&prints=eligible")
    assert response.status_code == 422
    assert response.json()["error"] == PRINTS_ELIGIBLE_1M_ONLY_MESSAGE


def test_invalid_prints_is_422(client: TestClient) -> None:
    assert client.get(URL.format(g="1m") + "&prints=clean").status_code == 422
