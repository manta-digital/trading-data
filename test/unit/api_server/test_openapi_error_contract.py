"""The committed schema's promises about every route (issues #51 and #23).

Read from the committed artifact, as ``test_openapi_operations_schema`` is:
the artifact is what trading-engine and trading-ui generate clients from.

- every route publishes a ``200`` schema;
- every ``422`` admits both bodies it can carry: ``ErrorResponse`` from a
  refusal, ``HTTPValidationError`` from FastAPI's own validation;
- ``404`` is declared on exactly the routes that send it;
- ``ErrorResponse`` publishes the structured refusal fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from manta_trading.api_server.admission import (
    RefusalReason,
    admit_rows,
    over_ceiling,
    range_reversed,
    trade_ceiling,
)

ARTIFACT_PATH = Path(__file__).resolve().parents[3] / "docs" / "api" / "openapi.json"

#: Every route that can answer 404: an unknown symbol, product, contract,
#: series, event or market named in its path.
NOT_FOUND_ROUTES = {
    "/api/v1/bars/{symbol}",
    "/api/v1/symbols/{symbol}",
    "/api/v1/kalshi/series/{ticker}",
    "/api/v1/kalshi/series/{ticker}/events",
    "/api/v1/kalshi/events/{event_ticker}",
    "/api/v1/kalshi/events/{event_ticker}/markets",
    "/api/v1/kalshi/markets/{ticker}",
    "/api/v1/kalshi/markets/{ticker}/candlesticks",
    "/api/v1/kalshi/markets/{ticker}/trades",
    "/api/v1/futures/products/{product}/contracts",
    "/api/v1/futures/products/{product}/schedule",
    "/api/v1/futures/products/{product}/ticks",
    "/api/v1/futures/products/{product}/bars",
    "/api/v1/futures/products/{product}/tick-bars",
    "/api/v1/futures/contracts/{symbol}/ticks",
    "/api/v1/futures/contracts/{symbol}/bars",
    "/api/v1/futures/contracts/{symbol}/tick-bars",
}


def _operations() -> list[tuple[str, dict[str, Any]]]:
    spec = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    return [
        (path, operation)
        for path, operations in spec["paths"].items()
        for operation in operations.values()
    ]


def _schema(response: dict[str, Any]) -> dict[str, Any] | None:
    return response.get("content", {}).get("application/json", {}).get("schema")


def test_every_route_publishes_a_200_schema() -> None:
    missing = [p for p, op in _operations() if _schema(op["responses"]["200"]) is None]
    assert missing == []


def test_every_422_admits_both_bodies() -> None:
    either = {
        "anyOf": [
            {"$ref": "#/components/schemas/ErrorResponse"},
            {"$ref": "#/components/schemas/HTTPValidationError"},
        ]
    }
    refused = [
        (p, op["responses"]["422"])
        for p, op in _operations()
        if "422" in op["responses"]
    ]
    assert refused
    assert [p for p, response in refused if _schema(response) != either] == []


def test_404_is_declared_on_exactly_the_routes_that_send_it() -> None:
    declared = {p for p, op in _operations() if "404" in op["responses"]}
    assert declared == NOT_FOUND_ROUTES


def test_error_response_publishes_the_structured_fields() -> None:
    spec = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    error = spec["components"]["schemas"]["ErrorResponse"]
    assert error["required"] == ["error"]
    assert set(error["properties"]) == {
        "error",
        "reason",
        "limit",
        "estimate",
        "granularity",
        "max_span_days",
    }
    reasons = spec["components"]["schemas"]["RefusalReason"]["enum"]
    assert reasons == [r.value for r in RefusalReason]


# -- the refusals themselves ------------------------------------------------------


def test_kalshi_row_ceiling_carries_limit_and_estimate() -> None:
    with pytest.raises(Exception) as raised:
        admit_rows(80_001, 75_000, remedy="narrow start/end")
    refusal = raised.value
    assert refusal.status_code == 422  # type: ignore[attr-defined]
    assert refusal.fields == {  # type: ignore[attr-defined]
        "reason": RefusalReason.ROW_CEILING,
        "limit": 75_000,
        "estimate": 80_001,
    }
    assert "80,001 rows, over the 75,000 row limit" in refusal.detail  # type: ignore[attr-defined]


def test_futures_row_ceiling_quotes_only_the_limit() -> None:
    refusal = over_ceiling(75_000, remedy="narrow start/end")
    assert refusal.fields == {"reason": RefusalReason.ROW_CEILING, "limit": 75_000}


def test_trade_ceiling_and_reversed_range_name_their_reason() -> None:
    trades = trade_ceiling("over", limit=2_000_000, estimate=2_500_000)
    assert trades.fields == {
        "reason": RefusalReason.TRADE_CEILING,
        "limit": 2_000_000,
        "estimate": 2_500_000,
    }
    assert range_reversed("reversed").fields == {"reason": RefusalReason.RANGE_REVERSED}
