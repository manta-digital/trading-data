"""Unit tests for :mod:`manta_trading.api_server.serialization`.

The helper's contract is that ``mode="json"`` normalizes the payload before
either encoder runs, so the json and msgpack branches carry *the same*
scalars — a Decimal as its exact string, an aware datetime as ISO-8601 with a
UTC offset, and None as null (design 188 D7).
"""

from __future__ import annotations

import asyncio
import gzip
from datetime import UTC, datetime
from decimal import Decimal

import msgpack
import orjson
import pytest
from pydantic import BaseModel
from starlette.requests import Request

from manta_trading.api_server.serialization import (
    JSON_MEDIA_TYPE,
    MSGPACK_MEDIA_TYPE,
    ResponseFormat,
    send_timeseries,
    timeseries_response,
)


class _Sample(BaseModel):
    """A model carrying each type whose rendering the helper is responsible for."""

    price: Decimal
    observed_at: datetime
    settled_at: datetime | None


_MODEL = _Sample(
    price=Decimal("0.4900"),
    observed_at=datetime(2026, 1, 2, 15, 30, tzinfo=UTC),
    settled_at=None,
)


def _json_payload() -> dict:
    response = timeseries_response(_MODEL, "json")
    assert response.media_type == JSON_MEDIA_TYPE
    return orjson.loads(response.body)


def _msgpack_payload() -> dict:
    response = timeseries_response(_MODEL, "msgpack")
    assert response.media_type == MSGPACK_MEDIA_TYPE
    return msgpack.unpackb(response.body, raw=False)


def test_json_branch_media_type_and_decimal_string() -> None:
    """Decimals reach the wire as their exact stored text, not a float."""
    payload = _json_payload()
    assert payload["price"] == "0.4900"


def test_msgpack_branch_media_type_and_decimal_string() -> None:
    """The msgpack branch agrees with json — no ``default=str`` divergence."""
    payload = _msgpack_payload()
    assert payload["price"] == "0.4900"


def test_aware_datetime_is_iso_8601_with_utc_offset_on_both_branches() -> None:
    """Both branches emit a parseable ISO-8601 instant that round-trips to UTC."""
    for payload in (_json_payload(), _msgpack_payload()):
        rendered = payload["observed_at"]
        assert isinstance(rendered, str)
        parsed = datetime.fromisoformat(rendered)
        assert parsed.utcoffset() == UTC.utcoffset(None)
        assert parsed == _MODEL.observed_at


def test_none_survives_as_null_on_both_branches() -> None:
    """An absent optional stays absent rather than becoming the string 'None'."""
    for payload in (_json_payload(), _msgpack_payload()):
        assert payload["settled_at"] is None


def test_branches_carry_identical_payloads() -> None:
    """The format parameter selects an encoding, never a different document."""
    assert _json_payload() == _msgpack_payload()


# --- Response compression (API_GZIP_LEVEL) ---------------------------------


def _request(accept_encoding: str | None) -> Request:
    headers = (
        []
        if accept_encoding is None
        else [(b"accept-encoding", accept_encoding.encode())]
    )
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.mark.parametrize("fmt", ["json", "msgpack"])
def test_send_timeseries_gzips_when_the_client_accepts_it(fmt: ResponseFormat) -> None:
    """The compressed body decodes to exactly the uncompressed encoding."""
    response = asyncio.run(send_timeseries(_request("gzip, deflate"), _MODEL, fmt))
    assert response.headers["content-encoding"] == "gzip"
    assert response.headers["vary"] == "Accept-Encoding"
    assert (
        gzip.decompress(bytes(response.body)) == timeseries_response(_MODEL, fmt).body
    )


@pytest.mark.parametrize("accept_encoding", [None, "identity", "br"])
def test_send_timeseries_sends_plain_without_gzip_in_accept_encoding(
    accept_encoding: str | None,
) -> None:
    response = asyncio.run(send_timeseries(_request(accept_encoding), _MODEL, "json"))
    assert "content-encoding" not in response.headers
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.body == timeseries_response(_MODEL, "json").body
