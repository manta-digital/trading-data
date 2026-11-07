"""Unit tests for the Kalshi time-series routes (slice 188, Section 5).

The readers are monkeypatched on the route module and the pool is a fake whose
``connection()`` yields a sentinel, so what is under test is the route's
ordering (seek, count, fetch), its window resolution, and its response facts.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock
from uuid import UUID

import msgpack
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import (
    get_db_pool,
    get_kalshi_trades_excluded,
    get_max_bars,
)
from manta_trading.api_server.routes import kalshi_timeseries as route_module
from manta_trading.data.kalshi.candle_repository import CANDLE_COLUMNS
from manta_trading.data.kalshi.serve_timeseries import (
    CandleRow,
    MarketContext,
    TapeFacts,
    TradeRow,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

TICKER = "KXFED-26SEP-T1"
TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
CEILING = 75_000

CANDLES_PATH = f"/api/v1/kalshi/markets/{TICKER}/candlesticks"
TRADES_PATH = f"/api/v1/kalshi/markets/{TICKER}/trades"


def _context(
    *,
    collected: bool = True,
    category: str | None = "Economics",
) -> MarketContext:
    return MarketContext(
        ticker=TICKER,
        series_category=category,
        candle_collected=collected,
        candle_coverage_from=TS if collected else None,
        candle_complete_through=TS if collected else None,
    )


def _candle_row() -> CandleRow:
    return CandleRow(
        end_period_ts=TS,
        values=tuple(Decimal("0.4900") for _ in CANDLE_COLUMNS),
    )


def _trade_row() -> TradeRow:
    return TradeRow(
        values=(
            TS,
            UUID("4b1d1e2a-0000-4000-8000-000000000001"),
            Decimal("5.00"),
            Decimal("0.4900"),
            Decimal("0.5100"),
            "yes",
            "yes",
            False,
        )
    )


class _FakePool:
    """A pool whose checkout yields a sentinel and records its scope."""

    def __init__(self) -> None:
        self.checkouts = 0
        self.open_now = 0

    @contextmanager
    def connection(self) -> Iterator[Any]:
        self.checkouts += 1
        self.open_now += 1
        try:
            yield MagicMock(name="sentinel_conn")
        finally:
            self.open_now -= 1


@pytest.fixture
def pool() -> _FakePool:
    return _FakePool()


@pytest.fixture
def app(pool: _FakePool) -> FastAPI:
    application = create_app()
    application.state.db_pool = pool
    application.dependency_overrides[get_db_pool] = lambda: pool
    application.dependency_overrides[get_max_bars] = lambda: CEILING
    application.dependency_overrides[get_kalshi_trades_excluded] = lambda: frozenset()
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record every reader call so ordering can be asserted, not assumed."""
    record: dict[str, Any] = {}

    def spy(name: str, result: Any) -> None:
        record[name] = []

        def fake(*args: Any, **kwargs: Any) -> Any:
            record[name].append((args[1:], kwargs))
            return result

        target = (
            route_module.periods
            if name.endswith("period_candles")
            else route_module.series
        )
        monkeypatch.setattr(target, name, fake)

    record["_spy"] = spy
    return record


def _seed(
    calls: dict[str, Any],
    *,
    context: MarketContext | None = None,
    candles: int = 0,
    trades: int = 0,
    facts: TapeFacts | None = None,
) -> None:
    calls["_spy"]("market_context", context)
    calls["_spy"]("count_candles", candles)
    calls["_spy"]("fetch_candles", [_candle_row()] * candles)
    calls["_spy"]("count_period_candles", candles)
    calls["_spy"]("fetch_period_candles", [_candle_row()] * candles)
    calls["_spy"]("count_trades", trades)
    calls["_spy"]("fetch_trades", [_trade_row()] * trades)
    calls["_spy"]("tape_facts", facts)


class TestUnknownMarket:
    @pytest.mark.parametrize(
        ("path", "count_reader"),
        [(CANDLES_PATH, "count_candles"), (TRADES_PATH, "count_trades")],
    )
    def test_404_with_no_count_call(
        self, client: TestClient, calls: dict[str, Any], path: str, count_reader: str
    ) -> None:
        _seed(calls, context=None)
        response = client.get(path)

        assert response.status_code == 404
        assert response.json() == {"error": f"Market '{TICKER}' not found"}
        assert calls[count_reader] == []


class TestCeiling:
    def test_candles_over_ceiling_is_422_with_both_numbers_and_no_fetch(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        calls["_spy"]("count_candles", 132_552)
        response = client.get(CANDLES_PATH)

        assert response.status_code == 422
        message = response.json()["error"]
        assert "132,552" in message
        assert f"{CEILING:,}" in message
        assert calls["fetch_candles"] == []

    def test_trades_over_ceiling_is_422_and_skips_the_fetch(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context())
        calls["_spy"]("count_trades", 90_000)
        response = client.get(TRADES_PATH)

        assert response.status_code == 422
        assert "90,000" in response.json()["error"]
        assert calls["fetch_trades"] == []

    def test_the_connection_is_released_before_the_refusal(
        self, client: TestClient, calls: dict[str, Any], pool: _FakePool
    ) -> None:
        """The 422 is raised after the executor returns (185 D8a)."""
        _seed(calls, context=_context())
        calls["_spy"]("count_candles", 132_552)

        assert client.get(CANDLES_PATH).status_code == 422
        assert pool.checkouts == 1
        assert pool.open_now == 0


class TestEmptyWindowIsUnambiguous:
    """All four meanings of ``count: 0`` are distinguishable from the body
    alone — the point of the D5 per-response facts."""

    def test_genuinely_no_activity(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        body = client.get(CANDLES_PATH).json()

        assert body["count"] == 0
        assert body["collected"] is True
        assert body["complete_through"] == TS.isoformat().replace("+00:00", "Z")

    def test_market_is_not_collected(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(collected=False), candles=0)
        body = client.get(CANDLES_PATH).json()

        assert body["count"] == 0
        assert body["collected"] is False
        assert body["coverage_from"] is None
        assert body["complete_through"] is None

    def test_window_is_past_the_watermark(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """``tape_complete_through`` behind the window explains the zero."""
        facts = TapeFacts(coverage_from=TS, tape_complete_through=TS)
        _seed(calls, context=_context(), trades=0, facts=facts)
        body = client.get(f"{TRADES_PATH}?start=2026-09-01T00:00:00Z").json()

        assert body["count"] == 0
        assert body["tape_filtered"] is False
        assert body["tape_complete_through"] == TS.isoformat().replace("+00:00", "Z")

    def test_category_is_filtered_from_the_tape(
        self, app: FastAPI, client: TestClient, calls: dict[str, Any]
    ) -> None:
        app.dependency_overrides[get_kalshi_trades_excluded] = lambda: frozenset(
            {"Economics"}
        )
        facts = TapeFacts(coverage_from=TS, tape_complete_through=TS)
        _seed(calls, context=_context(category="Economics"), trades=0, facts=facts)
        body = client.get(TRADES_PATH).json()

        assert body["count"] == 0
        assert body["tape_filtered"] is True


class TestPeriodMinutes:
    """``period_minutes`` (issue 61): validated, echoed, and routed to the
    derived-candle readers for anything but the collected minute."""

    @pytest.mark.parametrize("period", [60, 1440])
    def test_coarse_periods_use_the_derived_readers(
        self, client: TestClient, calls: dict[str, Any], period: int
    ) -> None:
        _seed(calls, context=_context(), candles=2)
        body = client.get(f"{CANDLES_PATH}?period_minutes={period}").json()

        assert body["period_minutes"] == period
        assert body["count"] == 2
        assert calls["count_candles"] == []
        assert calls["fetch_candles"] == []
        assert calls["count_period_candles"][0][1]["period"] == period
        assert calls["fetch_period_candles"][0][1]["period"] == period

    def test_one_minute_is_sent_explicitly_and_reads_stored_rows(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=1)
        body = client.get(f"{CANDLES_PATH}?period_minutes=1").json()

        assert body["period_minutes"] == 1
        assert calls["count_period_candles"] == []
        assert len(calls["fetch_candles"]) == 1

    def test_omitted_period_is_the_collected_minute(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=1)
        assert client.get(CANDLES_PATH).json()["period_minutes"] == 1

    @pytest.mark.parametrize("period", [0, 5, 61, 10080, "hour"])
    def test_any_other_period_is_422(
        self, client: TestClient, calls: dict[str, Any], period: Any
    ) -> None:
        _seed(calls, context=_context())
        response = client.get(f"{CANDLES_PATH}?period_minutes={period}")

        assert response.status_code == 422
        assert calls["market_context"] == []

    def test_ceiling_counts_served_rows_for_coarse_periods(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        calls["_spy"]("count_period_candles", CEILING + 1)
        response = client.get(f"{CANDLES_PATH}?period_minutes=60")

        assert response.status_code == 422
        assert calls["fetch_period_candles"] == []

    def test_complete_through_is_the_minute_collection_instant(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=1)
        body = client.get(f"{CANDLES_PATH}?period_minutes=1440").json()

        assert body["complete_through"] == "2026-06-01T12:00:00Z"
        assert calls["market_context"][0][1]["period"] == 1


class TestWindowResolution:
    def test_reversed_range_is_422(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context())
        response = client.get(
            f"{CANDLES_PATH}?start=2026-06-10T00:00:00Z&end=2026-06-01T00:00:00Z"
        )

        assert response.status_code == 422
        assert "is after end" in response.json()["error"]

    def test_reversed_range_is_rejected_before_any_db_work(
        self, client: TestClient, calls: dict[str, Any], pool: _FakePool
    ) -> None:
        _seed(calls, context=_context())
        client.get(
            f"{CANDLES_PATH}?start=2026-06-10T00:00:00Z&end=2026-06-01T00:00:00Z"
        )

        assert pool.checkouts == 0
        assert calls["market_context"] == []

    def test_bare_dates_become_a_half_open_utc_day(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """``D..D`` is ``[D 00:00, D+1 00:00)`` — issue 61."""
        _seed(calls, context=_context(), candles=0)
        client.get(f"{CANDLES_PATH}?start=2026-06-01&end=2026-06-01")

        kwargs = calls["count_candles"][0][1]
        assert kwargs["start"] == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        assert kwargs["end"] == datetime(2026, 6, 2, 0, 0, tzinfo=UTC)

    def test_explicit_datetime_bounds_are_used_as_given(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        client.get(
            f"{CANDLES_PATH}?start=2026-06-01T08:00:00Z&end=2026-06-01T09:00:00Z"
        )

        kwargs = calls["count_candles"][0][1]
        assert kwargs["start"] == datetime(2026, 6, 1, 8, 0, tzinfo=UTC)
        assert kwargs["end"] == datetime(2026, 6, 1, 9, 0, tzinfo=UTC)

    def test_trades_use_the_same_half_open_day(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), trades=0)
        client.get(f"{TRADES_PATH}?start=2026-06-01&end=2026-06-01")

        kwargs = calls["count_trades"][0][1]
        assert kwargs["start"] == datetime(2026, 6, 1, 0, 0, tzinfo=UTC)
        assert kwargs["end"] == datetime(2026, 6, 2, 0, 0, tzinfo=UTC)

    def test_naive_datetime_is_read_as_utc(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        client.get(f"{CANDLES_PATH}?start=2026-06-01T08:30:00")

        assert calls["count_candles"][0][1]["start"] == datetime(
            2026, 6, 1, 8, 30, tzinfo=UTC
        )

    def test_omitted_bounds_reach_the_reader_as_none(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=0)
        client.get(CANDLES_PATH)

        kwargs = calls["count_candles"][0][1]
        assert kwargs["start"] is None
        assert kwargs["end"] is None


class TestSerialization:
    def test_json_is_the_default_with_decimal_strings(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=1)
        response = client.get(CANDLES_PATH)

        assert response.headers["content-type"] == "application/json"
        candle = response.json()["candlesticks"][0]
        assert candle["yes_bid"]["open_dollars"] == "0.4900"

    def test_msgpack_carries_the_same_decimal_strings(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        _seed(calls, context=_context(), candles=1)
        response = client.get(f"{CANDLES_PATH}?format=msgpack")

        assert response.headers["content-type"] == "application/x-msgpack"
        body = msgpack.unpackb(response.content, raw=False)
        assert body["candlesticks"][0]["yes_bid"]["open_dollars"] == "0.4900"

    def test_trades_msgpack_round_trips(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        facts = TapeFacts(coverage_from=TS, tape_complete_through=TS)
        _seed(calls, context=_context(), trades=1, facts=facts)
        response = client.get(f"{TRADES_PATH}?format=msgpack")

        body = msgpack.unpackb(response.content, raw=False)
        assert body["trades"][0]["yes_price_dollars"] == "0.4900"
        assert body["count"] == 1


class TestAbsentTapeState:
    def test_null_facts_are_a_200_not_an_error(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """The trades phase has never run — a fresh install (D10)."""
        _seed(calls, context=_context(), trades=0, facts=None)
        response = client.get(TRADES_PATH)

        assert response.status_code == 200
        body = response.json()
        assert body["coverage_from"] is None
        assert body["tape_complete_through"] is None
        assert body["count"] == 0
