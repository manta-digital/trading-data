"""Unit tests for the /api/v1/status endpoint and its response models (slice 185).

Mocking boundary is the same one the CLI staleness tests use
(``test/unit/cli/commands/test_data_status_coverage.py``): real
``FreshnessVerdict``/``CoverageFreshness``/``StatusRow`` dataclasses, patched
fetch functions, no DB connection.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db
from manta_trading.api_server.models.responses import (
    CoverageStatus,
    CoverageVerdict,
    StatusRowRecord,
)
from manta_trading.cli.rendering.status_table import HealthStatus, StatusRow
from manta_trading.data.maintenance.status_coverage import (
    COVERAGE_VIEWS,
    CoverageFreshness,
)
from manta_trading.market.maintenance.cagg_freshness import (
    FreshnessVerdict,
    StalenessSignal,
)

# ---------------------------------------------------------------------------
# Fixture builders (same idiom as test_data_status_coverage.py)
# ---------------------------------------------------------------------------


def _verdict(view_name: str, *, is_fresh: bool) -> FreshnessVerdict:
    return FreshnessVerdict(
        view_name=view_name,
        is_fresh=is_fresh,
        signals=() if is_fresh else (StalenessSignal.NOT_SCHEDULED,),
        lag=timedelta(0) if is_fresh else timedelta(days=4),
        threshold=timedelta(days=1),
        detail="test verdict",
    )


def _freshness(*, stale: bool) -> CoverageFreshness:
    return CoverageFreshness(
        verdicts=(
            _verdict(COVERAGE_VIEWS[0], is_fresh=not stale),
            _verdict(COVERAGE_VIEWS[1], is_fresh=True),
        )
    )


def _row(symbol: str = "SPY", granularity: str = "daily") -> StatusRow:
    return StatusRow(
        symbol=symbol,
        granularity=granularity,
        health=HealthStatus.OK,
        bars_stored=1000,
        first_bar_ts=datetime(2004, 1, 2, tzinfo=UTC),
        last_bar_ts=datetime(2026, 8, 1, tzinfo=UTC),
        gap_count=0,
        last_attempt_ts=datetime(2026, 8, 2, tzinfo=UTC),
        last_attempt_outcome="SUCCESS",
        target_end_ts=datetime(2026, 8, 3, tzinfo=UTC),
        effective_start=date(2004, 1, 2),
    )


# ---------------------------------------------------------------------------
# Model mapping
# ---------------------------------------------------------------------------


class TestCoverageModels:
    def test_fresh_freshness_maps_to_two_verdicts_in_order(self) -> None:
        status = CoverageStatus.from_freshness(_freshness(stale=False))
        assert status.is_stale is False
        assert [v.view_name for v in status.verdicts] == list(COVERAGE_VIEWS)
        assert all(v.is_fresh for v in status.verdicts)

    def test_stale_verdict_maps_signals_and_seconds(self) -> None:
        status = CoverageStatus.from_freshness(_freshness(stale=True))
        assert status.is_stale is True
        stale = status.verdicts[0]
        assert stale.is_fresh is False
        assert stale.signals == [StalenessSignal.NOT_SCHEDULED.value]
        assert stale.lag_seconds == timedelta(days=4).total_seconds()
        assert stale.threshold_seconds == timedelta(days=1).total_seconds()

    def test_none_lag_and_threshold_are_preserved_not_zeroed(self) -> None:
        """A silent 0.0 fallback would report 'no lag' for 'could not measure'."""
        verdict = FreshnessVerdict(
            view_name=COVERAGE_VIEWS[0],
            is_fresh=False,
            signals=(StalenessSignal.PROBE_FAILED,),
            lag=None,
            threshold=None,
            detail="probe failed",
        )
        record = CoverageVerdict.from_verdict(verdict)
        assert record.lag_seconds is None
        assert record.threshold_seconds is None


class TestStatusRowRecord:
    def test_round_trips_every_field(self) -> None:
        row = _row()
        record = StatusRowRecord.from_status_row(row)
        assert record.symbol == row.symbol
        assert record.granularity == row.granularity
        assert record.health == row.health
        assert record.bars_stored == row.bars_stored
        assert record.first_bar_ts == row.first_bar_ts
        assert record.last_bar_ts == row.last_bar_ts
        assert record.gap_count == row.gap_count
        assert record.last_attempt_ts == row.last_attempt_ts
        assert record.last_attempt_outcome == row.last_attempt_outcome
        assert record.target_end_ts == row.target_end_ts
        assert record.effective_start == row.effective_start

    def test_nullable_fields_survive_as_none(self) -> None:
        row = StatusRow(
            symbol="AAPL",
            granularity="minute",
            health=HealthStatus.GAPS,
            bars_stored=None,
            first_bar_ts=None,
            last_bar_ts=None,
            gap_count=None,
            last_attempt_ts=None,
            last_attempt_outcome=None,
            target_end_ts=None,
            effective_start=None,
        )
        record = StatusRowRecord.from_status_row(row)
        assert record.bars_stored is None
        assert record.first_bar_ts is None
        assert record.last_bar_ts is None
        assert record.gap_count is None
        assert record.last_attempt_ts is None
        assert record.last_attempt_outcome is None
        assert record.target_end_ts is None
        assert record.effective_start is None


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

_STATUS_MODULE = "manta_trading.api_server.routes.status"
_SUMMARY = {"OK": 12, "GAPS": 3, "STALE": 1, "FAILED": 0}


def _stub_db() -> Iterator[psycopg.Connection[Any]]:
    yield MagicMock(spec=psycopg.Connection)


@pytest.fixture
def test_app() -> FastAPI:
    """A fresh app with ``get_db`` overridden; lifespan is never entered."""
    app = create_app()
    app.state.db_pool = MagicMock(name="sentinel_pool")
    app.dependency_overrides[get_db] = _stub_db
    return app


@contextlib.contextmanager
def _mocked_fetches(
    *, stale: bool = False, rows: list[StatusRow] | None = None
) -> Iterator[tuple[MagicMock, MagicMock]]:
    """Patch both ``status_queries`` calls at the route module's import site."""
    rows = [_row()] if rows is None else rows
    freshness = _freshness(stale=stale)
    with (
        patch(
            f"{_STATUS_MODULE}.fetch_status_rows_with_freshness",
            return_value=(rows, freshness),
        ) as fetch_rows,
        patch(
            f"{_STATUS_MODULE}.fetch_all_health_counts_with_freshness",
            return_value=(_SUMMARY, freshness),
        ) as fetch_counts,
    ):
        yield fetch_rows, fetch_counts


class TestStatusRoute:
    def test_default_request_returns_all_scope(self, test_app: FastAPI) -> None:
        with _mocked_fetches():
            response = TestClient(test_app).get("/api/v1/status")
        assert response.status_code == 200
        body = response.json()
        assert body["scope"] == "all"
        assert body["symbol"] is None
        assert body["count"] == 1
        assert body["summary"] == _SUMMARY
        assert len(body["coverage"]["verdicts"]) == 2
        assert body["coverage"]["is_stale"] is False

    def test_symbol_sets_scope_and_is_forwarded(self, test_app: FastAPI) -> None:
        with _mocked_fetches() as (fetch_rows, _):
            response = TestClient(test_app).get("/api/v1/status?symbol=SPY")
        assert response.status_code == 200
        assert response.json()["scope"] == "symbol"
        assert response.json()["symbol"] == "SPY"
        assert fetch_rows.call_args.kwargs["symbol"] == "SPY"

    def test_unknown_symbol_is_200_with_empty_rows(self, test_app: FastAPI) -> None:
        """D5: zero matching records is a valid answer, not a 404."""
        with _mocked_fetches(rows=[]):
            response = TestClient(test_app).get("/api/v1/status?symbol=NOPE")
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == []
        assert body["count"] == 0
        assert body["scope"] == "symbol"

    def test_all_true_clears_the_health_filter(self, test_app: FastAPI) -> None:
        with _mocked_fetches() as (fetch_rows, _):
            response = TestClient(test_app).get("/api/v1/status?all=true")
        assert response.status_code == 200
        assert fetch_rows.call_args.kwargs["health_filter"] is None

    def test_omitted_health_uses_the_cli_default(self, test_app: FastAPI) -> None:
        with _mocked_fetches() as (fetch_rows, _):
            TestClient(test_app).get("/api/v1/status")
        assert fetch_rows.call_args.kwargs["health_filter"] == [
            HealthStatus.GAPS.value,
            HealthStatus.STALE.value,
            HealthStatus.FAILED.value,
        ]

    def test_explicit_health_is_forwarded_verbatim(self, test_app: FastAPI) -> None:
        with _mocked_fetches() as (fetch_rows, _):
            response = TestClient(test_app).get("/api/v1/status?health=OK,GAPS")
        assert response.status_code == 200
        assert fetch_rows.call_args.kwargs["health_filter"] == ["OK", "GAPS"]

    def test_invalid_health_returns_422(self, test_app: FastAPI) -> None:
        with _mocked_fetches():
            response = TestClient(test_app).get("/api/v1/status?health=BOGUS")
        assert response.status_code == 422
        assert "BOGUS" in response.text

    def test_granularity_is_forwarded(self, test_app: FastAPI) -> None:
        with _mocked_fetches() as (fetch_rows, _):
            response = TestClient(test_app).get("/api/v1/status?granularity=daily")
        assert response.status_code == 200
        assert fetch_rows.call_args.kwargs["granularity"] == "daily"

    def test_unknown_granularity_returns_422(self, test_app: FastAPI) -> None:
        with _mocked_fetches():
            response = TestClient(test_app).get("/api/v1/status?granularity=hourly")
        assert response.status_code == 422

    def test_stale_coverage_still_returns_rows(self, test_app: FastAPI) -> None:
        """Report, don't refuse (D9 / 167 D3a)."""
        with _mocked_fetches(stale=True):
            response = TestClient(test_app).get("/api/v1/status")
        assert response.status_code == 200
        body = response.json()
        assert body["coverage"]["is_stale"] is True
        assert body["count"] == 1
        stale_verdict = body["coverage"]["verdicts"][0]
        assert stale_verdict["is_fresh"] is False
        assert stale_verdict["signals"] == [StalenessSignal.NOT_SCHEDULED.value]

    def test_route_does_not_auto_extend(self, test_app: FastAPI) -> None:
        """D4: the write side effect stays a CLI concern."""
        with (
            _mocked_fetches(),
            patch(
                "manta_trading.data.maintenance.auto_extend"
                ".maybe_extend_trading_sessions"
            ) as auto_extend,
        ):
            response = TestClient(test_app).get("/api/v1/status")
        assert response.status_code == 200
        assert not auto_extend.called

    def test_route_registered(self) -> None:
        paths = {getattr(route, "path", None) for route in create_app().routes}
        assert "/api/v1/status" in paths
