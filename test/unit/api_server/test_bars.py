"""Unit tests for the /api/v1/bars endpoint and supporting models."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call

import msgpack
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_daily_db, get_minute_db
from manta_trading.api_server.models.responses import BarRecord, BarsResponse
from manta_trading.constants import Granularity
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB


def _make_ohlcv_df(n: int) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with ``n`` rows.

    Index: UTC DatetimeIndex at 1-minute spacing from 2024-01-02 09:30.
    Columns: open, high, low, close (float), volume (int).
    """
    index = pd.date_range(
        start="2024-01-02 09:30:00",
        periods=n,
        freq="1min",
        tz="UTC",
    )
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)],
            "close": [100.5 + i for i in range(n)],
            "volume": [1000 + i * 10 for i in range(n)],
        },
        index=index,
    )


@pytest.fixture
def test_app() -> FastAPI:
    """Build a fresh app with DB state mocked; lifespan is not entered."""
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    app.state.minute_db = MagicMock(spec=TimescaleMinuteDataDB)
    app.state.daily_db = MagicMock(spec=TimescaleDailyDataDB)
    return app


def test_from_dataframe_count() -> None:
    df = _make_ohlcv_df(3)
    result = BarsResponse.from_dataframe("SPY", Granularity.D1, True, df)
    assert result.count == 3
    assert len(result.bars) == 3
    assert result.symbol == "SPY"
    assert result.granularity == "1d"


def test_from_dataframe_field_types() -> None:
    df = _make_ohlcv_df(2)
    result = BarsResponse.from_dataframe("SPY", Granularity.D1, True, df)
    bar = result.bars[0]
    assert isinstance(bar.volume, int)
    assert isinstance(bar.open, float)
    assert isinstance(bar.timestamp, datetime)
    assert bar.timestamp.tzinfo is not None
    assert bar.timestamp.tzinfo == timezone.utc


# --- Route tests ---


def test_daily_bars_json(test_app: FastAPI) -> None:
    test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(3)
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-03"
    )
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    body = response.json()
    assert body["count"] == 3
    assert body["granularity"] == "1d"
    assert body["symbol"] == "SPY"
    bar = body["bars"][0]
    for field in ("open", "high", "low", "close", "volume"):
        assert field in bar


def test_minute_routing_and_datetime_conversion(test_app: FastAPI) -> None:
    test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(2)
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=1m&start=2024-01-01&end=2024-01-02"
    )
    assert response.status_code == 200
    assert test_app.state.minute_db.get_minute_data.called
    assert not test_app.state.daily_db.get_daily_data.called
    _args, kwargs = test_app.state.minute_db.get_minute_data.call_args
    start_time = kwargs.get("start_time") if "start_time" in kwargs else _args[1]
    assert start_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def test_msgpack_format(test_app: FastAPI) -> None:
    test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(2)
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-03&format=msgpack"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-msgpack"
    data = msgpack.unpackb(response.content, raw=False)
    assert data["count"] == 2


def test_empty_result_returns_404(test_app: FastAPI) -> None:
    test_app.state.daily_db.get_daily_data.return_value = pd.DataFrame()
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-03"
    )
    assert response.status_code == 404
    body = response.json()
    assert "error" in body
    assert "detail" not in body


def test_invalid_granularity_returns_422(test_app: FastAPI) -> None:
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=bad&start=2024-01-01&end=2024-01-03"
    )
    assert response.status_code == 422


def test_adjusted_false_forwarded(test_app: FastAPI) -> None:
    test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(1)
    client = TestClient(test_app)
    response = client.get(
        "/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-03&adjusted=false"
    )
    assert response.status_code == 200
    _args, kwargs = test_app.state.daily_db.get_daily_data.call_args
    adjusted = kwargs.get("adjusted") if "adjusted" in kwargs else _args[4]
    assert adjusted is False
