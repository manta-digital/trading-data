"""Databento paid calls and job reads: parameters and outcomes (slice 220, TD 9–10).

A paid call's timeout, disconnect, 5xx, or mid-stream error is
``ProviderOutcomeUnknownError`` — never ``ProviderTransientError`` — because
the provider may already have charged. Only a 4xx proves it did not.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from databento.common.error import BentoError
from tick_support.batch_responses import JOB_ID, batch_api, job_record
from tick_support.fake_historical import (
    FakeApi,
    FakeHistorical,
    client_error,
    connection_error,
    read_timeout,
    server_error,
)

from manta_trading.data.tick.constants import (
    CME_DATASET,
    BatchJobState,
    SType,
    TickSchema,
)
from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.data.tick.provider import TickRequest
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderOutcomeUnknownError,
    ProviderPermanentError,
    ProviderTransientError,
)

REQUEST = TickRequest(
    dataset=CME_DATASET,
    symbols=("ES.c.0",),
    stype_in=SType.CONTINUOUS,
    schema=TickSchema.TRADES,
    start=date(2025, 1, 6),
    end=date(2025, 1, 11),
)
MIDNIGHT_START = datetime(2025, 1, 6, tzinfo=UTC)
MIDNIGHT_END = datetime(2025, 1, 11, tzinfo=UTC)


def _provider(
    batch: FakeApi | None = None, timeseries: FakeApi | None = None
) -> DatabentoTickProvider:
    client = FakeHistorical(batch=batch, timeseries=timeseries)
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(599)))
    return DatabentoTickProvider(client, http)  # type: ignore[arg-type]


#: The paid-call outcome table (design TD 10's failure-modes table).
PAID_FAILURES: list[tuple[str, Callable[[], BaseException], type[ProviderError]]] = [
    ("timeout", read_timeout, ProviderOutcomeUnknownError),
    ("connection", connection_error, ProviderOutcomeUnknownError),
    ("502", lambda: server_error(502), ProviderOutcomeUnknownError),
    ("504", lambda: server_error(504), ProviderOutcomeUnknownError),
    (
        "mid-stream",
        lambda: BentoError("Error streaming response"),
        ProviderOutcomeUnknownError,
    ),
    ("429", lambda: client_error(429), ProviderTransientError),
    ("400", lambda: client_error(400), ProviderPermanentError),
    ("422", lambda: client_error(422), ProviderPermanentError),
    ("401", lambda: client_error(401), ProviderAuthError),
    ("403", lambda: client_error(403), ProviderAuthError),
]


# -- submit_batch, batch_job, batch_jobs_since ------------------------------------


def test_submit_batch_sends_exact_parameters() -> None:
    batch = batch_api()
    job = _provider(batch).submit_batch(REQUEST)
    [sent] = batch.calls_to("submit_job")
    assert sent == {
        "dataset": CME_DATASET,
        "symbols": ["ES.c.0"],
        "schema": "trades",
        "stype_in": "continuous",
        "start": MIDNIGHT_START,
        "end": MIDNIGHT_END,
        "encoding": "dbn",
        "compression": "zstd",
        "split_duration": "day",
        "delivery": "download",
        "stype_out": "instrument_id",
    }
    assert batch.calls_to("get_job_details") == [{"job_id": JOB_ID}]
    assert job.job_id == JOB_ID
    assert job.state is BatchJobState.DONE  # read back, not the submit answer


def test_batch_job_parses_the_manifest_fields() -> None:
    job = _provider(batch_api()).batch_job(JOB_ID)
    assert job.request == REQUEST
    assert job.ts_expiration == datetime(2025, 2, 11, 0, 25, 10, tzinfo=UTC)
    assert job.ts_received == datetime(2025, 1, 12, 0, 24, 3, 786913, tzinfo=UTC)
    assert job.cost_usd == Decimal("1.25")
    assert (job.record_count, job.billed_size) == (2_000_000, 96_000_000)
    assert (job.actual_size, job.package_size) == (96_000_000, 30_000_000)


def test_unprocessed_job_has_no_sizes() -> None:
    record = job_record(state="queued")
    for field in ("cost_usd", "record_count", "billed_size", "actual_size"):
        record[field] = None
    record["package_size"] = record["ts_expiration"] = None
    job = _provider(batch_api({JOB_ID: record})).batch_job(JOB_ID)
    assert job.state is BatchJobState.QUEUED
    assert job.cost_usd is None and job.ts_expiration is None


def test_job_off_a_day_boundary_is_refused() -> None:
    record = job_record(start="2025-01-06 12:00:00+00:00")
    with pytest.raises(ProviderPermanentError, match="day boundary"):
        _provider(batch_api({JOB_ID: record})).batch_job(JOB_ID)


def test_batch_jobs_since_lists_ids_then_reads_each() -> None:
    other = "GLBX-20250112-KLMNOPQRST"
    jobs = {JOB_ID: job_record(), other: job_record(other, schema="tbbo")}
    batch = batch_api(jobs)
    since = datetime(2025, 1, 12, tzinfo=UTC)
    found = _provider(batch).batch_jobs_since(since)
    [listed] = batch.calls_to("list_jobs")
    assert listed["since"] == since
    assert listed["short"] is True
    assert set(listed["states"]) == {s.value for s in BatchJobState}
    assert [j.job_id for j in found] == [JOB_ID, other]
    assert [j.request.schema for j in found] == [TickSchema.TRADES, TickSchema.TBBO]
    assert batch.calls_to("get_job_details") == [{"job_id": JOB_ID}, {"job_id": other}]


@pytest.mark.parametrize(("label", "make", "expected"), PAID_FAILURES)
def test_submit_batch_outcomes(
    label: str, make: Callable[[], BaseException], expected: type[ProviderError]
) -> None:
    batch = batch_api()
    batch.fail("submit_job", make())
    with pytest.raises(ProviderError) as caught:
        _provider(batch).submit_batch(REQUEST)
    assert type(caught.value) is expected
    assert not isinstance(caught.value, ProviderTransientError) or label == "429"


def test_submit_batch_read_back_failure_is_outcome_unknown() -> None:
    """Submitted and charged; a failed read-back must not look retryable."""
    batch = batch_api()
    batch.fail("get_job_details", server_error(503))
    with pytest.raises(ProviderOutcomeUnknownError, match=JOB_ID):
        _provider(batch).submit_batch(REQUEST)


def test_batch_job_read_is_a_free_call() -> None:
    batch = batch_api()
    batch.fail("get_job_details", server_error(503))
    with pytest.raises(ProviderTransientError):
        _provider(batch).batch_job(JOB_ID)


# -- fetch_range ---------------------------------------------------------------

PAYLOAD = b"DBN\x03" + b"\x00" * 60


def _streams(payload: bytes = PAYLOAD) -> Callable[[dict[str, Any]], object]:
    def get_range(kwargs: dict[str, Any]) -> object:
        with Path(kwargs["path"]).open("xb") as out:  # the SDK opens with x+b
            out.write(payload)
        return None

    return get_range


def _fails_mid_stream(exc: BaseException) -> Callable[[dict[str, Any]], object]:
    def get_range(kwargs: dict[str, Any]) -> object:
        Path(kwargs["path"]).write_bytes(PAYLOAD[:10])
        raise exc

    return get_range


def test_fetch_range_success_leaves_only_dest(tmp_path: Path) -> None:
    timeseries = FakeApi({"get_range": _streams()})
    dest = tmp_path / "unit.dbn.zst"
    assert _provider(timeseries=timeseries).fetch_range(REQUEST, dest) == dest
    assert dest.read_bytes() == PAYLOAD
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unit.dbn.zst"]
    [sent] = timeseries.calls_to("get_range")
    assert sent["path"] == tmp_path / "unit.dbn.zst.partial"
    assert sent["stype_out"] == "instrument_id"
    assert (sent["start"], sent["end"]) == (MIDNIGHT_START, MIDNIGHT_END)


@pytest.mark.parametrize(("label", "make", "expected"), PAID_FAILURES)
def test_fetch_range_outcomes_leave_no_file(
    tmp_path: Path,
    label: str,
    make: Callable[[], BaseException],
    expected: type[ProviderError],
) -> None:
    timeseries = FakeApi({"get_range": _fails_mid_stream(make())})
    with pytest.raises(ProviderError) as caught:
        _provider(timeseries=timeseries).fetch_range(REQUEST, tmp_path / "u.dbn.zst")
    assert type(caught.value) is expected
    assert list(tmp_path.iterdir()) == []


def test_fetch_range_deletes_leftover_partial_first(tmp_path: Path) -> None:
    dest = tmp_path / "unit.dbn.zst"
    (tmp_path / "unit.dbn.zst.partial").write_bytes(b"crash residue")
    _provider(timeseries=FakeApi({"get_range": _streams()})).fetch_range(REQUEST, dest)
    assert dest.read_bytes() == PAYLOAD
    assert sorted(p.name for p in tmp_path.iterdir()) == ["unit.dbn.zst"]


def test_fetch_range_refuses_existing_dest_without_a_call(tmp_path: Path) -> None:
    dest = tmp_path / "unit.dbn.zst"
    dest.write_bytes(b"finished")
    timeseries = FakeApi({"get_range": _streams()})
    with pytest.raises(FileExistsError):
        _provider(timeseries=timeseries).fetch_range(REQUEST, dest)
    assert timeseries.calls == []
    assert dest.read_bytes() == b"finished"


def test_recorded_job_parses() -> None:
    """A real job record (read free from the account; ids redacted)."""
    fixture = Path(__file__).resolve().parents[3] / "fixtures" / "databento" / "batch"
    body = json.loads((fixture / "get_job_details.json").read_text())["response"]
    job = _provider(batch_api({body["id"]: body})).batch_job(body["id"])
    assert job.state is BatchJobState.EXPIRED
    assert job.request == TickRequest(
        dataset=CME_DATASET,
        symbols=("ES.FUT",),
        stype_in=SType.PARENT,
        schema=TickSchema.TBBO,
        start=date(2024, 11, 1),
        end=date(2025, 1, 1),
    )
    assert job.ts_expiration == datetime(2025, 2, 22, 5, 18, 12, 939863, tzinfo=UTC)
    assert job.cost_usd == Decimal("36.80458068847656")
    assert (job.record_count, job.billed_size) == (17_642_240, 1_411_379_200)
