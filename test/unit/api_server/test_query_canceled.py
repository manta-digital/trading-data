"""A cancelled query is ``504``, not ``500`` (slice 186 D10).

The load-bearing claim under test is not just the status code: it is that a
``504`` always means a *data* query was cancelled. A freshness probe that times
out must still yield a ``200`` with a stale verdict, because "narrow the
requested range" is useless advice for a coverage probe.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db, get_db_pool
from manta_trading.constants import API_MAX_BARS_PER_REQUEST, Granularity
from manta_trading.market.maintenance.cagg_freshness import reset_freshness_cache
from manta_trading.market.timescale_daily_db import TimescaleDailyDataDB
from manta_trading.market.timescale_minute_db import TimescaleMinuteDataDB

_FRESHNESS_MODULE = "manta_trading.market.maintenance.cagg_freshness"
_CONFIGURED_TIMEOUT = "37s"
"""Deliberately not the 20s default: a handler that hardcoded the default would
pass every assertion made against it."""


def _cancelled() -> psycopg.errors.QueryCanceled:
    return psycopg.errors.QueryCanceled(
        "canceling statement due to statement timeout"
    )


def _stub_pool() -> MagicMock:
    pool = MagicMock(name="stub_pool")
    pool.connection.return_value.__enter__.return_value = MagicMock(
        spec=psycopg.Connection
    )
    return pool


def _stub_db() -> Iterator[Any]:
    yield MagicMock(spec=psycopg.Connection)


@pytest.fixture(autouse=True)
def _clear_verdict_cache() -> Iterator[None]:
    """Verdicts memoize for 60s; a cached fresh verdict would mask a probe."""
    reset_freshness_cache()
    yield
    reset_freshness_cache()


@pytest.fixture
def test_app() -> FastAPI:
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    app.state.minute_db = MagicMock(spec=TimescaleMinuteDataDB)
    app.state.daily_db = MagicMock(spec=TimescaleDailyDataDB)
    app.state.max_bars_per_request = API_MAX_BARS_PER_REQUEST
    app.state.statement_timeout = _CONFIGURED_TIMEOUT
    app.dependency_overrides[get_db_pool] = _stub_pool
    app.dependency_overrides[get_db] = _stub_db
    return app


def _bars(test_app: FastAPI, granularity: str = "1m") -> Any:
    return TestClient(test_app, raise_server_exceptions=False).get(
        f"/api/v1/bars/SPY?granularity={granularity}"
        "&start=2024-01-01&end=2024-01-03"
    )


class TestCancelledDataQuery:
    def test_cancelled_bars_query_returns_504(self, test_app: FastAPI) -> None:
        test_app.state.minute_db.get_minute_data.side_effect = _cancelled()
        response = _bars(test_app)
        assert response.status_code == 504
        assert set(response.json()) == {"error"}

    def test_message_quotes_the_configured_budget(self, test_app: FastAPI) -> None:
        """An operator who raises MT_API_STATEMENT_TIMEOUT must not be told 20s."""
        test_app.state.minute_db.get_minute_data.side_effect = _cancelled()
        error = _bars(test_app).json()["error"]
        assert _CONFIGURED_TIMEOUT in error
        assert "20s" not in error
        assert "narrow the requested range" in error

    def test_handler_logs_at_warning_with_method_and_path(
        self, test_app: FastAPI, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Operator-actionable and handled — visible, but not a crash."""
        import logging

        test_app.state.minute_db.get_minute_data.side_effect = _cancelled()
        with caplog.at_level(logging.WARNING, logger="manta_trading.api_server.app"):
            _bars(test_app)

        records = [r for r in caplog.records if r.levelname == "WARNING"]
        assert records
        message = records[-1].getMessage()
        assert "/api/v1/bars/SPY" in message
        assert "granularity=1m" in message
        assert _CONFIGURED_TIMEOUT in message

    def test_other_psycopg_errors_still_return_sanitized_500(
        self, test_app: FastAPI
    ) -> None:
        """The 504 handler is strictly narrower than the Exception handler."""
        test_app.state.minute_db.get_minute_data.side_effect = psycopg.OperationalError(
            "connection to server at 192.168.1.144 failed"
        )
        response = _bars(test_app)
        assert response.status_code == 500
        assert response.json() == {"error": "internal server error"}


class TestCancelledProbeIsNotAGatewayTimeout:
    """Review F011: a cancelled *probe* must not surface as ``504``.

    ``cagg_freshness`` catches ``psycopg.Error`` internally and converts it to a
    PROBE_FAILED (stale) verdict, so the exception never reaches the handler.
    Without these assertions, ``504`` could silently come to mean "the coverage
    probe timed out", for which the handler's advice is wrong.
    """

    def test_cancelled_bars_probe_returns_200_and_is_stale(
        self, test_app: FastAPI
    ) -> None:
        test_app.state.minute_db.get_minute_data.return_value = pd.DataFrame(
            {
                "open": [1.0],
                "high": [2.0],
                "low": [0.5],
                "close": [1.5],
                "volume": [10],
            },
            index=pd.date_range("2024-01-02", periods=1, freq="1min", tz="UTC"),
        )
        with patch(
            f"{_FRESHNESS_MODULE}._read_refresh_job", side_effect=_cancelled()
        ):
            response = _bars(test_app, granularity=Granularity.M5.value)

        assert response.status_code == 200
        body = response.json()
        assert body["is_stale"] is True
        assert body["count"] == 1

    def test_cancelled_health_probe_returns_200_and_stale_coverage(
        self, test_app: FastAPI
    ) -> None:
        with patch(
            f"{_FRESHNESS_MODULE}._read_refresh_job", side_effect=_cancelled()
        ):
            response = TestClient(test_app, raise_server_exceptions=False).get(
                "/api/v1/health"
            )

        assert response.status_code == 200
        body = response.json()
        assert body["db"] == "ok"
        assert body["coverage"] == "stale"

    def test_control_an_escaping_probe_cancellation_would_be_a_504(
        self, test_app: FastAPI
    ) -> None:
        """Gives the two tests above their discriminating power.

        Against a mocked connection the probe reports stale for several
        reasons, so ``coverage: "stale"`` alone proves little. What the two
        tests actually pin is that the cancellation never *escapes* — and this
        control shows that if it did, the handler would fire. The internal
        ``except psycopg.Error`` in ``_evaluate`` is therefore load-bearing for
        D10's claim, not incidental.
        """
        test_app.state.minute_db.get_minute_data.return_value = pd.DataFrame()
        with patch(
            "manta_trading.api_server.routes.bars.assert_cagg_fresh",
            side_effect=_cancelled(),
        ):
            response = _bars(test_app, granularity=Granularity.M5.value)
        assert response.status_code == 504


def test_504_is_declared_on_every_data_route() -> None:
    """D7: the status must land in the committed schema, not just in behavior."""
    paths = create_app().openapi()["paths"]
    for path in (
        "/api/v1/bars/{symbol}",
        "/api/v1/status",
        "/api/v1/symbols",
        "/api/v1/symbols/{symbol}",
        "/api/v1/gaps/{symbol}",
    ):
        assert "504" in paths[path]["get"]["responses"], path
