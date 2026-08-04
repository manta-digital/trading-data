"""Unit tests for the /api/v1/bars endpoint and supporting models."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import msgpack
import pandas as pd
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db_pool
from manta_trading.api_server.models.responses import BarsResponse
from manta_trading.constants import (
    API_MAX_BARS_PER_REQUEST,
    API_SERVING_SESSION,
    BARS_PER_TRADING_DAY,
    GRANULARITY_SOURCE,
    TRADING_DAYS_PER_CALENDAR_DAY,
    Granularity,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_BARS_MODULE = "manta_trading.api_server.routes.bars"


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


def _stub_pool() -> MagicMock:
    """A pool whose ``connection()`` context manager yields a mock connection."""
    pool = MagicMock(name="stub_pool")
    pool.connection.return_value.__enter__.return_value = MagicMock(
        spec=psycopg.Connection
    )
    return pool


def _verdict(view_name: str, *, is_fresh: bool) -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=is_fresh,
        signals=() if is_fresh else (StalenessSignal.NOT_SCHEDULED,),
        lag=timedelta(0) if is_fresh else timedelta(days=4),
        threshold=timedelta(days=1),
        detail="test verdict",
    )


@contextlib.contextmanager
def _mocked_probe(*, is_fresh: bool) -> Iterator[MagicMock]:
    """Patch ``assert_cagg_fresh`` at the bars module's import site."""
    with patch(
        f"{_BARS_MODULE}.assert_cagg_fresh",
        side_effect=lambda _conn, view_name: _verdict(view_name, is_fresh=is_fresh),
    ) as probe:
        yield probe


@pytest.fixture
def test_app() -> FastAPI:
    """Build a fresh app with DB state mocked; lifespan is not entered.

    ``get_db_pool`` is overridden explicitly so no test depends on the sentinel
    pool MagicMock happening to resolve. The policy values the lifespan would
    normally resolve (186 D9) are set here for the same reason.
    """
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    app.state.minute_db = MagicMock(spec=TimescaleMinuteDataDB)
    app.state.daily_db = MagicMock(spec=TimescaleDailyDataDB)
    app.state.max_bars_per_request = API_MAX_BARS_PER_REQUEST
    app.state.statement_timeout = API_SERVING_SESSION.statement_timeout
    app.dependency_overrides[get_db_pool] = _stub_pool
    return app


def test_from_dataframe_count() -> None:
    df = _make_ohlcv_df(3)
    result = BarsResponse.from_dataframe(
        "SPY", Granularity.D1, True, df, is_stale=False
    )
    assert result.count == 3
    assert len(result.bars) == 3
    assert result.symbol == "SPY"
    assert result.granularity == "1d"
    assert result.is_stale is False


def test_from_dataframe_field_types() -> None:
    df = _make_ohlcv_df(2)
    result = BarsResponse.from_dataframe("SPY", Granularity.D1, True, df, is_stale=True)
    bar = result.bars[0]
    assert isinstance(bar.volume, int)
    assert isinstance(bar.open, float)
    assert isinstance(bar.timestamp, datetime)
    assert bar.timestamp.tzinfo is not None
    assert bar.timestamp.tzinfo == UTC
    assert result.is_stale is True


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
    assert start_time == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


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


# --- Staleness (slice 185 D7) ---


class TestBarsStaleness:
    def test_cagg_granularity_fresh(self, test_app: FastAPI) -> None:
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(3)
        with _mocked_probe(is_fresh=True) as probe:
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=5m&start=2024-01-01&end=2024-01-03"
            )
        assert response.status_code == 200
        assert response.json()["is_stale"] is False
        probe.assert_called_once()
        assert probe.call_args.args[1] == GRANULARITY_SOURCE[Granularity.M5]

    def test_cagg_granularity_stale_still_returns_bars(self, test_app: FastAPI) -> None:
        """Report, don't refuse: a stale cagg is still a 200 with its rows."""
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(3)
        with _mocked_probe(is_fresh=False):
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=5m&start=2024-01-01&end=2024-01-03"
            )
        assert response.status_code == 200
        body = response.json()
        assert body["is_stale"] is True
        assert body["count"] == 3

    def test_daily_family_cagg_resolves_through_granularity_source(
        self, test_app: FastAPI
    ) -> None:
        test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(2)
        with _mocked_probe(is_fresh=True) as probe:
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=1mo&start=2024-01-01&end=2024-06-01"
            )
        assert response.status_code == 200
        assert probe.call_args.args[1] == GRANULARITY_SOURCE[Granularity.MO1]

    @pytest.mark.parametrize(
        ("granularity", "db_attr", "method"),
        [
            ("1m", "minute_db", "get_minute_data"),
            ("1d", "daily_db", "get_daily_data"),
        ],
    )
    def test_raw_granularities_are_never_probed(
        self, test_app: FastAPI, granularity: str, db_attr: str, method: str
    ) -> None:
        getattr(getattr(test_app.state, db_attr), method).return_value = _make_ohlcv_df(
            2
        )
        with _mocked_probe(is_fresh=False) as probe:
            response = TestClient(test_app).get(
                f"/api/v1/bars/SPY?granularity={granularity}"
                "&start=2024-01-01&end=2024-01-03"
            )
        assert response.status_code == 200
        assert response.json()["is_stale"] is False
        assert not probe.called

    def test_raw_granularity_checks_out_no_connection(self, test_app: FastAPI) -> None:
        """The pool is size 8; a request that never probes must not hold a slot.

        Regression guard: an earlier revision took ``Depends(get_db)`` on
        ``get_bars``, so every bars request held a pooled connection for its
        full duration even at ``1d``, where none is used. Eight concurrent bars
        requests then exhausted the pool and stalled ``/health`` (measured:
        0.010s → 4.03s).
        """
        test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(2)
        pool = _stub_pool()
        test_app.dependency_overrides[get_db_pool] = lambda: pool
        with _mocked_probe(is_fresh=True):
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=1d&start=2024-01-01&end=2024-01-03"
            )
        assert response.status_code == 200
        assert not pool.connection.called

    def test_cagg_granularity_checks_out_exactly_one_connection(
        self, test_app: FastAPI
    ) -> None:
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(2)
        pool = _stub_pool()
        test_app.dependency_overrides[get_db_pool] = lambda: pool
        with _mocked_probe(is_fresh=True):
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=5m&start=2024-01-01&end=2024-01-03"
            )
        assert response.status_code == 200
        assert pool.connection.call_count == 1

    def test_msgpack_carries_is_stale(self, test_app: FastAPI) -> None:
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(2)
        with _mocked_probe(is_fresh=False):
            response = TestClient(test_app).get(
                "/api/v1/bars/SPY?granularity=5m"
                "&start=2024-01-01&end=2024-01-03&format=msgpack"
            )
        assert response.status_code == 200
        data = msgpack.unpackb(response.content, raw=False)
        assert data["is_stale"] is True


# --- Range admission cap (slice 186 D4) -------------------------------------


def _exploding_pool() -> MagicMock:
    """A pool that fails loudly if any connection is checked out."""
    pool = MagicMock(name="exploding_pool")
    pool.connection.side_effect = AssertionError(
        "a rejected request must not check out a connection"
    )
    return pool


def _span_for(granularity: Granularity, ceiling: int) -> int:
    """The maximum admissible inclusive span in days, computed as the route does."""
    per_day = BARS_PER_TRADING_DAY[granularity] * TRADING_DAYS_PER_CALENDAR_DAY
    return int(ceiling / per_day)


def _request(
    test_app: FastAPI, granularity: str, start: date, end: date
) -> Any:
    return TestClient(test_app).get(
        f"/api/v1/bars/SPY?granularity={granularity}"
        f"&start={start.isoformat()}&end={end.isoformat()}"
    )


class TestRangeAdmission:
    def test_twenty_year_minute_request_is_rejected(self, test_app: FastAPI) -> None:
        response = _request(test_app, "1m", date(2004, 1, 1), date(2024, 1, 1))
        assert response.status_code == 422
        body = response.json()
        assert set(body) == {"error"}
        assert "75,000" in body["error"]
        assert "113 days" in body["error"]

    def test_rejected_request_checks_out_no_connection(
        self, test_app: FastAPI
    ) -> None:
        """The point of the decision, not a side effect: a request that cannot
        be served must cost one comparison, not a pooled connection and an
        executor thread."""
        pool = _exploding_pool()
        test_app.dependency_overrides[get_db_pool] = lambda: pool
        response = _request(test_app, "1m", date(2004, 1, 1), date(2024, 1, 1))
        assert response.status_code == 422
        assert not test_app.state.minute_db.get_minute_data.called
        assert not test_app.state.daily_db.get_daily_data.called

    @pytest.mark.parametrize(
        "granularity", [Granularity.M1, Granularity.M5, Granularity.M15]
    )
    def test_boundary_is_exact(
        self, test_app: FastAPI, granularity: Granularity
    ) -> None:
        """One day inside the limit is admitted; one day outside is rejected."""
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(1)
        max_days = _span_for(granularity, API_MAX_BARS_PER_REQUEST)
        start = date(2024, 1, 1)

        with _mocked_probe(is_fresh=True):
            inside = _request(
                test_app,
                granularity.value,
                start,
                start + timedelta(days=max_days - 1),
            )
            outside = _request(
                test_app, granularity.value, start, start + timedelta(days=max_days)
            )
        assert inside.status_code == 200
        assert outside.status_code == 422

    def test_daily_grain_is_never_capped(self, test_app: FastAPI) -> None:
        """At ``1d`` and coarser the cap is invisible — 20 years is ~5,000 bars."""
        test_app.state.daily_db.get_daily_data.return_value = _make_ohlcv_df(2)
        response = _request(test_app, "1d", date(2004, 1, 1), date(2024, 1, 1))
        assert response.status_code == 200

    def test_reversed_range_is_rejected(self, test_app: FastAPI) -> None:
        """Before 186 this returned an empty frame and a misleading 404."""
        pool = _exploding_pool()
        test_app.dependency_overrides[get_db_pool] = lambda: pool
        response = _request(test_app, "1d", date(2024, 3, 1), date(2024, 1, 1))
        assert response.status_code == 422
        error = response.json()["error"]
        assert "2024-03-01" in error
        assert "2024-01-01" in error
        assert not test_app.state.daily_db.get_daily_data.called

    def test_same_day_range_is_admitted(self, test_app: FastAPI) -> None:
        """start == end is one day, not zero — the window is inclusive."""
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(1)
        response = _request(test_app, "1m", date(2024, 6, 10), date(2024, 6, 10))
        assert response.status_code == 200

    def test_message_quotes_the_configured_ceiling(self, test_app: FastAPI) -> None:
        """An override must not produce a message contradicting the enforced
        limit — both the ceiling and the span are computed from the live value.
        """
        test_app.state.max_bars_per_request = 1_000
        response = _request(test_app, "1m", date(2024, 1, 1), date(2024, 3, 1))
        assert response.status_code == 422
        error = response.json()["error"]
        assert "1,000 bar limit" in error
        assert "75,000" not in error
        assert f"{_span_for(Granularity.M1, 1_000):,} days" in error

    def test_ceiling_comes_from_app_state_not_a_literal(
        self, test_app: FastAPI
    ) -> None:
        """A raised ceiling admits what the default rejects."""
        test_app.state.minute_db.get_minute_data.return_value = _make_ohlcv_df(1)
        window = (date(2024, 1, 1), date(2024, 12, 31))

        rejected = _request(test_app, "1m", *window)
        test_app.state.max_bars_per_request = 10_000_000
        admitted = _request(test_app, "1m", *window)

        assert rejected.status_code == 422
        assert admitted.status_code == 200
