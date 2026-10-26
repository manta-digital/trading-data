"""``GET /api/v1/bars`` with ``prints`` on a real database (slices 171, 173).

The chain runs through 064, so the rollups under their current names hold
eligible values. One database per file: every fixture day and a hand-written
edge symbol are written and the 5m and 1h rollups refreshed once; the tests
only read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import psycopg
import pytest
from fastapi.testclient import TestClient
from tick_support.api import client_for

from manta_trading.constants import (
    GRANULARITY_SOURCE,
    OFF_TAPE_DAY_TZ,
    PRINTS_ALL_1M_ONLY_MESSAGE,
    Granularity,
    OffTape,
)
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "off_tape"
URL = "/api/v1/bars/SPY?granularity={g}&start=2026-09-08&end=2026-09-09&adjusted=false"
PRICES = ["open", "high", "low", "close"]
ROLLED_UP = (Granularity.M5, Granularity.H1)

KNOWN = [  # (fixture, ET minutes, value): 171's success criterion 1
    ("SPY_2026-09-08", ["07:35", "07:36", "07:37", "07:38"], 770.19),
    ("SPY_2026-09-08", ["16:01", "16:03", "16:04"], 770.3516),
    ("SPY_2026-09-08", ["16:52", "17:26"], 724.228),
    ("SPY_2026-09-11", ["17:33"], 787.5169),
    ("AVGO_2026-06-03", ["16:15"], 295.4665),
    ("MSFT_2026-08-05", ["16:13"], 384.28),
]
FIXTURE_DAYS = sorted({name for name, *_ in KNOWN})

_EDGE_T0 = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)  # a 5m bucket start


def _edge_rows() -> list[tuple[object, ...]]:
    """Bucket 1 clean, bucket 2 only all-marked minutes, bucket 3 clean."""
    rows: list[tuple[object, ...]] = []
    for offset in range(15):
        mark = int(OffTape.ALL) if 5 <= offset < 10 else 0
        high = "500" if mark else "101"
        stamp = _EDGE_T0 + timedelta(minutes=offset)
        rows.append((stamp, "EDGE", "100", high, "99", "100.5", 10, mark))
    return rows


def _load(name: str) -> pd.DataFrame:
    bars = pd.read_csv(FIXTURE / f"{name}.csv")
    bars["time"] = pd.to_datetime(bars["time"], utc=True)
    return bars


@pytest.fixture(scope="module")
def client(module_migrated_db: str) -> Iterator[TestClient]:
    url = module_migrated_db
    writer = TimescaleMinuteDataDB(url)
    try:
        for name in FIXTURE_DAYS:
            bars = _load(name)
            frame = bars.set_index("time")[[*PRICES, "volume"]]
            assert writer.write_minute_data_bulk(bars["symbol"].iloc[0], frame)
    finally:
        writer.close()
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO minute_ohlcv (time, symbol, open, high, low, close,"
                " volume, off_tape) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                _edge_rows(),
            )
        for granularity in ROLLED_UP:
            view = GRANULARITY_SOURCE[granularity]
            conn.execute(f"CALL refresh_continuous_aggregate('{view}', NULL, NULL)")
    yield from client_for(url, None)


def test_default_serves_eligible_bars_and_says_so(client: TestClient) -> None:
    body = client.get(URL.format(g="1m")).json()
    assert body["prints"] == "eligible"
    assert 770.3516 not in [bar["high"] for bar in body["bars"]]
    # From 16:00 ET (20:00 UTC) the session trades near 766; the 770.35 wicks
    # fall to each bar's highest eligible value.
    after_close = [
        b["high"] for b in body["bars"] if b["timestamp"] >= "2026-09-08T20:00"
    ]
    assert max(after_close) < 767


def test_all_at_1m_serves_raw_bars(client: TestClient) -> None:
    body = client.get(URL.format(g="1m") + "&prints=all").json()
    assert body["prints"] == "all"
    raw = _load("SPY_2026-09-08")
    assert body["count"] == len(raw)
    assert [bar["high"] for bar in body["bars"]] == raw["high"].tolist()


def test_default_at_5m_is_eligible(client: TestClient) -> None:
    body = client.get(URL.format(g="5m")).json()
    assert body["prints"] == "eligible"
    assert body["count"] > 0


def test_all_above_1m_is_422(client: TestClient) -> None:
    response = client.get(URL.format(g="5m") + "&prints=all")
    assert response.status_code == 422
    assert response.json()["error"] == PRINTS_ALL_1M_ONLY_MESSAGE


def test_invalid_prints_is_422(client: TestClient) -> None:
    assert client.get(URL.format(g="1m") + "&prints=clean").status_code == 422


def test_a_bucket_of_only_marked_minutes_is_absent(client: TestClient) -> None:
    body = client.get(
        "/api/v1/bars/EDGE?granularity=5m&start=2026-09-08&end=2026-09-08"
        "&adjusted=false"
    ).json()
    stamps = [pd.Timestamp(bar["timestamp"]) for bar in body["bars"]]
    assert stamps == [_EDGE_T0, _EDGE_T0 + timedelta(minutes=10)]
    assert all(bar["high"] == 101 for bar in body["bars"])


_FLOOR = {Granularity.M5: "5min", Granularity.H1: "1h"}


@pytest.mark.parametrize("granularity", ROLLED_UP, ids=lambda g: g.value)
@pytest.mark.parametrize(("name", "minutes", "value"), KNOWN)
def test_known_off_tape_value_absent_from_rollup_bars(
    client: TestClient,
    granularity: Granularity,
    name: str,
    minutes: list[str],
    value: float,
) -> None:
    """Success criterion 1: the value is gone from every bucket holding one
    of its minutes, served through the API."""
    symbol, day = name.split("_")
    stamps = [pd.Timestamp(f"{day} {m}", tz=OFF_TAPE_DAY_TZ) for m in minutes]
    buckets = {s.tz_convert(UTC).floor(_FLOOR[granularity]) for s in stamps}
    raw = _load(name).set_index("time").loc[[s.tz_convert(UTC) for s in stamps]]
    assert (raw[PRICES].astype(float) - value).abs().lt(5e-5).to_numpy().any()
    end = (pd.Timestamp(day) + pd.Timedelta(days=1)).date().isoformat()
    body = client.get(
        f"/api/v1/bars/{symbol}?granularity={granularity.value}&start={day}"
        f"&end={end}&adjusted=false"
    ).json()
    served = [b for b in body["bars"] if pd.Timestamp(b["timestamp"]) in buckets]
    assert len(served) == len(buckets)
    hits = [b for b in served if any(abs(b[p] - value) < 5e-5 for p in PRICES)]
    assert hits == [], f"{value} ({minutes} ET) present in {granularity.value} bars"
