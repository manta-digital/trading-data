"""Application-level tests for the serving API: metadata and error contracts.

Route behavior lives in the per-route modules; this module covers what
``create_app`` itself is responsible for.
"""

from __future__ import annotations

from manta_trading.api_server.app import create_app
from manta_trading.version import package_version


def test_openapi_version_comes_from_package_metadata() -> None:
    """Slice 186 D3 — one version in the repo, not a hardcoded literal.

    The pre-186 value was ``"0.1.0"`` while the distribution was at 0.7.3;
    asserting equality with ``package_version()`` is what keeps them married.
    """
    info = create_app().openapi()["info"]
    assert info["version"] == package_version()
    assert info["version"] != "0.1.0"
