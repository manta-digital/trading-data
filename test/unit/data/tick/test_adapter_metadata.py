"""Databento metadata adapter: parameters, parsing, error mapping (slice 220).

Runs on a fake ``Historical``; no network, no key.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal

import httpx
import pytest
from tick_support.fake_historical import (
    FakeApi,
    FakeHistorical,
    client_error,
    connection_error,
    read_timeout,
    server_error,
)
from tick_support.metadata_responses import (
    FIGURES,
    REQUEST_END,
    REQUEST_START,
    metadata_api,
    symbology_api,
)

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    CME_DATASET,
    DatasetCondition,
    SType,
    TickSchema,
)
from manta_trading.data.tick.databento.adapter import (
    DatabentoTickProvider,
    api_key_env,
)
from manta_trading.data.tick.provider import SymbolInterval, TickRequest
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)

REQUEST = TickRequest(
    dataset=CME_DATASET,
    symbols=("ES.c.0",),
    stype_in=SType.CONTINUOUS,
    schema=TickSchema.TBBO,
    start=REQUEST_START,
    end=REQUEST_END,
)


def _http() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(599)))


def _provider(
    metadata: FakeApi | None = None, symbology: FakeApi | None = None
) -> tuple[DatabentoTickProvider, FakeApi, FakeApi]:
    metadata = metadata or metadata_api()
    symbology = symbology or symbology_api()
    client = FakeHistorical(metadata=metadata, symbology=symbology)
    return DatabentoTickProvider(client, _http()), metadata, symbology  # type: ignore[arg-type]


# -- parameters and parsing ----------------------------------------------------


def test_dataset_condition_sends_inclusive_end_and_returns_one_per_day() -> None:
    provider, metadata, _ = _provider()
    conditions = provider.dataset_condition(CME_DATASET, REQUEST_START, REQUEST_END)
    [sent] = metadata.calls_to("get_dataset_condition")
    assert sent["start_date"] == date(2025, 1, 6)
    assert sent["end_date"] == date(2025, 1, 10)
    assert [c.day for c in conditions] == [date(2025, 1, d) for d in range(6, 11)]
    assert {c.condition for c in conditions} == {DatasetCondition.AVAILABLE}


def test_dataset_condition_short_answer_is_permanent() -> None:
    metadata = metadata_api()
    metadata.responses["get_dataset_condition"] = [
        {"date": "2025-01-06", "condition": "available", "last_modified_date": None}
    ]
    provider, _, _ = _provider(metadata)
    with pytest.raises(ProviderPermanentError, match="expected one per day"):
        provider.dataset_condition(CME_DATASET, REQUEST_START, REQUEST_END)


def test_dataset_range_parses_nanosecond_timestamps() -> None:
    provider, _, _ = _provider()
    available = provider.dataset_range(CME_DATASET)
    assert available.end == datetime(2025, 9, 26, tzinfo=UTC)
    assert available.start == datetime(2010, 6, 6, tzinfo=UTC)


def test_request_parameters_are_the_requests_own() -> None:
    provider, metadata, _ = _provider()
    assert provider.record_count(REQUEST) == FIGURES[TickSchema.TBBO][0]
    assert provider.billable_size(REQUEST) == FIGURES[TickSchema.TBBO][1]
    provider.cost(REQUEST)
    for method in ("get_record_count", "get_billable_size", "get_cost"):
        [sent] = metadata.calls_to(method)
        assert sent == {
            "dataset": CME_DATASET,
            "symbols": ["ES.c.0"],
            "schema": "tbbo",
            "stype_in": "continuous",
            "start": REQUEST_START,
            "end": REQUEST_END,
        }


def test_cost_is_decimal_and_mode_is_never_sent() -> None:
    provider, metadata, _ = _provider()
    cost = provider.cost(REQUEST)
    assert cost == Decimal("2.5")
    assert isinstance(cost, Decimal)
    assert "mode" not in metadata.calls_to("get_cost")[0]


def test_resolve_symbols_sends_instrument_id_stype_out() -> None:
    provider, _, symbology = _provider()
    resolution = provider.resolve_symbols(REQUEST)
    [sent] = symbology.calls_to("resolve")
    assert sent["stype_out"] == "instrument_id"
    assert sent["stype_in"] == "continuous"
    assert (sent["start_date"], sent["end_date"]) == (REQUEST_START, REQUEST_END)
    assert resolution.mappings == {
        "ES.c.0": (SymbolInterval(date(2025, 1, 6), date(2025, 1, 11), 5002),)
    }
    assert resolution.partial == ()
    assert resolution.not_found == ()


# -- free-call error mapping ---------------------------------------------------

FREE_CALLS: list[tuple[str, Callable[[DatabentoTickProvider], object]]] = [
    ("get_dataset_range", lambda p: p.dataset_range(CME_DATASET)),
    (
        "get_dataset_condition",
        lambda p: p.dataset_condition(CME_DATASET, REQUEST_START, REQUEST_END),
    ),
    ("get_record_count", lambda p: p.record_count(REQUEST)),
    ("get_billable_size", lambda p: p.billable_size(REQUEST)),
    ("get_cost", lambda p: p.cost(REQUEST)),
]

FAILURES: list[tuple[str, Callable[[], BaseException], type[ProviderError]]] = [
    ("5xx", lambda: server_error(502), ProviderTransientError),
    ("504", lambda: server_error(504), ProviderTransientError),
    ("429", lambda: client_error(429), ProviderTransientError),
    ("timeout", read_timeout, ProviderTransientError),
    ("connection", connection_error, ProviderTransientError),
    ("401", lambda: client_error(401), ProviderAuthError),
    ("403", lambda: client_error(403), ProviderAuthError),
    ("400", lambda: client_error(400), ProviderPermanentError),
]


@pytest.mark.parametrize(("method", "invoke"), FREE_CALLS, ids=lambda x: x)
@pytest.mark.parametrize(("label", "make", "expected"), FAILURES)
def test_free_call_error_mapping(
    method: str,
    invoke: Callable[[DatabentoTickProvider], object],
    label: str,
    make: Callable[[], BaseException],
    expected: type[ProviderError],
) -> None:
    metadata = metadata_api()
    metadata.fail(method, make())
    provider, _, _ = _provider(metadata)
    with pytest.raises(expected) as caught:
        invoke(provider)
    assert type(caught.value) is expected


@pytest.mark.parametrize(("label", "make", "expected"), FAILURES)
def test_resolve_error_mapping(
    label: str, make: Callable[[], BaseException], expected: type[ProviderError]
) -> None:
    symbology = symbology_api()
    symbology.fail("resolve", make())
    provider, _, _ = _provider(symbology=symbology)
    with pytest.raises(expected):
        provider.resolve_symbols(REQUEST)


MALFORMED: list[tuple[str, object]] = [
    ("get_dataset_range", {"start_date": "2010-06-06"}),
    ("get_dataset_condition", {"not": "a list"}),
    ("get_record_count", "many"),
    ("get_billable_size", -1),
    ("get_cost", None),
]


@pytest.mark.parametrize(("method", "body"), MALFORMED)
def test_malformed_response_is_permanent(method: str, body: object) -> None:
    """A shape the adapter cannot parse, with no ``BentoError`` raised."""
    metadata = metadata_api()
    metadata.responses[method] = body
    provider, _, _ = _provider(metadata)
    invoke = dict(FREE_CALLS)[method]
    with pytest.raises(ProviderPermanentError, match="unusable response"):
        invoke(provider)


def test_unknown_condition_is_permanent() -> None:
    metadata = metadata_api()
    metadata.responses["get_dataset_condition"] = lambda kwargs: [
        {"date": "2025-01-06", "condition": "bad", "last_modified_date": None}
    ]
    provider, _, _ = _provider(metadata)
    with pytest.raises(ProviderPermanentError, match="unknown dataset condition"):
        provider.dataset_condition(CME_DATASET, REQUEST_START, date(2025, 1, 7))


def test_malformed_resolve_is_permanent() -> None:
    symbology = symbology_api()
    symbology.responses["resolve"] = {"result": {"ES.c.0": [{"d0": "2025-01-06"}]}}
    provider, _, _ = _provider(symbology=symbology)
    with pytest.raises(ProviderPermanentError):
        provider.resolve_symbols(REQUEST)


# -- construction and lifetime -------------------------------------------------


def test_from_settings_without_key_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MT_DATABENTO_API_KEY", raising=False)
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with pytest.raises(ProviderAuthError, match="MT_DATABENTO_API_KEY"):
        DatabentoTickProvider.from_settings(Settings(_env_file=None))


def test_key_env_name_comes_from_the_registry() -> None:
    assert api_key_env() == "MT_DATABENTO_API_KEY"


def test_from_settings_ignores_the_sdk_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``DATABENTO_API_KEY`` alone must not satisfy the adapter."""
    monkeypatch.delenv("MT_DATABENTO_API_KEY", raising=False)
    monkeypatch.setenv("DATABENTO_API_KEY", "db-sdk-fallback")
    with pytest.raises(ProviderAuthError):
        DatabentoTickProvider.from_settings(Settings(_env_file=None))


def test_from_settings_passes_the_key_explicitly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MT_DATABENTO_API_KEY", "db-test-key")
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    with DatabentoTickProvider.from_settings(Settings(_env_file=None)) as provider:
        assert provider._client._key == "db-test-key"


def test_context_manager_closes_http_on_normal_exit() -> None:
    http = _http()
    with DatabentoTickProvider(FakeHistorical(), http):  # type: ignore[arg-type]
        assert not http.is_closed
    assert http.is_closed


def test_context_manager_closes_http_on_exception() -> None:
    http = _http()
    with pytest.raises(RuntimeError, match="boom"):
        with DatabentoTickProvider(FakeHistorical(), http):  # type: ignore[arg-type]
            raise RuntimeError("boom")
    assert http.is_closed
