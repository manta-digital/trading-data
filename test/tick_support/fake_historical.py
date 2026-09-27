"""A ``databento.Historical``-shaped fake for the tick adapter tests (slice 220).

Each API namespace records its calls as ``(method, kwargs)`` and answers from a
response table; ``fail(method, exc)`` makes one method raise. A namespace the
test did not supply raises on access, which is how the preflight's
"no billable surface touched" backstop is asserted.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import requests
from databento.common.error import BentoClientError, BentoServerError

Responder = Callable[[dict[str, Any]], object]


class FakeApi:
    """One SDK namespace (``metadata``, ``symbology``, ``batch``, ``timeseries``)."""

    def __init__(self, responses: dict[str, object | Responder] | None = None):
        self.responses: dict[str, object | Responder] = dict(responses or {})
        self.errors: dict[str, BaseException] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fail(self, method: str, exc: BaseException) -> None:
        self.errors[method] = exc

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        return [kwargs for name, kwargs in self.calls if name == method]

    def __getattr__(self, method: str) -> Callable[..., object]:
        if method.startswith("_"):
            raise AttributeError(method)

        def call(**kwargs: Any) -> object:
            self.calls.append((method, kwargs))
            if method in self.errors:
                raise self.errors[method]
            answer = self.responses[method]
            return answer(kwargs) if callable(answer) else answer

        return call


class FakeHistorical:
    """Stands in for ``databento.Historical``; unsupplied namespaces raise."""

    def __init__(
        self,
        metadata: FakeApi | None = None,
        symbology: FakeApi | None = None,
        batch: FakeApi | None = None,
        timeseries: FakeApi | None = None,
    ):
        self._apis = {
            "metadata": metadata,
            "symbology": symbology,
            "batch": batch,
            "timeseries": timeseries,
        }

    def __getattr__(self, name: str) -> FakeApi:
        apis = self.__dict__.get("_apis", {})
        if name not in apis:
            raise AttributeError(name)
        api = apis[name]
        if api is None:
            raise AssertionError(f"Historical.{name} accessed but not supplied")
        return api


# -- SDK failures, built as the SDK raises them ------------------------------


def server_error(status: int = 502) -> BentoServerError:
    return BentoServerError(http_status=status, message="server error")


def client_error(status: int) -> BentoClientError:
    return BentoClientError(http_status=status, message="client error")


def read_timeout() -> requests.ReadTimeout:
    return requests.ReadTimeout("read timed out")


def connection_error() -> requests.ConnectionError:
    return requests.ConnectionError("connection reset")
