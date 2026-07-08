"""Unit tests for ``manta_trading.api.eodhd_sync`` (slice 146 T16/T16a/T16b).

Covers:
  - 429 with Retry-After (integer seconds) → sleep, retry, succeed.
  - 429 without Retry-After → sleep default 60s, retry, succeed.
  - MAX_RETRY_COUNT consecutive 429s → QuotaBucketMisconfiguredError.
  - httpx.RemoteProtocolError mid-response → classified transient,
    retried per slice 145 policy, no partial JSON persisted.
  - httpx.ReadError (truncated body) → same classification.
  - Missing bucket on contextvar → QuotaBucketUnsetError.
  - Bucket consume runs once per HTTP attempt (so retry costs are
    properly accounted on the upstream side).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from manta_trading.api.eodhd_sync import (
    QuotaBucketMisconfiguredError,
    QuotaBucketUnsetError,
    eodhd_get,
)
from manta_trading.constants import MAX_RETRY_COUNT
from manta_trading.data.acquisition.quota import CallType, QuotaBucket


def _bucket() -> QuotaBucket:
    return QuotaBucket(now=lambda: 0.0, sleep=lambda _s: None)


def _resp(status: int, headers: dict[str, str] | None = None) -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status
    r.headers = headers or {}
    return r


def test_unset_bucket_raises():
    # The contextvar default is None; with no override either, must raise.
    client = MagicMock()
    with pytest.raises(QuotaBucketUnsetError):
        eodhd_get(client, "https://eodhd.com/api/eod/AAPL", CallType.EOD)


def test_429_with_integer_retry_after_retries_and_succeeds():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    client.get.side_effect = [
        _resp(429, {"Retry-After": "1"}),
        _resp(200),
    ]

    resp = eodhd_get(
        client, "u", CallType.EOD,
        bucket=bucket, sleep=sleeps.append,
    )
    assert resp.status_code == 200
    # First sleep is the Retry-After value.
    assert sleeps and sleeps[0] == pytest.approx(1.0)
    assert client.get.call_count == 2


def test_429_without_retry_after_uses_default_60s():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    client.get.side_effect = [_resp(429), _resp(200)]

    resp = eodhd_get(
        client, "u", CallType.EOD,
        bucket=bucket, sleep=sleeps.append,
    )
    assert resp.status_code == 200
    assert sleeps and sleeps[0] == pytest.approx(60.0)


def test_429_escalation_after_max_retries_raises():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    # MAX_RETRY_COUNT + 1 consecutive 429s → escalation
    client.get.side_effect = [
        _resp(429, {"Retry-After": "0"})
        for _ in range(MAX_RETRY_COUNT + 1)
    ]

    with pytest.raises(QuotaBucketMisconfiguredError):
        eodhd_get(
            client, "u", CallType.EOD,
            bucket=bucket, sleep=sleeps.append,
        )


def test_remote_protocol_error_retries_then_succeeds():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    client.get.side_effect = [
        httpx.RemoteProtocolError("peer disconnect"),
        _resp(200),
    ]

    resp = eodhd_get(
        client, "u", CallType.EOD,
        bucket=bucket, sleep=sleeps.append,
    )
    assert resp.status_code == 200
    # We waited per backoff before retry.
    assert sleeps and sleeps[0] >= 1.0


def test_read_error_classified_as_transient_and_retried():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    client.get.side_effect = [
        httpx.ReadError("truncated"),
        _resp(200),
    ]

    resp = eodhd_get(
        client, "u", CallType.EOD,
        bucket=bucket, sleep=sleeps.append,
    )
    assert resp.status_code == 200


def test_remote_protocol_error_exhausts_retries_and_raises():
    sleeps: list[float] = []
    bucket = _bucket()
    client = MagicMock()
    client.get.side_effect = [
        httpx.RemoteProtocolError("peer disconnect")
        for _ in range(MAX_RETRY_COUNT + 1)
    ]
    with pytest.raises(httpx.RemoteProtocolError):
        eodhd_get(
            client, "u", CallType.EOD,
            bucket=bucket, sleep=sleeps.append,
        )


def test_bucket_consume_called_once_per_http_attempt():
    sleeps: list[float] = []
    bucket = MagicMock()
    bucket.consume = MagicMock()
    client = MagicMock()
    client.get.side_effect = [
        _resp(429, {"Retry-After": "0"}),
        _resp(200),
    ]
    eodhd_get(
        client, "u", CallType.EOD,
        bucket=bucket, sleep=sleeps.append,
    )
    # consume called twice — once per attempt, retries are NOT free.
    assert bucket.consume.call_count == 2


def test_partial_response_body_not_pre_parsed():
    """eodhd_get must NOT call resp.json() — the cycle's classify_outcome
    handles parsing at the call site, where context is available to
    decide if a parse failure is empty/transient/etc.
    """
    bucket = _bucket()
    client = MagicMock()
    resp = _resp(200)
    client.get.return_value = resp

    eodhd_get(client, "u", CallType.EOD, bucket=bucket)
    # json() should NOT have been called inside the wrapper.
    resp.json.assert_not_called()
