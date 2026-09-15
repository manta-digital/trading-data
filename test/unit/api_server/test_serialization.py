"""Unit tests for :mod:`manta_trading.api_server.serialization`.

The helper's contract is that ``mode="json"`` normalizes the payload before
either encoder runs, so the json and msgpack branches carry *the same*
scalars — a Decimal as its exact string, an aware datetime as ISO-8601 with a
UTC offset, and None as null (design 188 D7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import msgpack
import orjson
from pydantic import BaseModel

from manta_trading.api_server.serialization import (
    JSON_MEDIA_TYPE,
    MSGPACK_MEDIA_TYPE,
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
