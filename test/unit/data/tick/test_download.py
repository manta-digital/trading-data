"""Verified batch download over ``httpx.MockTransport`` (slice 220, TD 9).

Each case asserts what is left on disk: a final name only when size and
SHA-256 match; a ``.partial`` kept only where it can be resumed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from tick_support.batch_responses import JOB_ID, batch_api, file_record
from tick_support.fake_historical import FakeHistorical, server_error

from manta_trading.data.tick.databento.adapter import DatabentoTickProvider
from manta_trading.providers.errors import (
    ProviderAuthError,
    ProviderPermanentError,
    ProviderTransientError,
)

NAME = "glbx-mdp3-20250106.trades.dbn.zst"
CONTENT = bytes(range(256)) * 40  # 10,240 bytes
PARTIAL = NAME + ".partial"

Handler = Callable[[httpx.Request], httpx.Response]


class Server:
    """A MockTransport handler that records requests and serves ``CONTENT``."""

    def __init__(self, respond: Handler | None = None):
        self.requests: list[httpx.Request] = []
        self._respond = respond or self.serve

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._respond(request)

    @staticmethod
    def serve(request: httpx.Request) -> httpx.Response:
        ranged = request.headers.get("Range")
        if ranged is None:
            return httpx.Response(200, content=CONTENT)
        offset = int(ranged.removeprefix("bytes=").removesuffix("-"))
        return httpx.Response(206, content=CONTENT[offset:])


def _download(
    tmp_path: Path, server: Server, content: bytes = CONTENT
) -> tuple[Path, ...]:
    batch = batch_api()
    batch.responses["list_files"] = [file_record(NAME, content)]
    http = httpx.Client(transport=httpx.MockTransport(server))
    provider = DatabentoTickProvider(FakeHistorical(batch=batch), http)  # type: ignore[arg-type]
    with provider:
        return provider.download_batch(JOB_ID, tmp_path)


def _names(tmp_path: Path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir())


def test_full_transfer_leaves_only_the_final_name(tmp_path: Path) -> None:
    server = Server()
    assert _download(tmp_path, server) == (tmp_path / NAME,)
    assert (tmp_path / NAME).read_bytes() == CONTENT
    assert _names(tmp_path) == [NAME]
    assert "Range" not in server.requests[0].headers


def test_interrupted_then_resumed_with_range(tmp_path: Path) -> None:
    def drop_midway(request: httpx.Request) -> httpx.Response:
        def body() -> Iterator[bytes]:
            yield CONTENT[:4000]
            raise httpx.ReadError("connection reset", request=request)

        return httpx.Response(200, content=body())

    with pytest.raises(ProviderTransientError):
        _download(tmp_path, Server(drop_midway))
    assert _names(tmp_path) == [PARTIAL]
    assert (tmp_path / PARTIAL).stat().st_size == 4000

    server = Server()
    _download(tmp_path, server)
    assert server.requests[0].headers["Range"] == "bytes=4000-"
    assert (tmp_path / NAME).read_bytes() == CONTENT
    assert _names(tmp_path) == [NAME]


def test_full_size_partial_is_verified_with_no_request(tmp_path: Path) -> None:
    (tmp_path / PARTIAL).write_bytes(CONTENT)
    server = Server()
    _download(tmp_path, server)
    assert server.requests == []
    assert _names(tmp_path) == [NAME]


def test_oversize_partial_is_deleted_and_restarted(tmp_path: Path) -> None:
    (tmp_path / PARTIAL).write_bytes(CONTENT + b"extra")
    server = Server()
    _download(tmp_path, server)
    assert "Range" not in server.requests[0].headers
    assert (tmp_path / NAME).read_bytes() == CONTENT
    assert _names(tmp_path) == [NAME]


def test_200_to_a_ranged_request_restarts_from_byte_zero(tmp_path: Path) -> None:
    (tmp_path / PARTIAL).write_bytes(b"\xff" * 3000)  # wrong bytes, short
    server = Server(lambda _: httpx.Response(200, content=CONTENT))
    _download(tmp_path, server)
    assert server.requests[0].headers["Range"] == "bytes=3000-"
    assert (tmp_path / NAME).read_bytes() == CONTENT


def test_416_deletes_partial_and_is_transient(tmp_path: Path) -> None:
    (tmp_path / PARTIAL).write_bytes(CONTENT[:100])
    with pytest.raises(ProviderTransientError, match="416"):
        _download(tmp_path, Server(lambda _: httpx.Response(416)))
    assert _names(tmp_path) == []


def test_checksum_mismatch_deletes_partial_and_is_transient(tmp_path: Path) -> None:
    corrupt = bytearray(CONTENT)
    corrupt[5000] ^= 0xFF
    server = Server(lambda _: httpx.Response(200, content=bytes(corrupt)))
    with pytest.raises(ProviderTransientError, match="SHA-256"):
        _download(tmp_path, server)
    assert _names(tmp_path) == []


def test_stalled_response_is_transient_and_keeps_partial(tmp_path: Path) -> None:
    def stall(request: httpx.Request) -> httpx.Response:
        def body() -> Iterator[bytes]:
            yield CONTENT[:2048]
            raise httpx.ReadTimeout("no bytes for the timeout", request=request)

        return httpx.Response(200, content=body())

    with pytest.raises(ProviderTransientError, match="ReadTimeout"):
        _download(tmp_path, Server(stall))
    assert _names(tmp_path) == [PARTIAL]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, ProviderPermanentError),
        (410, ProviderPermanentError),
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (429, ProviderTransientError),
        (503, ProviderTransientError),
    ],
)
def test_status_mapping(tmp_path: Path, status: int, expected: type) -> None:
    with pytest.raises(expected):
        _download(tmp_path, Server(lambda _: httpx.Response(status)))
    assert _names(tmp_path) == []


def test_existing_verified_final_is_returned_without_a_request(tmp_path: Path) -> None:
    (tmp_path / NAME).write_bytes(CONTENT)
    server = Server()
    assert _download(tmp_path, server) == (tmp_path / NAME,)
    assert server.requests == []


def test_existing_mismatched_final_is_refused(tmp_path: Path) -> None:
    (tmp_path / NAME).write_bytes(b"something else")
    with pytest.raises(ProviderPermanentError, match="refusing"):
        _download(tmp_path, Server())
    assert (tmp_path / NAME).read_bytes() == b"something else"


def test_list_files_failure_is_a_free_call_error(tmp_path: Path) -> None:
    batch = batch_api()
    batch.fail("list_files", server_error(502))
    http = httpx.Client(transport=httpx.MockTransport(Server()))
    provider = DatabentoTickProvider(FakeHistorical(batch=batch), http)  # type: ignore[arg-type]
    with pytest.raises(ProviderTransientError):
        provider.download_batch(JOB_ID, tmp_path)


def test_path_like_file_name_is_refused(tmp_path: Path) -> None:
    batch = batch_api()
    batch.responses["list_files"] = [file_record("../escape.dbn.zst", CONTENT)]
    http = httpx.Client(transport=httpx.MockTransport(Server()))
    provider = DatabentoTickProvider(FakeHistorical(batch=batch), http)  # type: ignore[arg-type]
    with pytest.raises(ProviderPermanentError, match="plain file name"):
        provider.download_batch(JOB_ID, tmp_path)


def test_sdk_batch_download_is_never_called(tmp_path: Path) -> None:
    batch = batch_api()
    batch.responses["list_files"] = [file_record(NAME, CONTENT)]
    http = httpx.Client(transport=httpx.MockTransport(Server()))
    DatabentoTickProvider(FakeHistorical(batch=batch), http).download_batch(  # type: ignore[arg-type]
        JOB_ID, tmp_path
    )
    assert [name for name, _ in batch.calls] == ["list_files"]
