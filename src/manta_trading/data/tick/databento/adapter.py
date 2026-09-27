"""``DatabentoTickProvider``: the tick protocols over the ``databento`` SDK.

Errors are mapped once, here (design Technical Decision 10). Free calls:
5xx, 429, connection and timeout → ``ProviderTransientError``; 401/403 →
``ProviderAuthError``; any other 4xx or a malformed response →
``ProviderPermanentError``. The key is passed to the SDK explicitly — never
its ``DATABENTO_API_KEY`` fallback — and is never logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from http import HTTPStatus
from types import TracebackType
from typing import Self, TypedDict, TypeVar

import databento
import httpx
import requests
from databento.common.error import BentoClientError, BentoError, BentoServerError

from manta_trading.config import Settings
from manta_trading.data.tick.constants import TICK_DOWNLOAD_TIMEOUT_SECONDS, SType
from manta_trading.data.tick.databento import _parse
from manta_trading.data.tick.provider import (
    DatasetRange,
    DayCondition,
    SymbolResolution,
    TickRequest,
)
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.profiles import get_profile
from manta_trading.providers.types import ProviderType

_T = TypeVar("_T")

#: Failures below HTTP: no answer was received.
NETWORK_ERRORS: tuple[type[Exception], ...] = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)
#: A parser's complaint about a response body (see ``_parse``).
MALFORMED_ERRORS: tuple[type[Exception], ...] = (KeyError, TypeError, ValueError)
#: A free call's refusal or unreadable answer that is not an HTTP status.
_FREE_PERMANENT_ERRORS: tuple[type[Exception], ...] = (BentoError, *MALFORMED_ERRORS)

_AUTH_STATUSES = frozenset({HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN})


def api_key_env() -> str:
    """The key's environment name, from the provider registry (not re-spelled)."""
    env = get_profile(ProviderType.DATABENTO.value).api_key_env
    if env is None:
        raise ProviderAuthError("databento profile names no API key variable")
    return env


def client_error(what: str, exc: BentoClientError) -> ProviderError:
    """A 4xx answer: the provider refused, so nothing was charged."""
    detail = f"Databento {what}: HTTP {exc.http_status}: {exc.message}"
    if exc.http_status == HTTPStatus.TOO_MANY_REQUESTS:
        return ProviderTransientError(detail)
    if exc.http_status in _AUTH_STATUSES:
        return ProviderAuthError(detail)
    return ProviderPermanentError(detail)


@contextmanager
def free_call(what: str) -> Iterator[None]:
    """Map every failure of a free call (and of parsing its answer)."""
    try:
        yield
    except BentoServerError as exc:
        raise ProviderTransientError(
            f"Databento {what}: HTTP {exc.http_status}: {exc.message}"
        ) from exc
    except BentoClientError as exc:
        raise client_error(what, exc) from exc
    except NETWORK_ERRORS as exc:
        raise ProviderTransientError(f"Databento {what}: {exc!r}") from exc
    except _FREE_PERMANENT_ERRORS as exc:
        raise ProviderPermanentError(
            f"Databento {what}: unusable response: {exc!r}"
        ) from exc


class DatabentoTickProvider:
    """``ITickMetadataProvider`` (and, from Section 4, acquisition) over the SDK.

    Not thread-safe: one instance per concurrent caller. A context manager
    that owns ``download_http``: ``__exit__`` closes it, however it was built.
    """

    def __init__(self, client: databento.Historical, download_http: httpx.Client):
        self._client = client
        self._http = download_http

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        key = settings.databento_api_key
        if key is None or not key.strip():
            raise ProviderAuthError(f"{api_key_env()} is not set")
        http = httpx.Client(
            auth=(key, ""),
            timeout=TICK_DOWNLOAD_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        return cls(databento.Historical(key=key), http)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._http.close()

    # -- free metadata ------------------------------------------------------

    def _free(self, what: str, call: Callable[[], _T]) -> _T:
        with free_call(what):
            return call()

    def dataset_range(self, dataset: str) -> DatasetRange:
        return self._free(
            "get_dataset_range",
            lambda: _parse.parse_dataset_range(
                self._client.metadata.get_dataset_range(dataset=dataset)
            ),
        )

    def dataset_condition(
        self, dataset: str, start: date, end: date
    ) -> tuple[DayCondition, ...]:
        # The one inclusive-end endpoint (design TD 8): [start, end) → end - 1.
        inclusive_end = end - timedelta(days=1)
        return self._free(
            "get_dataset_condition",
            lambda: _parse.parse_conditions(
                self._client.metadata.get_dataset_condition(
                    dataset=dataset, start_date=start, end_date=inclusive_end
                ),
                start,
                end,
            ),
        )

    def record_count(self, request: TickRequest) -> int:
        return self._free(
            "get_record_count",
            lambda: _parse.as_count(
                self._client.metadata.get_record_count(**_query(request)),
                "record count",
            ),
        )

    def billable_size(self, request: TickRequest) -> int:
        return self._free(
            "get_billable_size",
            lambda: _parse.as_count(
                self._client.metadata.get_billable_size(**_query(request)),
                "billable size",
            ),
        )

    def cost(self, request: TickRequest) -> Decimal:
        # ``mode`` is deprecated in the SDK and is never passed.
        return self._free(
            "get_cost",
            lambda: _parse.as_usd(self._client.metadata.get_cost(**_query(request))),
        )

    def resolve_symbols(self, request: TickRequest) -> SymbolResolution:
        return self._free(
            "symbology.resolve",
            lambda: _parse.parse_resolution(
                self._client.symbology.resolve(
                    dataset=request.dataset,
                    symbols=list(request.symbols),
                    stype_in=request.stype_in.value,
                    stype_out=SType.INSTRUMENT_ID.value,
                    start_date=request.start,
                    end_date=request.end,
                )
            ),
        )


class _Query(TypedDict):
    """The keywords the SDK's metadata and submit calls share."""

    dataset: str
    symbols: list[str]
    schema: str
    stype_in: str
    start: date
    end: date


def _query(request: TickRequest) -> _Query:
    """The request's parameters as the SDK names them."""
    return {
        "dataset": request.dataset,
        "symbols": list(request.symbols),
        "schema": request.schema.value,
        "stype_in": request.stype_in.value,
        "start": request.start,
        "end": request.end,
    }
