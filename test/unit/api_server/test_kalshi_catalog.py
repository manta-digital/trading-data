"""Unit tests for the Kalshi catalog routes (slice 188, Section 3).

Follows ``test_symbols.py``: a real app, a sentinel connection injected through
``get_db``, and the ``serve_catalog`` readers monkeypatched on the route
module. The database is never touched — what is under test is the route's
ordering (seek, then guard, then fetch), its status codes, and its messages.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from manta_trading.api_server.app import create_app
from manta_trading.api_server.deps import get_db, get_max_bars
from manta_trading.api_server.routes import kalshi_catalog as route_module
from manta_trading.data.kalshi.constants import MarketStatus
from manta_trading.data.kalshi.serve_catalog import (
    CategoryCount,
    EventRow,
    MarketRow,
    SeriesRow,
)

TS = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
CEILING = 75_000


def _series(ticker: str = "KXFED", category: str | None = "Economics") -> SeriesRow:
    return SeriesRow(
        ticker=ticker,
        frequency="monthly",
        title="Fed decision",
        category=category,
        tags=None,
        settlement_sources=None,
        fee_type=None,
        fee_multiplier=Decimal("0.0700"),
        contract_url=None,
        contract_terms_url=None,
        product_metadata=None,
        last_updated_ts=TS,
        first_seen_at=TS,
        last_synced_at=TS,
    )


def _event(event_ticker: str = "KXFED-26SEP") -> EventRow:
    return EventRow(
        event_ticker=event_ticker,
        series_ticker="KXFED",
        title=None,
        sub_title=None,
        category=None,
        mutually_exclusive=None,
        strike_date=TS,
        strike_period=None,
        collateral_return_type=None,
        available_on_brokers=None,
        settlement_sources=None,
        product_metadata=None,
        last_updated_ts=TS,
        first_seen_at=TS,
        last_synced_at=TS,
    )


def _market(ticker: str = "KXFED-26SEP-T1") -> MarketRow:
    fields: dict[str, Any] = {}
    for name, field in MarketRow.__dataclass_fields__.items():
        text = field.type if isinstance(field.type, str) else str(field.type)
        if "datetime" in text:
            fields[name] = TS
        elif "Decimal" in text:
            fields[name] = Decimal("0.4900")
        elif "bool" in text:
            fields[name] = False
        else:
            fields[name] = None
    fields["ticker"] = ticker
    fields["event_ticker"] = "KXFED-26SEP"
    fields["status"] = MarketStatus.ACTIVE.value
    return MarketRow(**fields)


@pytest.fixture
def app() -> FastAPI:
    """Fresh app with the connection and the ceiling injected."""
    application = create_app()
    application.state.db_pool = MagicMock(name="sentinel_pool")
    application.dependency_overrides[get_db] = lambda: MagicMock(name="sentinel_conn")
    application.dependency_overrides[get_max_bars] = lambda: CEILING
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Record every reader call so ordering can be asserted, not assumed."""
    record: dict[str, list[Any]] = {}

    def spy(name: str, result: Any) -> Any:
        record[name] = []

        def fake(*args: Any, **kwargs: Any) -> Any:
            record[name].append((args[1:], kwargs))
            return result() if callable(result) else result

        monkeypatch.setattr(route_module.catalog, name, fake)
        return fake

    record["_spy"] = spy  # type: ignore[assignment]
    return record


class TestCategories:
    def test_returns_rows_in_order_with_category_count(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"](
            "fetch_categories",
            [
                CategoryCount(category="Economics", series_count=2),
                CategoryCount(category="Politics", series_count=40),
            ],
        )
        response = client.get("/api/v1/kalshi/categories")

        assert response.status_code == 200
        body = response.json()
        # count is the number of categories, not the series total.
        assert body["count"] == 2
        assert [c["category"] for c in body["categories"]] == [
            "Economics",
            "Politics",
        ]
        assert body["categories"][1]["series_count"] == 40

    def test_issues_no_count_guard_call(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """Its row count is the number of distinct categories; no guard."""
        calls["_spy"]("fetch_categories", [])
        calls["_spy"]("count_series", 999_999)

        assert client.get("/api/v1/kalshi/categories").status_code == 200
        assert calls["count_series"] == []


class TestSeeks:
    @pytest.mark.parametrize(
        ("path", "reader", "row", "key"),
        [
            ("/api/v1/kalshi/series/KXFED", "fetch_series", _series(), "ticker"),
            (
                "/api/v1/kalshi/events/KXFED-26SEP",
                "fetch_event",
                _event(),
                "event_ticker",
            ),
            (
                "/api/v1/kalshi/markets/KXFED-26SEP-T1",
                "fetch_market",
                _market(),
                "ticker",
            ),
        ],
    )
    def test_returns_the_record(
        self,
        client: TestClient,
        calls: dict[str, Any],
        path: str,
        reader: str,
        row: Any,
        key: str,
    ) -> None:
        calls["_spy"](reader, row)
        response = client.get(path)

        assert response.status_code == 200
        assert response.json()[key] == getattr(row, key)

    @pytest.mark.parametrize(
        ("path", "reader", "resource", "ticker"),
        [
            ("/api/v1/kalshi/series/NOPE", "fetch_series", "Series", "NOPE"),
            ("/api/v1/kalshi/events/NOPE", "fetch_event", "Event", "NOPE"),
            ("/api/v1/kalshi/markets/NOPE", "fetch_market", "Market", "NOPE"),
        ],
    )
    def test_unknown_ticker_is_404_with_error_body(
        self,
        client: TestClient,
        calls: dict[str, Any],
        path: str,
        reader: str,
        resource: str,
        ticker: str,
    ) -> None:
        calls["_spy"](reader, None)
        response = client.get(path)

        assert response.status_code == 404
        assert response.json() == {"error": f"{resource} '{ticker}' not found"}


class TestSeriesList:
    def test_filters_reach_the_reader(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("count_series", 1)
        calls["_spy"]("fetch_series_list", [_series()])
        response = client.get("/api/v1/kalshi/series?category=Economics&search=KXF")

        assert response.status_code == 200
        assert response.json()["count"] == 1
        for name in ("count_series", "fetch_series_list"):
            assert calls[name][0][1] == {"category": "Economics", "search": "KXF"}

    def test_empty_result_is_200_with_count_zero(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """A filter matching nothing is a valid answer, not an error (SC3)."""
        calls["_spy"]("count_series", 0)
        calls["_spy"]("fetch_series_list", [])
        response = client.get("/api/v1/kalshi/series?search=ZZZZ")

        assert response.status_code == 200
        assert response.json() == {"count": 0, "series": []}

    def test_over_ceiling_is_422_naming_both_numbers_and_skips_the_fetch(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("count_series", 132_552)
        calls["_spy"]("fetch_series_list", [])
        response = client.get("/api/v1/kalshi/series")

        assert response.status_code == 422
        message = response.json()["error"]
        assert "132,552" in message
        assert f"{CEILING:,}" in message
        assert calls["fetch_series_list"] == []


class TestEventsList:
    def test_unknown_series_is_404_before_any_count(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """The parent seek runs first, so a 404 costs no aggregate."""
        calls["_spy"]("fetch_series", None)
        calls["_spy"]("count_events", 0)
        calls["_spy"]("fetch_events", [])
        response = client.get("/api/v1/kalshi/series/NOPE/events")

        assert response.status_code == 404
        assert response.json() == {"error": "Series 'NOPE' not found"}
        assert calls["count_events"] == []
        assert calls["fetch_events"] == []

    def test_known_series_with_no_events_is_200_empty(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_series", _series())
        calls["_spy"]("count_events", 0)
        calls["_spy"]("fetch_events", [])
        response = client.get("/api/v1/kalshi/series/KXFED/events")

        assert response.status_code == 200
        assert response.json() == {
            "series_ticker": "KXFED",
            "count": 0,
            "events": [],
        }

    def test_strike_bounds_reach_the_reader_as_datetimes(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_series", _series())
        calls["_spy"]("count_events", 1)
        calls["_spy"]("fetch_events", [_event()])
        response = client.get(
            "/api/v1/kalshi/series/KXFED/events"
            "?strike_from=2026-09-01T00:00:00Z&strike_to=2026-09-30T00:00:00Z"
        )

        assert response.status_code == 200
        kwargs = calls["fetch_events"][0][1]
        assert kwargs["strike_from"] == datetime(2026, 9, 1, tzinfo=UTC)
        assert kwargs["strike_to"] == datetime(2026, 9, 30, tzinfo=UTC)

    def test_over_ceiling_is_422_and_skips_the_fetch(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_series", _series())
        calls["_spy"]("count_events", 80_000)
        calls["_spy"]("fetch_events", [])
        response = client.get("/api/v1/kalshi/series/KXFED/events")

        assert response.status_code == 422
        assert "80,000" in response.json()["error"]
        assert calls["fetch_events"] == []


class TestMarketsList:
    def test_status_reaches_the_reader_as_separate_values(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_event", _event())
        calls["_spy"]("count_markets", 1)
        calls["_spy"]("fetch_markets", [_market()])
        response = client.get(
            "/api/v1/kalshi/events/KXFED-26SEP/markets?status=active,finalized"
        )

        assert response.status_code == 200
        assert calls["fetch_markets"][0][1] == {"statuses": ["active", "finalized"]}

    def test_unknown_event_is_404_before_any_count(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_event", None)
        calls["_spy"]("count_markets", 0)
        calls["_spy"]("fetch_markets", [])
        response = client.get("/api/v1/kalshi/events/NOPE/markets")

        assert response.status_code == 404
        assert calls["count_markets"] == []

    def test_invalid_status_is_422_naming_the_valid_set(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """``open`` is Kalshi's filter word; the column holds ``active``."""
        calls["_spy"]("fetch_event", _event())
        response = client.get("/api/v1/kalshi/events/KXFED-26SEP/markets?status=open")

        assert response.status_code == 422
        message = response.json()["error"]
        assert "Invalid status values: open" in message
        for member in MarketStatus:
            assert member.value in message

    def test_present_but_empty_status_is_422(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_event", _event())
        response = client.get("/api/v1/kalshi/events/KXFED-26SEP/markets?status=")

        assert response.status_code == 422
        assert "provided but empty" in response.json()["error"]

    def test_invalid_status_is_rejected_before_the_parent_seek(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        """Validation is free; the seek is not."""
        calls["_spy"]("fetch_event", _event())
        response = client.get("/api/v1/kalshi/events/KXFED-26SEP/markets?status=bogus")

        assert response.status_code == 422
        assert calls["fetch_event"] == []

    def test_omitted_status_means_no_filter(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_event", _event())
        calls["_spy"]("count_markets", 1)
        calls["_spy"]("fetch_markets", [_market()])
        response = client.get("/api/v1/kalshi/events/KXFED-26SEP/markets")

        assert response.status_code == 200
        assert calls["fetch_markets"][0][1] == {"statuses": None}


class TestStatusVocabulary:
    def test_valid_set_is_derived_from_the_enum(self) -> None:
        """Adding a member to MarketStatus extends the accepted set alone."""
        assert set(route_module._VALID_STATUS) == {m.value for m in MarketStatus}

    def test_every_served_status_is_accepted(
        self, client: TestClient, calls: dict[str, Any]
    ) -> None:
        calls["_spy"]("fetch_event", _event())
        calls["_spy"]("count_markets", 0)
        calls["_spy"]("fetch_markets", [])
        every = ",".join(m.value for m in MarketStatus)
        response = client.get(
            f"/api/v1/kalshi/events/KXFED-26SEP/markets?status={every}"
        )

        assert response.status_code == 200
