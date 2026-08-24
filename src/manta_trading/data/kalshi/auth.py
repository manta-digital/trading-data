"""Optional authenticated mode for the Kalshi client (design 261, TD 4a).

Kalshi API keys are an RSA private key plus a key ID. Each signed request
carries ``KALSHI-ACCESS-KEY``, ``KALSHI-ACCESS-TIMESTAMP`` (milliseconds)
and ``KALSHI-ACCESS-SIGNATURE``: RSA-PSS (SHA-256, MGF1-SHA256,
digest-length salt) over ``timestamp_ms + method + path``, base64-encoded,
with the **query string excluded** from the signed path (Discovery
Findings, Authentication mechanism).

Mode selection is explicit, never a silent fallback: both credentials set →
authenticated; neither → public; exactly one, or an unreadable PEM file →
error at construction.
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from manta_trading.data.kalshi.constants import (
    KALSHI_ACCESS_KEY_HEADER,
    KALSHI_ACCESS_SIGNATURE_HEADER,
    KALSHI_ACCESS_TIMESTAMP_HEADER,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_PRIVATE_KEY_PATH_ENV,
)

_PSS_PADDING = padding.PSS(
    mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH
)
_DIGEST = hashes.SHA256()


class KalshiCredentialError(ValueError):
    """Partial or unusable Kalshi credentials at client construction."""


@dataclass(frozen=True)
class KalshiCredentials:
    """A loaded key pair: the key ID and its RSA private key."""

    key_id: str
    private_key: rsa.RSAPrivateKey

    def sign(self, timestamp_ms: int, method: str, path: str) -> str:
        """Base64 RSA-PSS signature over ``timestamp_ms + method + path``."""
        message = signing_message(timestamp_ms, method, path)
        return base64.b64encode(
            self.private_key.sign(message, _PSS_PADDING, _DIGEST)
        ).decode()

    def headers(
        self, method: str, path: str, *, timestamp_ms: int | None = None
    ) -> dict[str, str]:
        """The three ``KALSHI-ACCESS-*`` headers for one request.

        ``path`` must be the bare URL path (no query string).
        """
        if timestamp_ms is None:
            timestamp_ms = int(time.time() * 1000)
        return {
            KALSHI_ACCESS_KEY_HEADER: self.key_id,
            KALSHI_ACCESS_TIMESTAMP_HEADER: str(timestamp_ms),
            KALSHI_ACCESS_SIGNATURE_HEADER: self.sign(timestamp_ms, method, path),
        }


def signing_message(timestamp_ms: int, method: str, path: str) -> bytes:
    """The exact bytes that get signed — exposed so tests verify against it."""
    return f"{timestamp_ms}{method.upper()}{path}".encode()


def load_credentials(
    key_id: str | None, private_key_path: Path | None
) -> KalshiCredentials | None:
    """Resolve the mode from the configured credential pair.

    Returns ``None`` (public mode) when neither is set, credentials when
    both are, and raises :class:`KalshiCredentialError` for exactly one or
    for a missing/unreadable/non-RSA PEM file. The PEM loads once, here.
    """
    if key_id is None and private_key_path is None:
        return None
    if key_id is None or private_key_path is None:
        raise KalshiCredentialError(
            f"Kalshi credentials are a pair: set both {KALSHI_API_KEY_ID_ENV} and "
            f"{KALSHI_PRIVATE_KEY_PATH_ENV}, or neither for public mode"
        )
    try:
        pem = private_key_path.read_bytes()
    except OSError as exc:
        raise KalshiCredentialError(
            f"cannot read Kalshi private key file {private_key_path}: {exc}"
        ) from exc
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except ValueError as exc:
        raise KalshiCredentialError(
            f"Kalshi private key file {private_key_path} is not a valid PEM key"
        ) from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise KalshiCredentialError(
            f"Kalshi private key file {private_key_path} is not an RSA key"
        )
    return KalshiCredentials(key_id=key_id, private_key=key)
