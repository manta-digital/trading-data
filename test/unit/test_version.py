"""Tests for manta_trading.version — the single source of the package version.

Slice 186 D3: the CLI's ``--version`` and the OpenAPI ``info.version`` both read
from here, so a divergence between them is a test failure rather than a support
ticket.
"""

from __future__ import annotations

import importlib.metadata
import importlib.resources
import logging

import pytest

from manta_trading.constants import DISTRIBUTION_NAME
from manta_trading.version import package_version


def test_reports_installed_distribution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "1.2.3" if name == DISTRIBUTION_NAME else "wrong-name",
    )
    assert package_version() == "1.2.3"


def test_falls_back_to_dev_and_warns_on_missing_metadata(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unregistered checkout reports an obvious placeholder and says so.

    Silence here would let an unversioned build masquerade as a release.
    """

    def _raise_not_found(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _raise_not_found)
    with caplog.at_level(logging.WARNING, logger="manta_trading.version"):
        assert package_version() == "dev"

    assert any(
        record.levelname == "WARNING" and DISTRIBUTION_NAME in record.getMessage()
        for record in caplog.records
    )


def test_version_module_is_a_leaf() -> None:
    """No imports from cli or api_server — both consume it, neither owns it."""
    source = (
        importlib.resources.files("manta_trading")
        .joinpath("version.py")
        .read_text(encoding="utf-8")
    )
    assert "manta_trading.cli" not in source
    assert "manta_trading.api_server" not in source
