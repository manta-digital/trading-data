"""Authenticated-mode tests (slice 261, Task 7.2).

The key pair is generated here; no test touches real credentials.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from manta_trading.config import Settings
from manta_trading.data.kalshi.auth import (
    KalshiCredentialError,
    KalshiCredentials,
    load_credentials,
    signing_message,
)
from manta_trading.data.kalshi.client import KalshiClient
from manta_trading.data.kalshi.constants import (
    KALSHI_ACCESS_KEY_HEADER,
    KALSHI_ACCESS_SIGNATURE_HEADER,
    KALSHI_ACCESS_TIMESTAMP_HEADER,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_AUTHENTICATED_RATE_LIMIT,
    KALSHI_PRIVATE_KEY_PATH_ENV,
    KALSHI_PUBLIC_RATE_LIMIT,
)
from manta_trading.data.kalshi.transport import KalshiTransport
from manta_trading.providers.types import RateLimit

KEY_ID = "test-key-id"


def make_settings(**overrides: Any) -> Settings:
    """Settings without ``.env`` (the project's test precedent), typed once."""
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]  # pyright: ignore[reportCallIssue]


@pytest.fixture(scope="module")
def private_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def pem_path(private_key: rsa.RSAPrivateKey, tmp_path: Path) -> Path:
    path = tmp_path / "kalshi.pem"
    path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return path


@pytest.fixture
def credentials(private_key: rsa.RSAPrivateKey) -> KalshiCredentials:
    return KalshiCredentials(key_id=KEY_ID, private_key=private_key)


def verify(private_key: rsa.RSAPrivateKey, signature_b64: str, message: bytes) -> None:
    """Raises ``InvalidSignature`` if the signature does not match."""
    private_key.public_key().verify(
        base64.b64decode(signature_b64),
        message,
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
        ),
        hashes.SHA256(),
    )


class Capture:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"markets": [], "cursor": ""})


class TestSignature:
    def test_verifies_over_exact_signed_string(
        self, credentials: KalshiCredentials, private_key: rsa.RSAPrivateKey
    ):
        signature = credentials.sign(1700000000000, "GET", "/trade-api/v2/markets")
        assert signing_message(1700000000000, "GET", "/trade-api/v2/markets") == (
            b"1700000000000GET/trade-api/v2/markets"
        )
        verify(private_key, signature, b"1700000000000GET/trade-api/v2/markets")

    def test_headers_are_well_formed(
        self, credentials: KalshiCredentials, private_key: rsa.RSAPrivateKey
    ):
        headers = credentials.headers("get", "/trade-api/v2/x", timestamp_ms=42)
        assert set(headers) == {
            KALSHI_ACCESS_KEY_HEADER,
            KALSHI_ACCESS_TIMESTAMP_HEADER,
            KALSHI_ACCESS_SIGNATURE_HEADER,
        }
        assert headers[KALSHI_ACCESS_KEY_HEADER] == KEY_ID
        assert headers[KALSHI_ACCESS_TIMESTAMP_HEADER] == "42"
        # PSS is salted, so signatures differ per call: verify, don't compare.
        verify(
            private_key,
            headers[KALSHI_ACCESS_SIGNATURE_HEADER],
            signing_message(42, "GET", "/trade-api/v2/x"),
        )


class TestRequestSigning:
    async def test_query_string_excluded_from_signed_path(
        self, credentials: KalshiCredentials, private_key: rsa.RSAPrivateKey
    ):
        capture = Capture()
        client = KalshiClient(
            transport=httpx.MockTransport(capture), credentials=credentials
        )
        await client.get_markets(limit=5, min_updated_ts=1)
        request = capture.requests[0]
        assert request.url.query  # the query went on the wire...
        timestamp = int(request.headers[KALSHI_ACCESS_TIMESTAMP_HEADER])
        # ...but the signature covers only the bare path, prefix included.
        verify(
            private_key,
            request.headers[KALSHI_ACCESS_SIGNATURE_HEADER],
            signing_message(timestamp, "GET", "/trade-api/v2/markets"),
        )
        assert request.headers[KALSHI_ACCESS_KEY_HEADER] == KEY_ID

    async def test_public_mode_sends_no_auth_headers(self):
        capture = Capture()
        client = KalshiClient(transport=httpx.MockTransport(capture))
        await client.get_markets()
        headers = capture.requests[0].headers
        for name in (
            KALSHI_ACCESS_KEY_HEADER,
            KALSHI_ACCESS_TIMESTAMP_HEADER,
            KALSHI_ACCESS_SIGNATURE_HEADER,
        ):
            assert name not in headers


class TestModeSelection:
    def test_neither_is_public(self):
        assert load_credentials(None, None) is None

    def test_both_loads_key(self, pem_path: Path):
        creds = load_credentials(KEY_ID, pem_path)
        assert creds is not None
        assert creds.key_id == KEY_ID
        assert isinstance(creds.private_key, rsa.RSAPrivateKey)

    def test_only_key_id_is_error(self):
        with pytest.raises(KalshiCredentialError):
            load_credentials(KEY_ID, None)

    def test_only_path_is_error(self, pem_path: Path):
        with pytest.raises(KalshiCredentialError):
            load_credentials(None, pem_path)

    def test_missing_pem_file_is_error_with_cause(self, tmp_path: Path):
        with pytest.raises(KalshiCredentialError) as info:
            load_credentials(KEY_ID, tmp_path / "absent.pem")
        assert isinstance(info.value.__cause__, OSError)

    def test_garbage_pem_is_error(self, tmp_path: Path):
        bad = tmp_path / "bad.pem"
        bad.write_text("not a key")
        with pytest.raises(KalshiCredentialError):
            load_credentials(KEY_ID, bad)


class TestBudgetSelection:
    def test_authenticated_budget(self, credentials: KalshiCredentials):
        transport = KalshiTransport(credentials=credentials)
        assert transport.mode == "authenticated"
        assert transport.rate_limit is KALSHI_AUTHENTICATED_RATE_LIMIT

    def test_public_budget(self):
        transport = KalshiTransport()
        assert transport.mode == "public"
        assert transport.rate_limit is KALSHI_PUBLIC_RATE_LIMIT

    def test_explicit_budget_wins(self, credentials: KalshiCredentials):
        explicit = RateLimit(requests_per_minute=7)
        assert (
            KalshiTransport(credentials=credentials, rate_limit=explicit).rate_limit
            is explicit
        )


class TestFromSettings:
    @pytest.fixture(autouse=True)
    def clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(KALSHI_API_KEY_ID_ENV, raising=False)
        monkeypatch.delenv(KALSHI_PRIVATE_KEY_PATH_ENV, raising=False)

    def test_env_var_names_match_constants(self):
        prefix = Settings.model_config.get("env_prefix")
        assert f"{prefix}KALSHI_API_KEY_ID" == KALSHI_API_KEY_ID_ENV
        assert f"{prefix}KALSHI_PRIVATE_KEY_PATH" == KALSHI_PRIVATE_KEY_PATH_ENV
        assert "kalshi_api_key_id" in Settings.model_fields
        assert "kalshi_private_key_path" in Settings.model_fields

    def test_authenticated_from_settings(self, pem_path: Path):
        settings = make_settings(
            kalshi_api_key_id=KEY_ID, kalshi_private_key_path=pem_path
        )
        assert KalshiClient.from_settings(settings).mode == "authenticated"

    def test_public_from_settings(self):
        settings = make_settings()
        assert KalshiClient.from_settings(settings).mode == "public"

    def test_partial_pair_from_settings_is_error(self):
        settings = make_settings(kalshi_api_key_id=KEY_ID)
        with pytest.raises(KalshiCredentialError):
            KalshiClient.from_settings(settings)
