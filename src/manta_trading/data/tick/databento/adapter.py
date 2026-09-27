"""``DatabentoTickProvider``: the tick protocols over the ``databento`` SDK.

Errors are mapped once, here (design Technical Decision 10). Free calls:
5xx, 429, connection and timeout → ``ProviderTransientError``; 401/403 →
``ProviderAuthError``; any other 4xx or a malformed response →
``ProviderPermanentError``. Paid calls (``submit_batch``, ``fetch_range``):
a 4xx is mapped as above (the provider refused; nothing was charged) and
**anything else** → ``ProviderOutcomeUnknownError``. The key is passed to the
SDK explicitly — never its ``DATABENTO_API_KEY`` fallback — and is never
logged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from http import HTTPStatus
from pathlib import Path
from types import TracebackType
from typing import Self

import databento
import httpx
import requests
from databento.common.error import BentoClientError, BentoError, BentoServerError

from manta_trading.config import Settings
from manta_trading.data.tick.constants import (
    TICK_DOWNLOAD_TIMEOUT_SECONDS,
    BatchJobState,
    SType,
)
from manta_trading.data.tick.databento import _download, _parse
from manta_trading.data.tick.provider import (
    BatchJob,
    DatasetRange,
    DayCondition,
    SymbolResolution,
    TickRequest,
)
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderOutcomeUnknownError,
    ProviderPermanentError,
    ProviderTransientError,
)
from manta_trading.providers.profiles import get_profile
from manta_trading.providers.types import ProviderType

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

#: A paid call's failures that do not prove the provider refused (TD 10).
_PAID_UNKNOWN_ERRORS: tuple[type[Exception], ...] = (
    BentoError,
    requests.RequestException,
    *MALFORMED_ERRORS,
)
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


@contextmanager
def paid_call(what: str) -> Iterator[None]:
    """Map a billable call's failures: only a 4xx proves nothing was charged."""
    try:
        yield
    except BentoClientError as exc:
        raise client_error(what, exc) from exc
    except _PAID_UNKNOWN_ERRORS as exc:
        raise ProviderOutcomeUnknownError(
            f"Databento {what}: outcome unknown, may have been charged; "
            f"reconcile before resubmitting: {exc!r}"
        ) from exc


class DatabentoTickProvider:
    """``ITickMetadataProvider`` and ``ITickAcquisitionProvider`` over the SDK.

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

    def _free[T](self, what: str, call: Callable[[], T]) -> T:
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
                self._client.metadata.get_record_count(**_parse.query(request)),
                "record count",
            ),
        )

    def billable_size(self, request: TickRequest) -> int:
        return self._free(
            "get_billable_size",
            lambda: _parse.as_count(
                self._client.metadata.get_billable_size(**_parse.query(request)),
                "billable size",
            ),
        )

    def cost(self, request: TickRequest) -> Decimal:
        # ``mode`` is deprecated in the SDK and is never passed.
        return self._free(
            "get_cost",
            lambda: _parse.as_usd(
                self._client.metadata.get_cost(**_parse.query(request))
            ),
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

    # -- acquisition ----------------------------------------------------------

    def submit_batch(self, request: TickRequest) -> BatchJob:
        """PAID. Submit, then read the job back through ``get_job_details``."""
        with paid_call("batch.submit_job"):
            job_id = _parse.parse_job_id(
                self._client.batch.submit_job(
                    **_parse.query(request),
                    encoding="dbn",
                    compression="zstd",
                    split_duration="day",
                    delivery="download",
                    stype_out=SType.INSTRUMENT_ID.value,
                )
            )
        try:
            return self.batch_job(job_id)
        except ProviderError as exc:
            # Submitted (and charged); only the read-back failed.
            raise ProviderOutcomeUnknownError(
                f"batch job {job_id} was submitted but reading it back failed: {exc}"
            ) from exc

    def fetch_range(self, request: TickRequest, dest: Path) -> Path:
        """PAID. Stream to ``<dest>.partial``; rename to ``dest`` on completion."""
        if dest.exists():
            raise FileExistsError(f"{dest} already exists; a finished unit is final")
        partial = _download.partial_path(dest)
        partial.unlink(missing_ok=True)  # crash residue: a stream cannot resume
        try:
            with paid_call("timeseries.get_range"):
                self._client.timeseries.get_range(
                    **_parse.query(request),
                    stype_out=SType.INSTRUMENT_ID.value,
                    path=partial,
                )
        except ProviderError:
            partial.unlink(missing_ok=True)
            raise
        partial.rename(dest)
        return dest

    def batch_job(self, job_id: str) -> BatchJob:
        return self._free(
            "batch.get_job_details",
            lambda: _parse.parse_batch_job(
                self._client.batch.get_job_details(job_id=job_id)
            ),
        )

    def batch_jobs_since(self, since: datetime) -> tuple[BatchJob, ...]:
        """``list_jobs`` for ids only (every state), then details per id."""
        job_ids = self._free(
            "batch.list_jobs",
            lambda: _parse.parse_job_ids(
                self._client.batch.list_jobs(
                    states=[state.value for state in BatchJobState],
                    since=since,
                    short=True,
                )
            ),
        )
        return tuple(self.batch_job(job_id) for job_id in job_ids)

    def download_batch(self, job_id: str, dest_dir: Path) -> tuple[Path, ...]:
        """Verified download of every file of the job (never ``batch.download``)."""
        files = self._free(
            "batch.list_files",
            lambda: _parse.parse_batch_files(
                self._client.batch.list_files(job_id=job_id)
            ),
        )
        return tuple(_download.download_file(self._http, f, dest_dir) for f in files)
